#!/usr/bin/env python3
"""Migra o modelo de chave PIX no dataset já gerado, sem regerar 150 mil pessoas.

## Por que a migração existe

O modelo anterior colocava `pix_key` em `people` e ligava duas pessoas pela
**mesma chave**. Isso não acontece num banco brasileiro: o DICT (Resolução BCB
nº 1/2020) garante que uma chave endereça exatamente uma conta transacional por
vez e rejeita o cadastro duplicado. A aresta `shares_pix_key` descrevia um estado
que o sistema de pagamentos impede — e é o tipo de erro que um arquiteto de
cliente derruba na primeira pergunta.

O vínculo real é o outro lado do pagamento: **contas distintas que pagam para a
mesma chave de destino**, a conta de arrecadação no fim do funil de mulas. Daí
`same_pix_counterparty`.

## O que este script faz

1. dá a cada conta uma chave PIX própria e única (CPF na conta k=0, EVP nas demais);
2. preenche `transactions.to_pix_key` com a chave da conta de destino;
3. faz os membros de cada anel pagarem para a conta de arrecadação do seu ramo;
4. remove `people.pix_key` e as arestas `shares_pix_key`, que não deveriam existir.

Idempotente: rodar de novo reescreve os mesmos valores. Depois dele, rode
`materialize_connections.py` para reconstruir as arestas.

Uso:
    python migrate_pix_model.py            # migra
    python migrate_pix_model.py --dry-run  # só relata
"""
from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from pymongo import UpdateOne

from common import bulk_replace, chunked, det_id, get_db

NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)
SEED = 20260826


def pix_key_for(person_idx: int, k: int) -> str:
    """Mesma função do gerador. Duplicada aqui de propósito: a migração precisa
    rodar contra um checkout antigo sem depender do gerador novo."""
    if k == 0:
        return f"{person_idx:011d}"
    return det_id("pix", person_idx, k)


MAX_ACCOUNTS_PER_PERSON = 3


def chaves_das_contas(db, dry_run: bool) -> dict[str, str]:
    """Atribui uma chave única por conta e devolve o mapa conta -> chave.

    O índice `k` da conta dentro da pessoa não está gravado no documento, mas o
    `_id` **é** `det_id("account", i, k)` — o gerador é determinístico. Então dá
    para recuperar `k` reconstruindo o `_id` candidato para cada k possível e
    vendo qual bate.

    Isso importa: sem `k`, todas as contas de uma pessoa cairiam na mesma chave
    CPF e o índice único de `accounts.pix_key` rejeitaria a escrita — exatamente
    como o DICT rejeitaria na vida real.
    """
    mapa: dict[str, str] = {}
    updates: list[UpdateOne] = []
    sem_indice = 0
    for a in db.accounts.find({}, {"owner_seed_index": 1}):
        i = a.get("owner_seed_index")
        if i is None:
            sem_indice += 1
            continue
        k = next(
            (k for k in range(MAX_ACCOUNTS_PER_PERSON) if det_id("account", i, k) == a["_id"]),
            None,
        )
        if k is None:
            sem_indice += 1
            continue
        chave = pix_key_for(int(i), k)
        mapa[a["_id"]] = chave
        updates.append(
            UpdateOne(
                {"_id": a["_id"]},
                {"$set": {"pix_key": chave, "pix_key_type": "cpf" if k == 0 else "evp"}},
            )
        )
    distintas = len(set(mapa.values()))
    print(f"contas: {len(mapa):,} chaves atribuídas, {distintas:,} distintas")
    if distintas != len(mapa):
        raise SystemExit(
            f"chave PIX repetida em {len(mapa) - distintas:,} contas — "
            "o DICT não permite, e o índice único rejeitaria a escrita"
        )
    if sem_indice:
        print(f"  {sem_indice:,} contas sem índice recuperável, ignoradas")
    if not dry_run:
        for batch in chunked(updates):
            db.accounts.bulk_write(batch, ordered=False)
    return mapa


