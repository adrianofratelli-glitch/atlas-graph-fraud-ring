#!/usr/bin/env python3
"""Gera a população sintética de base: pessoas, contas, dispositivos e transações.

Idempotente: todo `_id` deriva de `det_id(...)` sobre os atributos-chave, então
rodar de novo com os mesmos parâmetros reescreve os mesmos documentos.

Nada aqui é dado real. CPFs são numéricos válidos em formato, mas gerados a
partir do índice do registro — não correspondem a pessoas.

Uso:
    python generate_synthetic_data.py --people 150000 --transactions 600000
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from faker import Faker

from common import bulk_replace, det_id, get_db
from reasons import ALL_LEGIT

SEED = 20260826
NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)
WINDOW_DAYS = 180

ACCOUNT_TYPES = ["checking", "credit_card", "savings"]

# Hubs deliberados. Um endereço de agência bancária compartilhado por milhares de
# contas legítimas é o falso positivo clássico que a poda por fan-out resolve —
# ver LIMITATIONS.md seção 4. Sem hub no dataset, a demo de poda não prova nada.
HUB_ADDRESS_COUNT = 3
HUB_DEVICE_COUNT = 5
HUB_SHARE_RATE = 0.06  # fração da população/tráfego que cai num hub

# Compartilhamento legítimo. Ele precisa existir — senão o grafo limpo fica com
# zero arestas e a rede de fraude aparece por ausência de contexto, não por
# vínculo. Mas precisa ser *estruturado* (domicílio, casal), não sorteio: pessoas
# aleatórias dividindo endereço criariam um componente conexo artificial que
# faria qualquer traversal de profundidade 4 varrer a base inteira.
HOUSEHOLD_SIZE = 3         # pessoas por domicílio compartilhado
HOUSEHOLD_RATE = 0.18      # fração da população que vive em domicílio compartilhado
DEVICE_SHARE_RATE = 0.04   # transações feitas no dispositivo de outra conta


def gen_people(fake: Faker, rng: random.Random, n: int, hub_addresses: list[dict]):
    """Endereço por domicílio: pessoas consecutivas dentro de um domicílio dividem
    o mesmo `address_id`. O resto tem endereço próprio."""
    i = 0
    while i < n:
        if rng.random() < HOUSEHOLD_RATE:
            group = min(rng.randint(2, HOUSEHOLD_SIZE), n - i)
        else:
            group = 1
        if rng.random() < HUB_SHARE_RATE:
            addr = dict(rng.choice(hub_addresses))
            addr.pop("is_hub", None)
        else:
            addr = {
                "address_id": det_id("addr", i),
                "street": fake.street_address(),
                "city": fake.city(),
                "zip": fake.postcode(),
            }
        for k in range(group):
            idx = i + k
            yield {
                "_id": det_id("person", idx),
                "name": fake.name(),
                "document_id": f"{idx:011d}",
                "phones": [f"11{rng.randint(900000000, 999999999)}"],
                "addresses": [addr],
                "risk_flags": [],
                "ring_id": None,
                "seed_index": idx,
                "created_at": NOW - timedelta(days=rng.randint(0, 3 * 365)),
            }
        i += group


def build_hub_addresses() -> list[dict]:
    return [
        {
            "address_id": det_id("addr", "hub", i),
            "street": f"Avenida Central {1000 + i}",
            "city": ["Sao Paulo", "Rio de Janeiro", "Belo Horizonte"][i % 3],
            "zip": f"0{1000 + i}-000",
            "is_hub": True,
        }
        for i in range(HUB_ADDRESS_COUNT)
    ]


# --- Chave PIX -------------------------------------------------------------
#
# Regra do DICT (Resolução BCB nº 1/2020): **uma chave endereça exatamente uma
# conta transacional por vez**. Duas contas não podem carregar a mesma chave — o
# DICT rejeita o cadastro. Por isso a chave mora em `accounts`, é única, e
# `shares_pix_key` (duas pessoas com a mesma chave) não existe neste modelo.
#
# O vínculo real de fraude é o outro lado: **contas distintas que pagam para a
# MESMA chave de destino**. É a assinatura do funil de mulas — a conta de
# arrecadação no fim da cadeia. Daí a aresta `same_pix_counterparty`, construída
# a partir de `transactions.to_pix_key`.
#
# Tipos de chave e o limite de cada um:
#   CPF   — uma só por pessoa, numa única conta. Aqui, sempre a conta k=0.
#   EVP   — chave aleatória, até 5 por conta PF. Aqui, uma por conta k>0.
def pix_key_for(person_idx: int, k: int) -> str:
    """Chave PIX de uma conta. Única por conta, como o DICT exige."""
    if k == 0:
        return f"{person_idx:011d}"  # chave CPF, uma por pessoa
    return det_id("pix", person_idx, k)  # chave aleatória (EVP)


def pix_key_type(k: int) -> str:
    return "cpf" if k == 0 else "evp"


def gen_devices(rng: random.Random, account_keys: list[tuple[int, int]]):
    """Um dispositivo por conta, mais os hubs (terminal público, quiosque)."""
    for i in range(HUB_DEVICE_COUNT):
        yield {
            "_id": det_id("device", "hub", i),
            "device_type": "web",
            "is_hub": True,
            "first_seen": NOW - timedelta(days=900),
        }
    for (pi, k) in account_keys:
        yield {
            "_id": det_id("device", pi, k),
            "device_type": rng.choice(["mobile", "mobile", "web"]),
            "is_hub": False,
            "first_seen": NOW - timedelta(days=rng.randint(0, 900)),
        }


def gen_accounts(rng: random.Random, n_people: int):
    """1 a 3 contas por pessoa, ~1.6 em média."""
    for i in range(n_people):
        person_id = det_id("person", i)
        for k in range(rng.choices([1, 2, 3], weights=[55, 30, 15])[0]):
            yield {
                "_id": det_id("account", i, k),
                "person_id": person_id,
                "account_type": ACCOUNT_TYPES[k % len(ACCOUNT_TYPES)],
                # Chave única por conta: é o que o DICT garante e o que impede a
                # aresta "duas pessoas, mesma chave" de existir.
                "pix_key": pix_key_for(i, k),
                "pix_key_type": pix_key_type(k),
                "opened_at": NOW - timedelta(days=rng.randint(30, 3 * 365)),
                "status": "active",
                "ring_id": None,
                "owner_seed_index": i,
                "_key": [i, k],
            }


def gen_transactions(
    rng: random.Random,
    n: int,
    account_ids: list[str],
    account_keys: list[tuple[int, int]],
    hub_device_ids: list[str],
):
    """O dispositivo é, por padrão, o da conta de origem. É isso que faz
    `shares_device` significar alguma coisa: quando duas contas aparecem no mesmo
    dispositivo, foi porque alguém realmente operou as duas."""
    n_acc = len(account_ids)
    for i in range(n):
        a = rng.randrange(n_acc)
        b = rng.randrange(n_acc)
        if a == b:
            b = (b + 1) % n_acc
        roll = rng.random()
        if roll < HUB_SHARE_RATE:
            device = rng.choice(hub_device_ids)
        elif roll < HUB_SHARE_RATE + DEVICE_SHARE_RATE:
            # dispositivo de uma conta vizinha: compartilhamento legítimo (casal,
            # familiar), que o traversal vai enxergar como aresta de 1 salto.
            j = (a + rng.randint(1, 3)) % n_acc
            device = det_id("device", *account_keys[j])
        else:
            device = det_id("device", *account_keys[a])
        yield {
            "_id": det_id("txn", i),
            "from_account": account_ids[a],
            "to_account": account_ids[b],
            # Chave de destino: é sobre ela que `same_pix_counterparty` agrupa.
            "to_pix_key": pix_key_for(*account_keys[b]),
            "device_id": device,
            "amount": round(rng.lognormvariate(5.2, 1.1), 2),
            "reason_text": rng.choice(ALL_LEGIT),
            "timestamp": NOW - timedelta(seconds=rng.randint(0, WINDOW_DAYS * 86400)),
            "ring_id": None,
        }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--people", type=int, default=150_000)
    p.add_argument("--transactions", type=int, default=600_000)
    p.add_argument("--drop", action="store_true", help="apaga as coleções antes (não é o padrão)")
    p.add_argument(
        "--only",
        choices=["people", "accounts", "devices", "transactions"],
        help="regenera só uma coleção (idempotente: reescreve os mesmos _id)",
    )
    args = p.parse_args()

    rng = random.Random(SEED)
    fake = Faker("pt_BR")
    Faker.seed(SEED)

    db = get_db()
    if args.drop:
        for c in ["people", "accounts", "devices", "transactions", "connections", "rings"]:
            db[c].drop()
        print("coleções apagadas")

    print(f"people ({args.people:,})")
    bulk_replace(db.people, gen_people(fake, rng, args.people, build_hub_addresses()), "people")
    if args.only == "people":
        print("\n--only people: parando aqui.")
        return

    print("accounts")
    account_ids: list[str] = []
    account_keys: list[tuple[int, int]] = []

    def accounts_tap():
        for doc in gen_accounts(rng, args.people):
            account_ids.append(doc["_id"])
            account_keys.append(tuple(doc.pop("_key")))
            yield doc

    bulk_replace(db.accounts, accounts_tap(), "accounts")

    print(f"devices ({len(account_keys):,} + {HUB_DEVICE_COUNT} hubs)")
    bulk_replace(db.devices, gen_devices(rng, account_keys), "devices")

    hub_device_ids = [det_id("device", "hub", i) for i in range(HUB_DEVICE_COUNT)]
    print(f"transactions ({args.transactions:,})")
    bulk_replace(
        db.transactions,
        gen_transactions(rng, args.transactions, account_ids, account_keys, hub_device_ids),
        "transactions",
    )

    print("\nresumo:")
    for c in ["people", "accounts", "devices", "transactions"]:
        print(f"  {c:14s} {db[c].estimated_document_count():>10,}")


if __name__ == "__main__":
    main()
