#!/usr/bin/env python3
"""Injeta redes de fraude (mule networks) com ground truth rastreável.

Cada rede recebe um `ring_id` gravado em `people`, `accounts` e `transactions`.
É isso que garante que a demo sempre encontre algo — a POV nunca depende de
sorte na aleatoriedade do gerador (ver README, seção Resiliência).

Topologia: árvore de fator de ramificação 3. Escolha deliberada — um anel em
cadeia exigiria profundidade 20+ para ser revelado, e um anel em malha completa
apareceria inteiro na profundidade 1. A árvore faz a rede crescer visivelmente
a cada salto, que é exatamente o passo 3 do roteiro de demo.

Os atributos compartilhados seguem a mesma árvore, e nunca o anel inteiro:

- device: cada par pai/filho divide um dispositivo — é a aresta que cria a árvore;
- endereço: dividido dentro de um ramo (pai + seus filhos diretos), não pela rede
  toda — um endereço único para os 20 membros faria a rede virar um clique e
  aparecer inteira na profundidade 1, matando o passo 3 do roteiro;
- chave PIX: só entre os coletores (o líder e seus filhos diretos), que é onde o
  dinheiro converge.

O efeito é que podar um tipo de aresta reduz a rede sem desfazê-la.

Uso:
    python inject_fraud_rings.py --rings 40 --ring-size-min 15 --ring-size-max 30
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from pymongo import UpdateOne

from common import bulk_replace, chunked, det_id, get_db
from reasons import ALL_RING

SEED = 20260826 + 7
NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)
BRANCHING = 2

# Trocas de caractere que imitam erro de digitação real de operador de cadastro.
# Nenhuma troca pode ser identidade: "e" -> "e" produziria um gêmeo com o nome
# exatamente igual, que o Atlas Search resolveria por casamento exato — o passo 6
# do roteiro precisa que só o `fuzzy` alcance o registro.
TYPO_SWAPS = {"a": "á", "e": "é", "i": "l", "o": "0", "s": "z", "n": "m", "r": "rr", "c": "ç"}


def make_typo(name: str, rng: random.Random) -> str:
    """Aplica uma troca de caractere. Nome fica quase idêntico, não idêntico."""
    chars = list(name)
    positions = [i for i, c in enumerate(chars) if c.lower() in TYPO_SWAPS and i > 0]
    if not positions:
        return name + "a"
    i = rng.choice(positions)
    chars[i] = TYPO_SWAPS[chars[i].lower()]
    return "".join(chars)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rings", type=int, default=40)
    p.add_argument("--ring-size-min", type=int, default=15)
    p.add_argument("--ring-size-max", type=int, default=30)
    p.add_argument("--txns-per-edge", type=int, default=3)
    args = p.parse_args()

    rng = random.Random(SEED)
    db = get_db()

    total_people = db.people.estimated_document_count()
    if total_people == 0:
        raise SystemExit("Rode generate_synthetic_data.py antes.")

    # Recruta membros a partir do fim da faixa de seed_index, para não colidir com
    # o intervalo que a demo usa como população "limpa".
    cursor = total_people - 1
    person_updates: list[UpdateOne] = []
    account_updates: list[UpdateOne] = []
    ring_txns: list[dict] = []
    funnel_txns: list[dict] = []
    ring_devices: list[dict] = []
    typo_people: list[UpdateOne] = []
    summary: list[dict] = []

    for r in range(args.rings):
        ring_id = f"ring_{r:03d}"
        size = rng.randint(args.ring_size_min, args.ring_size_max)
        members = [det_id("person", cursor - i) for i in range(size)]
        cursor -= size
        if cursor < size:
            raise SystemExit("População insuficiente para o número de redes pedido.")

        # Coletores: o líder e seus filhos diretos (índices 1..BRANCHING).
        #
        # Eles NÃO dividem uma chave PIX. O DICT (Resolução BCB nº 1/2020) garante
        # que uma chave endereça uma única conta, então "duas pessoas com a mesma
        # chave" não é um estado que exista num banco brasileiro — e um arquiteto
        # de cliente derruba a demo em dez segundos se a vir.
        #
        # O que existe, e é o sinal de verdade: os membros do anel **pagam para a
        # mesma chave de destino**, a da conta de arrecadação do coletor. É a
        # assinatura do funil de mulas, e é o que vira `same_pix_counterparty`.
        collectors = [idx for idx in range(0, min(BRANCHING + 1, size))]  # noqa: F841 (documenta a topologia)

        for idx, pid in enumerate(members):
            # Ramo = o pai direto. O nó 0 forma ramo com seus filhos.
            branch = 0 if idx == 0 else (idx - 1) // BRANCHING
            branch_address = {
                "address_id": det_id("addr", "ring", ring_id, branch),
                "street": f"Rua da Rede {r}, bloco {branch}",
                "city": "Sao Paulo",
                "zip": f"9{r:04d}-{branch:03d}",
            }
            person_updates.append(
                UpdateOne(
                    {"_id": pid},
                    {
                        "$set": {
                            "ring_id": ring_id,
                            "risk_flags": ["mule_network_candidate"],
                            "addresses": [branch_address],
                        }
                    },
                )
            )
            account_updates.append(
                UpdateOne(
                    {"person_id": pid},
                    {"$set": {"ring_id": ring_id, "status": "active"}},
                )
            )

        # Árvore: filho i tem pai (i-1)//BRANCHING. O par divide um device.
        edges: list[tuple[str, str, str]] = []
        for i in range(1, size):
            parent = members[(i - 1) // BRANCHING]
            child = members[i]
            device_id = det_id("device", "ring", ring_id, i)
            ring_devices.append(
                {
                    "_id": device_id,
                    "device_type": "mobile",
                    "is_hub": False,
                    "ring_id": ring_id,
                    "first_seen": NOW - timedelta(days=rng.randint(20, 200)),
                }
            )
            edges.append((parent, child, device_id))

        # Transações internas da rede, feitas no dispositivo compartilhado do par.
        # Alternam de sentido: as duas contas precisam aparecer como `from_account`
        # no mesmo device_id, senão a materialização de `shares_device` (que só olha
        # quem originou) não enxerga o vínculo.
        for e_idx, (parent, child, device_id) in enumerate(edges):
            for t in range(max(2, args.txns_per_edge)):
                invertido = t % 2 == 1
                ring_txns.append(
                    {
                        "_id": det_id("txn", "ring", ring_id, e_idx, t),
                        "_invertido": invertido,
                        "from_account": det_id("account", None, None),  # substituído abaixo
                        "to_account": None,
                        "device_id": device_id,
                        "amount": round(rng.uniform(1800, 4900), 2),
                        "reason_text": rng.choice(ALL_RING),
                        "timestamp": NOW - timedelta(seconds=rng.randint(0, 90 * 86400)),
                        "ring_id": ring_id,
                        "_parent": parent,
                        "_child": child,
                    }
                )

        # Funil hierárquico: cada membro paga para o **pai** na árvore, que é o
        # coletor do seu ramo.
        #
        # A versão anterior espalhava os pagamentos entre cinco coletores fixos, e
        # o efeito na topologia foi ruim: os pagadores de uma mesma chave viram
        # clique na materialização, e cliques de seis a sete membros derrubaram o
        # diâmetro do anel. Entrar por qualquer nó trazia 22 dos 30 já no primeiro
        # salto — a revelação por profundidade, que é a espinha da demonstração,
        # deixava de existir.
        #
        # Pagando o pai, os pagadores de uma chave são exatamente os **irmãos**
        # daquele ramo: grupos de quatro, e a aresta PIX passa a ligar irmão com
        # irmão. Ela é complementar à de dispositivo (que liga pai e filho) em vez
        # de redundante, e a árvore continua profunda.
        for idx, pid in enumerate(members):
            if idx == 0:
                continue
            coletor = members[(idx - 1) // BRANCHING]
            if coletor == pid:
                continue
            for t in range(2):
                funnel_txns.append(
                    {
                        "_id": det_id("txn", "funnel", ring_id, idx, t),
                        "from_account": None,
                        "to_account": None,
                        "device_id": det_id("device", "ring", ring_id, max(idx, 1)),
                        "amount": round(rng.uniform(900, 4800), 2),
                        "reason_text": rng.choice(ALL_RING),
                        "timestamp": NOW - timedelta(seconds=rng.randint(0, 60 * 86400)),
                        "ring_id": ring_id,
                        "_payer": pid,
                        "_collector": coletor,
                    }
                )

        # Gêmeo com erro de digitação: pessoa FORA da rede, nome quase idêntico ao
        # do líder. Nenhuma aresta a liga à rede — só o Atlas Search a encontra.
        leader = db.people.find_one({"_id": members[0]}, {"name": 1})
        # `cursor` já aponta para o primeiro índice livre abaixo do anel. Usar
        # `cursor - 1` deixaria `cursor` livre para o anel seguinte recrutar como
        # líder — e o gêmeo sobrescreveria o nome desse líder, quebrando a
        # correspondência typo/real que o passo 6 do roteiro depende.
        twin_seed = cursor
        cursor -= 1
        typo_people.append(
            UpdateOne(
                {"_id": det_id("person", twin_seed)},
                {
                    "$set": {
                        "name": make_typo(leader["name"], rng),
                        "near_duplicate_of": members[0],
                        "near_duplicate_ring": ring_id,
                    }
                },
            )
        )

        summary.append({"ring_id": ring_id, "size": size, "leader": members[0], "edges": len(edges)})

    print(f"marcando {len(person_updates):,} pessoas e suas contas em {args.rings} redes")
    for batch in chunked(person_updates):
        db.people.bulk_write(batch, ordered=False)
    for batch in chunked(account_updates):
        db.accounts.bulk_write(batch, ordered=False)
    for batch in chunked(typo_people):
        db.people.bulk_write(batch, ordered=False)

    bulk_replace(db.devices, ring_devices, "devices da rede")

    # Resolve as contas reais dos membros só agora (uma consulta, não N).
    member_ids = (
        {t["_parent"] for t in ring_txns}
        | {t["_child"] for t in ring_txns}
        | {t["_payer"] for t in funnel_txns}
        | {t["_collector"] for t in funnel_txns}
    )
    acct_by_person: dict[str, str] = {}
    pix_by_account: dict[str, str] = {}
    for a in db.accounts.find(
        {"person_id": {"$in": list(member_ids)}}, {"person_id": 1, "pix_key": 1}
    ):
        acct_by_person.setdefault(a["person_id"], a["_id"])
        if a.get("pix_key"):
            pix_by_account[a["_id"]] = a["pix_key"]

    final_txns = []
    for t in ring_txns:
        parent, child = t.pop("_parent"), t.pop("_child")
        invertido = t.pop("_invertido")
        if parent in acct_by_person and child in acct_by_person:
            origem, destino = (parent, child) if invertido else (child, parent)
            t["from_account"] = acct_by_person[origem]
            t["to_account"] = acct_by_person[destino]
            t["to_pix_key"] = pix_by_account.get(t["to_account"])
            final_txns.append(t)

    for t in funnel_txns:
        pagador, coletor = t.pop("_payer"), t.pop("_collector")
        if pagador in acct_by_person and coletor in acct_by_person:
            t["from_account"] = acct_by_person[pagador]
            t["to_account"] = acct_by_person[coletor]
            t["to_pix_key"] = pix_by_account.get(t["to_account"])
            if t["to_pix_key"]:
                final_txns.append(t)

    bulk_replace(db.transactions, final_txns, "transações da rede")

    db.rings.drop()
    db.rings.insert_many(summary)
    print(f"\nground truth em `rings`: {len(summary)} redes")
    print(f"exemplo para a demo: {summary[0]['ring_id']} (líder {summary[0]['leader']}, {summary[0]['size']} membros)")


if __name__ == "__main__":
    main()
