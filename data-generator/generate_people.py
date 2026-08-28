#!/usr/bin/env python3
"""Gera a população de pessoas físicas — os sócios da cadeia societária.

Cada pessoa carrega um perfil (ocupação, faixa de renda, faixa etária, canal de
abertura) derivado deterministicamente do `seed_index` em `profiles.py`. O perfil
não entra em nenhuma query do traversal: ele existe para o analista **ler** o
sócio depois de a cadeia tê-lo alcançado.

Isso importa numa decisão de crédito. Um sócio-administrador de 58 anos,
empresário, com renda declarada alta é uma leitura; um estudante de 22 anos sem
renda declarada figurando como sócio-administrador de quatro empresas em grupos
diferentes é outra. O grafo encontra os dois igual — quem distingue é a tela.

Determinismo: semente fixa, `_id` por `det_id`, e reexecutar reescreve os mesmos
documentos em vez de duplicar.

Uso:
    python generate_people.py --people 150000
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from faker import Faker

from common import bulk_insert, bulk_replace, det_id, get_db
from profiles import perfil

SEED = 20260826
NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)

# Domicílios de 2 a 3 pessoas. O compartilhamento de endereço é **estruturado**,
# não sorteado: pessoas aleatórias dividindo endereço criariam um componente
# conexo artificial que o traversal atravessaria sem significar nada.
HOUSEHOLD_RATE = 0.18


def gen_people(fake: Faker, rng: random.Random, n: int):
    i = 0
    while i < n:
        grupo = rng.choices([1, 2, 3], weights=[82, 12, 6])[0] if rng.random() < HOUSEHOLD_RATE else 1
        grupo = min(grupo, n - i)
        for k in range(grupo):
            idx = i + k
            yield {
                "_id": det_id("person", idx),
                "name": fake.name(),
                "document_id": f"{idx:011d}",
                **perfil(idx),
                "seed_index": idx,
                "created_at": NOW - timedelta(days=rng.randint(0, 3 * 365)),
            }
        i += grupo


def main() -> None:
    p = argparse.ArgumentParser()
    # 800 mil, não 150 mil. Com 1,2 M de empresas e ~2,2 sócios cada, uma população
    # de 150 mil colocava a mesma pessoa em ~17 empresas — "sócio em comum" deixava
    # de ser exceção e virava propriedade de todo par de empresas da base.
    p.add_argument("--people", type=int, default=800_000)
    p.add_argument("--drop", action="store_true")
    args = p.parse_args()

    rng = random.Random(SEED)
    fake = Faker("pt_BR")
    Faker.seed(SEED)
    db = get_db()

    if args.drop:
        db.people.drop()
        print("people apagada")

    limpo = db.people.estimated_document_count() == 0
    escrever = bulk_insert if limpo else bulk_replace
    print(f"people ({args.people:,}) —", "insert_many (carga limpa)" if limpo else "upsert (reexecução)")
    escrever(db.people, gen_people(fake, rng, args.people), "people")

    # Só `seed_index`. Os índices de perfil (`occupation`, `income_band`,
    # `age_band`, `onboarding_channel`) vinham do modelo anterior desta POV, em que
    # o atributo compartilhado era a aresta. Aqui esses campos são **projeção** no
    # painel do nó, nunca filtro nem ordenação — índice sobre eles é peso morto na
    # escrita e uma linha a explicar em qualquer revisão de schema.
    db.people.create_index("seed_index")
    print(f"\npeople {db.people.estimated_document_count():,}")


if __name__ == "__main__":
    main()
