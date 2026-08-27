#!/usr/bin/env python3
"""Materializa atributos compartilhados (Padrão A) como arestas explícitas (Padrão B).

É o job recomendado em `schema/collections.md`: o traversal da investigação roda
sobre `connections`, e `connections` é populada a partir dos atributos que já
existem no dado operacional.

A regra que importa: um atributo só vira aresta se seu fan-out estiver abaixo de
`HUB_FANOUT_THRESHOLD`. Um dispositivo usado por 800 contas, ou o endereço de uma
agência bancária, não é evidência de vínculo — é ruído de hub, e materializá-lo
transformaria metade da base em um componente conexo só. Ver LIMITATIONS.md §4.

As arestas são gravadas nos dois sentidos: `$graphLookup` segue
`connectFromField` -> `connectToField` numa direção só, e vínculo por atributo
compartilhado é, por natureza, simétrico.

Uso:
    python materialize_connections.py --hub-threshold 50
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from itertools import combinations

from pymongo import ASCENDING

from common import bulk_replace, det_id, get_db

NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)

# Um grupo com muitos membros geraria N*(N-1)/2 arestas. Mesmo abaixo do limiar de
# hub, o custo é quadrático, então o teto de pares por grupo é explícito.
MAX_PAIRS_PER_GROUP = 400

# Quantas vezes um pagador precisa ter pago à mesma chave para o destino contar
# como vínculo. Um pagamento avulso a um lojista é compra, não cumplicidade.
MIN_PIX_HITS = 2


def edges_from_groups(groups, edge_type: str, threshold: int):
    """Cada grupo é (chave, [ids de pessoas]). Emite arestas nos dois sentidos."""
    skipped_hubs = 0
    for key, members in groups:
        members = sorted(set(members))
        if len(members) < 2:
            continue
        if len(members) > threshold:
            skipped_hubs += 1
            continue
        pairs = list(combinations(members, 2))[:MAX_PAIRS_PER_GROUP]
        for a, b in pairs:
            weight = len(members)
            for src, dst in ((a, b), (b, a)):
                yield {
                    "_id": det_id("conn", edge_type, src, dst),
                    "from": src,
                    "to": dst,
                    "type": edge_type,
                    "shared_key": str(key),
                    "weight": weight,
                    "created_at": NOW,
                }
    if skipped_hubs:
        print(f"  {edge_type}: mais {skipped_hubs} grupos descartados por fan-out")


def device_groups(db, threshold: int):
    """device_id -> pessoas que *originaram* transações naquele dispositivo.

    Só `from_account`, deliberadamente. Incluir `to_account` transformaria "A pagou
    B do celular de A" em aresta de dispositivo compartilhado — o que não é vínculo
    nenhum, é só uma transação. Com `to_account` incluso o grafo legítimo explode
    (medido: grau médio 8.6 em vez de ~1, e profundidade 2 varrendo metade da base).
    Dispositivo compartilhado significa duas contas operadas do mesmo aparelho."""
    pipeline = [
        {"$group": {"_id": "$device_id", "accounts": {"$addToSet": "$from_account"}}},
        {"$match": {"$expr": {"$lte": [{"$size": "$accounts"}, threshold]}}},
        {"$unwind": "$accounts"},
        {"$lookup": {"from": "accounts", "localField": "accounts", "foreignField": "_id", "as": "a"}},
        {"$unwind": "$a"},
        {"$group": {"_id": "$_id", "people": {"$addToSet": "$a.person_id"}}},
        {"$match": {"$expr": {"$gte": [{"$size": "$people"}, 2]}}},
    ]
    for doc in db.transactions.aggregate(pipeline, allowDiskUse=True):
        yield doc["_id"], doc["people"]


def address_groups(db, threshold: int):
    pipeline = [
        {"$unwind": "$addresses"},
        {"$group": {"_id": "$addresses.address_id", "people": {"$addToSet": "$_id"}}},
        {"$match": {"$expr": {"$and": [
            {"$gte": [{"$size": "$people"}, 2]},
            {"$lte": [{"$size": "$people"}, threshold]},
        ]}}},
    ]
    for doc in db.people.aggregate(pipeline, allowDiskUse=True):
        yield doc["_id"], doc["people"]


def pix_counterparty_groups(db, threshold: int):
    """chave PIX de destino -> pessoas que *pagaram* para aquela chave.

    Não é "duas pessoas com a mesma chave": isso não existe. O DICT (Resolução
    BCB nº 1/2020) garante que uma chave endereça uma única conta transacional
    por vez, e rejeita o cadastro duplicado. Modelar assim era um erro factual
    que qualquer arquiteto de banco derruba na primeira pergunta.

    O vínculo real é o **destino em comum**: contas sem relação nenhuma entre si
    que pingam repetidamente na mesma chave. É a conta de arrecadação no fim do
    funil de mulas, e é o padrão que os times de PLD caçam de verdade.

    Duas guardas contra ruído legítimo:
      - o pagador precisa ter pago àquela chave **mais de uma vez** (`MIN_PIX_HITS`):
        um pagamento único para um lojista não é vínculo, é compra;
      - a chave que recebe de mais de `threshold` pessoas é hub (lojista, PSP,
        concessionária) e não vira aresta — mesma regra de fan-out dos demais tipos.
    """
    pipeline = [
        {"$match": {"to_pix_key": {"$ne": None}}},
        # Pagador distinto por chave, com quantas vezes pagou.
        {"$group": {
            "_id": {"chave": "$to_pix_key", "conta": "$from_account"},
            "vezes": {"$sum": 1},
        }},
        {"$match": {"vezes": {"$gte": MIN_PIX_HITS}}},
        {"$group": {"_id": "$_id.chave", "accounts": {"$addToSet": "$_id.conta"}}},
        {"$match": {"$expr": {"$and": [
            {"$gte": [{"$size": "$accounts"}, 2]},
            {"$lte": [{"$size": "$accounts"}, threshold]},
        ]}}},
        {"$unwind": "$accounts"},
        {"$lookup": {"from": "accounts", "localField": "accounts", "foreignField": "_id", "as": "a"}},
        {"$unwind": "$a"},
        {"$group": {"_id": "$_id", "people": {"$addToSet": "$a.person_id"}}},
        {"$match": {"$expr": {"$and": [
            {"$gte": [{"$size": "$people"}, 2]},
            {"$lte": [{"$size": "$people"}, threshold]},
        ]}}},
    ]
    for doc in db.transactions.aggregate(pipeline, allowDiskUse=True):
        yield doc["_id"], doc["people"]


def hub_report(db, threshold: int) -> None:
    """Conta os grupos descartados por fan-out.

    O descarte acontece dentro do `$match` de cada pipeline, então o contador em
    `edges_from_groups` nunca vê esses grupos — ele só pegaria um hub que passasse
    pelo filtro do servidor. Esta função pergunta ao servidor direto, para que o
    número publicado em LIMITATIONS.md seja medido e não afirmado.
    """
    print(f"hubs descartados (fan-out > {threshold}):")

    addr = list(db.people.aggregate([
        {"$unwind": "$addresses"},
        {"$group": {"_id": "$addresses.address_id", "n": {"$sum": 1}}},
        {"$match": {"$expr": {"$gt": ["$n", threshold]}}},
        {"$sort": {"n": -1}},
    ], allowDiskUse=True))
    print(f"  endereços: {len(addr)} grupos, {sum(a['n'] for a in addr):,} pessoas")
    for a in addr[:3]:
        print(f"    {a['_id']}: {a['n']:,} pessoas")

    dev = list(db.transactions.aggregate([
        {"$group": {"_id": "$device_id", "contas": {"$addToSet": "$from_account"}}},
        {"$set": {"n": {"$size": "$contas"}}},
        {"$match": {"$expr": {"$gt": ["$n", threshold]}}},
        {"$project": {"n": 1}},
        {"$sort": {"n": -1}},
    ], allowDiskUse=True))
    print(f"  dispositivos: {len(dev)} grupos, {sum(d['n'] for d in dev):,} contas")
    for d in dev[:3]:
        print(f"    {d['_id']}: {d['n']:,} contas")

    pix = list(db.transactions.aggregate([
        {"$match": {"to_pix_key": {"$ne": None}}},
        {"$group": {"_id": "$to_pix_key", "pagadores": {"$addToSet": "$from_account"}}},
        {"$set": {"n": {"$size": "$pagadores"}}},
        {"$match": {"$expr": {"$gt": ["$n", threshold]}}},
        {"$project": {"n": 1}},
        {"$sort": {"n": -1}},
    ], allowDiskUse=True))
    print(f"  chaves PIX de destino: {len(pix)} grupos, {sum(x['n'] for x in pix):,} pagadores")
    for x in pix[:3]:
        print(f"    {x['_id']}: {x['n']:,} pagadores")
    print()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--hub-threshold", type=int, default=int(os.getenv("HUB_FANOUT_THRESHOLD", 50)))
    p.add_argument("--rebuild", action="store_true", help="apaga connections antes de materializar")
    p.add_argument("--report-only", action="store_true", help="só relata os hubs descartados, não escreve")
    args = p.parse_args()

    db = get_db()
    hub_report(db, args.hub_threshold)
    if args.report_only:
        return
    if args.rebuild:
        db.connections.drop()
        print("connections apagada")

    t = args.hub_threshold
    print(f"materializando arestas (limiar de hub = {t})")
    bulk_replace(db.connections, edges_from_groups(address_groups(db, t), "shares_address", t), "shares_address")
    bulk_replace(
        db.connections,
        edges_from_groups(pix_counterparty_groups(db, t), "same_pix_counterparty", t),
        "same_pix_counterparty",
    )
    bulk_replace(db.connections, edges_from_groups(device_groups(db, t), "shares_device", t), "shares_device")

    # Os índices são criados AQUI, não em schema/indexes.js.
    #
    # `--rebuild` faz `drop()`, e `drop()` leva os índices junto. Criar os índices
    # num passo anterior do pipeline significa perdê-los silenciosamente: o
    # traversal continua funcionando, só que por COLLSCAN em cada nível do BFS.
    # Aconteceu de verdade nesta POV — os primeiros benchmarks foram medidos sobre
    # uma coleção sem índice e ninguém percebeu, porque nada quebra.
    print("\níndices de traversal (criados aqui porque --rebuild os apaga)")
    for chave, nome in (
        ([("from", ASCENDING)], "from_1"),
        ([("to", ASCENDING)], "to_1"),
        ([("type", ASCENDING), ("from", ASCENDING)], "type_1_from_1"),
        # Composto para o caminho com poda, que é o padrão da UI. Com `{from: 1}`
        # sozinho, o filtro de `weight` do `restrictSearchWithMatch` roda no
        # estágio FETCH: medido num nó de grau 400, o servidor lia 400 documentos
        # para devolver 227. Com o composto, 227 chaves e 227 documentos.
        # Ganho medido: 27% na profundidade 1; neutro em profundidade maior, onde
        # o custo é montar o resultado, não buscar chave. Custo: ~1% do tamanho
        # da coleção.
        ([("from", ASCENDING), ("weight", ASCENDING)], "from_1_weight_1"),
    ):
        db.connections.create_index(chave, name=nome)
        print(f"  {nome}")

    total = db.connections.estimated_document_count()
    print(f"\nconnections: {total:,} arestas (bidirecionais)")
    for tp in ["shares_device", "shares_address", "same_pix_counterparty"]:
        print(f"  {tp:18s} {db.connections.count_documents({'type': tp}):>10,}")


if __name__ == "__main__":
    main()
