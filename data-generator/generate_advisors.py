#!/usr/bin/env python3
"""Hierarquia comercial: superintendente → gerentes → assessores → carteira.

## A pergunta que esta base responde

"Quais contas **este** usuário pode ver?" Um gerente enxerga a carteira de todos
os assessores abaixo dele; um assessor enxerga só os clientes dele. É o mesmo
`$graphLookup` da cadeia societária aplicado a controle de acesso — árvore ainda
mais rasa (3 níveis), consulta pontual por chave de negócio, e o escopo decidido
no servidor.

Vale a POV porque é o padrão que aparece em quase toda mesa de investimento e
private banking, e porque a alternativa comum — materializar a lista de contas
visíveis por usuário — desatualiza a cada troca de carteira. Aqui a hierarquia é
a fonte da verdade e a visibilidade é derivada dela na hora da consulta.

## Modelagem: árvore auto-referente, sem coleção de arestas

`advisors.reports_to` aponta para o superior. Não existe coleção de arestas
separada porque a relação é **funcional** — cada pessoa tem no máximo um chefe —
e materializar isso numa coleção de arestas só acrescentaria uma junção. A
cadeia societária precisa da coleção separada porque lá a relação é N:N e carrega
atributo próprio (percentual, qualificação).

O vínculo com a carteira é `companies.advisor_id`: a empresa cliente aponta para
o assessor que a atende.

Uso:
    python generate_advisors.py
    python generate_advisors.py --managers 60 --advisors 900
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timezone

from faker import Faker
from pymongo import UpdateOne

from common import bulk_replace, chunked, det_id, get_db

SEED = 20260827
NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)

REGIOES = ["Sudeste", "Sul", "Nordeste", "Centro-Oeste", "Norte"]
SEGMENTOS = ["Corporate", "Middle", "Agro", "Private"]


def gen_hierarquia(rng: random.Random, fake: Faker, n_regionais: int,
                   n_gerentes: int, n_assessores: int):
    """Três níveis abaixo do topo, que é a forma real de uma mesa comercial."""
    topo = det_id("advisor", "head")
    yield {
        "_id": topo, "nome": fake.name(), "papel": "superintendente",
        "reports_to": None, "nivel": 0, "matricula": "SUP0001",
        "regiao": "Brasil", "segmento": "Todos", "desde": NOW,
    }

    regionais = []
    for r in range(n_regionais):
        rid = det_id("advisor", "regional", r)
        regionais.append(rid)
        yield {
            "_id": rid, "nome": fake.name(), "papel": "regional",
            "reports_to": topo, "nivel": 1, "matricula": f"REG{r:04d}",
            "regiao": REGIOES[r % len(REGIOES)],
            "segmento": SEGMENTOS[r % len(SEGMENTOS)], "desde": NOW,
        }

    gerentes = []
    for g in range(n_gerentes):
        gid = det_id("advisor", "gerente", g)
        pai = regionais[g % len(regionais)]
        gerentes.append(gid)
        yield {
            "_id": gid, "nome": fake.name(), "papel": "gerente",
            "reports_to": pai, "nivel": 2, "matricula": f"GER{g:04d}",
            "regiao": REGIOES[g % len(REGIOES)],
            "segmento": SEGMENTOS[g % len(SEGMENTOS)], "desde": NOW,
        }

    for a in range(n_assessores):
        yield {
            "_id": det_id("advisor", "assessor", a), "nome": fake.name(),
            "papel": "assessor", "reports_to": gerentes[a % len(gerentes)],
            "nivel": 3, "matricula": f"ASS{a:05d}",
            "regiao": REGIOES[a % len(REGIOES)],
            "segmento": SEGMENTOS[a % len(SEGMENTOS)], "desde": NOW,
        }


def atribui_carteira(db, n_assessores: int, grupos_vitrine: list[dict]) -> int:
    """Liga cada empresa a um assessor.

    Duas regras, e as duas existem por causa da leitura da tela:

    1. **A carteira concentra.** O grosso da base é distribuído em blocos de 40
       empresas consecutivas por assessor, não empresa a empresa. Um round-robin
       por `seed_index` espalhava um grupo econômico de 25 empresas por 25
       assessores diferentes — o que não é carteira, é sorteio, e destrói a
       pergunta "quem responde por este grupo?".
    2. **O grupo de vitrine é fatiado para produzir uma escada.** Três assessores
       escolhidos pela posição deles na árvore, não por sorteio:

       - dois sob o **mesmo gerente** (índices 0 e 60: `gerentes[a % 60]` dá o
         mesmo gerente para os dois);
       - um sob **outro gerente do mesmo regional** (índice 8: gerente 8, e
         `regionais[g % 8]` põe os gerentes 0 e 8 sob o regional 0).

       O resultado é o que a demo precisa mostrar: o assessor vê ~1/3 do grupo, o
       gerente vê ~2/3, o regional vê o grupo inteiro. Sem essa escolha, os três
       assessores caem em ramos distintos e **todo mundo vê o mesmo terço** — a
       hierarquia deixa de significar coisa alguma na tela.
    """
    BLOCO = 40
    ops = []
    for lote in chunked(db.companies.find({}, {"_id": 1, "seed_index": 1}), 5000):
        for c in lote:
            i = c.get("seed_index", 0)
            ops.append(UpdateOne(
                {"_id": c["_id"]},
                {"$set": {"advisor_id": det_id("advisor", "assessor",
                                               (i // BLOCO) % n_assessores)}},
            ))
        db.companies.bulk_write(ops, ordered=False)
        ops = []

    # Fatiamento deliberado dos grupos de vitrine.
    tocados = 0
    for g_idx, g in enumerate(grupos_vitrine):
        trio = [0, 60, 8]
        ops = [
            UpdateOne(
                {"_id": cid},
                {"$set": {"advisor_id": det_id("advisor", "assessor", trio[k % len(trio)])}},
            )
            for k, cid in enumerate(g["member_ids"])
        ]
        if ops:
            db.companies.bulk_write(ops, ordered=False)
            tocados += len(ops)
        if g_idx >= 4:  # os cinco primeiros bastam para a demo
            break
    return tocados


def propaga_para_exposicao(db) -> int:
    """Copia `advisor_id` da empresa para a exposição de crédito dela.

    Denormalização deliberada, e vale explicar o porquê porque contraria o
    instinto de normalizar: a carteira consolidada de um gerente somava a
    exposição percorrendo `companies` e fazendo `$lookup` em `credit_exposure`
    documento a documento. Para um regional isso são 160 mil empresas e ~13 s.

    Com `advisor_id` na própria exposição, a mesma resposta é um `$match` por
    índice sobre as ~380 mil exposições que existem, sem junção por documento.
    O campo é derivado e o gerador é a fonte — se a carteira mudar, roda de novo.
    """
    total = 0
    for lote in chunked(db.companies.find({"advisor_id": {"$exists": True}},
                                          {"_id": 1, "advisor_id": 1}), 5000):
        ops = [UpdateOne({"company_id": c["_id"]}, {"$set": {"advisor_id": c["advisor_id"]}})
               for c in lote]
        res = db.credit_exposure.bulk_write(ops, ordered=False)
        total += res.modified_count
    return total


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--regionais", type=int, default=8)
    p.add_argument("--managers", type=int, default=60)
    p.add_argument("--advisors", type=int, default=900)
    p.add_argument("--drop", action="store_true")
    args = p.parse_args()

    rng = random.Random(SEED)
    fake = Faker("pt_BR")
    Faker.seed(SEED)
    db = get_db()

    if args.drop:
        db.advisors.drop()

    n = bulk_replace(
        db.advisors,
        gen_hierarquia(rng, fake, args.regionais, args.managers, args.advisors),
        "advisors",
    )
    print(f"hierarquia: {n:,} pessoas (1 + {args.regionais} + {args.managers} + {args.advisors})")

    grupos = list(db.economic_groups.find({"showcase": True}).limit(5))
    tocados = atribui_carteira(db, args.advisors, grupos)
    print(f"carteira atribuída; grupos de vitrine fatiados entre 3 assessores "
          f"de 2 gerentes ({tocados} empresas)")

    n_exp = propaga_para_exposicao(db)
    print(f"advisor_id propagado para {n_exp:,} exposições de crédito")

    for chave, opts, porque in (
        ("reports_to", {}, "connectToField do traversal de subordinados"),
        ("papel", {}, "seleção de gerente/assessor na demo"),
        ("matricula", {"unique": True}, "login por matrícula"),
    ):
        print("  advisors." + db.advisors.create_index(chave, **opts) + f" — {porque}")
    print("  companies." + db.companies.create_index("advisor_id") + " — carteira do assessor")
    print("  credit_exposure." + db.credit_exposure.create_index("advisor_id")
          + " — soma da carteira sem junção por documento")


if __name__ == "__main__":
    main()