def preencher_destino(db, mapa: dict[str, str], dry_run: bool) -> int:
    """`transactions.to_pix_key` = chave PIX da conta de destino.

    Feito inteiramente **no servidor**, com `$lookup` + `$merge`. O caminho óbvio
    — um `update_many` por chave — seriam 240 mil idas ao cluster, cada uma
    pagando latência de rede; mesmo a 10 ms, é mais de meia hora. Aqui nenhum
    documento atravessa a rede: o servidor lê `transactions`, resolve a conta de
    destino e reescreve o campo na própria coleção.

    `whenMatched: merge` preserva os demais campos da transação, e
    `whenNotMatched: discard` garante que o estágio não crie documento novo.
    """
    # Retomável de propósito: o `$merge` sobre 600 mil transações pode ser
    # interrompido (socket, failover, Ctrl-C) com parte do trabalho já commitada.
    # Filtrar pelo que ainda não tem chave faz a segunda execução terminar só o
    # que falta, em vez de refazer tudo.
    pendentes = {"to_account": {"$ne": None}, "to_pix_key": None}
    if dry_run:
        n = db.transactions.count_documents(pendentes)
        print(f"transações: {n:,} ainda sem to_pix_key (dry-run)")
        return 0

    pipeline = [
        {"$match": pendentes},
        {
            "$lookup": {
                "from": "accounts",
                "localField": "to_account",
                "foreignField": "_id",
                "pipeline": [{"$project": {"pix_key": 1}}],
                "as": "_conta",
            }
        },
        {"$set": {"to_pix_key": {"$first": "$_conta.pix_key"}}},
        {"$unset": "_conta"},
        {"$match": {"to_pix_key": {"$ne": None}}},
        {
            "$merge": {
                "into": "transactions",
                "on": "_id",
                "whenMatched": "merge",
                "whenNotMatched": "discard",
            }
        },
    ]
    restantes = db.transactions.count_documents(pendentes)
    while restantes:
        db.transactions.aggregate(pipeline, allowDiskUse=True)
        antes, restantes = restantes, db.transactions.count_documents(pendentes)
        print(f"  to_pix_key: faltam {restantes:,}")
        if restantes >= antes:
            # Nenhum progresso: o que sobrou aponta para conta inexistente.
            print(f"  {restantes:,} transações apontam para conta sem chave; ignoradas")
            break
    total = db.transactions.count_documents({"to_pix_key": {"$ne": None}})
    print(f"transações: {total:,} com to_pix_key preenchido")
    return total


def funil_dos_aneis(db, mapa: dict[str, str], dry_run: bool) -> int:
    """Cada membro do anel paga para a conta de arrecadação do seu ramo.

    Sem isso, o anel perde o vínculo por PIX que a chave compartilhada dava antes,
    e a rede fecharia só por dispositivo e endereço. O funil é o padrão correto e
    é mais forte como evidência: N contas sem relação pingando na mesma chave.
    """
    rng = random.Random(SEED)
    BRANCHING = 4
    docs = []
    for r in db.rings.find():
        ring_id = r["ring_id"]
        membros = [
            p["_id"]
            for p in db.people.find({"ring_id": ring_id}, {"seed_index": 1}).sort("seed_index", -1)
        ]
        if len(membros) < 2:
            continue
        contas = {}
        for a in db.accounts.find({"person_id": {"$in": membros}}, {"person_id": 1}):
            contas.setdefault(a["person_id"], a["_id"])
        # Mesma topologia do gerador: cada membro paga o pai do seu ramo, então os
        # pagadores de uma chave são os irmãos. Espalhar por coletores fixos
        # criava cliques grandes e achatava o diâmetro do anel — ver o comentário
        # equivalente em `inject_fraud_rings.py`.
        for idx, pid in enumerate(membros):
            if idx == 0 or pid not in contas:
                continue
            coletor = membros[(idx - 1) // BRANCHING]
            if coletor == pid or coletor not in contas:
                continue
            destino = contas[coletor]
            for t in range(2):
                docs.append(
                    {
                        "_id": det_id("txn", "funnel", ring_id, idx, t),
                        "from_account": contas[pid],
                        "to_account": destino,
                        "to_pix_key": mapa.get(destino),
                        "device_id": det_id("device", "ring", ring_id, max(idx, 1)),
                        "amount": round(rng.uniform(900, 4800), 2),
                        "reason_text": "transferencia para conta propria",
                        "timestamp": NOW - timedelta(seconds=rng.randint(0, 60 * 86400)),
                        "ring_id": ring_id,
                    }
                )
    docs = [d for d in docs if d["to_pix_key"]]
    print(f"funil dos anéis: {len(docs):,} transações")
    if not dry_run and docs:
        bulk_replace(db.transactions, docs, "transações de funil")
    return len(docs)


def limpar_modelo_antigo(db, dry_run: bool) -> None:
    """Remove o que descrevia um estado que o DICT impede."""
    n_pessoas = db.people.count_documents({"pix_key": {"$exists": True}})
    n_arestas = db.connections.count_documents({"type": "shares_pix_key"})
    print(f"modelo antigo: {n_pessoas:,} pessoas com pix_key, {n_arestas:,} arestas shares_pix_key")
    if dry_run:
        return
    db.people.update_many({"pix_key": {"$exists": True}}, {"$unset": {"pix_key": ""}})
    db.connections.delete_many({"type": "shares_pix_key"})
    try:
        db.people.drop_index("pix_key_1")
    except Exception:
        pass  # índice já pode não existir; não é erro


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="só relata, não escreve")
    args = p.parse_args()

    db = get_db()
    print("migrando o modelo de chave PIX (DICT: uma chave, uma conta)\n")
    mapa = chaves_das_contas(db, args.dry_run)
    preencher_destino(db, mapa, args.dry_run)
    funil_dos_aneis(db, mapa, args.dry_run)
    limpar_modelo_antigo(db, args.dry_run)

    if not args.dry_run:
        db.accounts.create_index("pix_key", unique=True, sparse=True, name="pix_key_1")
        db.transactions.create_index("to_pix_key", sparse=True, name="to_pix_key_1")
        print("\níndices: accounts.pix_key_1 (único), transactions.to_pix_key_1")
        print("\nAgora rode: python data-generator/materialize_connections.py")


if __name__ == "__main__":
    main()
