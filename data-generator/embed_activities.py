#!/usr/bin/env python3
"""Vetoriza a descrição de atividade (`cnae_descricao`) das empresas.

## Para que serve numa decisão de crédito

Um grupo econômico com oito CNAEs diferentes **parece** diversificado, e
diversificação é o que dilui risco. Mas "construção de edifícios", "obras de
alvenaria" e "serviços de engenharia de obras" são o mesmo negócio com códigos
distintos: se o setor de construção desacelerar, o grupo inteiro sente junto.

Isso é risco de concentração, e nenhuma comparação por código ou por palavra
encontra — as três frases não dividem termo nenhum além de preposição. A busca
por significado encontra.

## Onde o vetor mora, e por que não é na empresa

Uma coleção própria, `activities`: **um documento por descrição distinta**, com o
vetor.

A primeira versão gravava `activity_embedding` nas 1,2 milhão de empresas e
indexava aquilo. Funcionava e era lento de um jeito difícil de defender: o
`$vectorSearch` percorria 1,2 milhão de documentos com `numCandidates: 10000`
para comparar **32 textos distintos** — 29 segundos por consulta. O objeto da
comparação é a atividade, não a empresa que a menciona.

Com a coleção de atividades a mesma resposta sai de 32 documentos, em
milissegundos, o índice vetorial cabe em qualquer tier, e a carga deixa de
escrever um binário de 512 dimensões em cada empresa.

Vale dizer isso na demo, porque é o erro que quase todo projeto de RAG comete:
indexar a linha em vez de indexar a coisa comparada. Num cadastro de campo livre
o conjunto distinto seria maior — mas continua sendo o conjunto certo.

Uso:
    python embed_activities.py
    python embed_activities.py --force   # reescreve mesmo onde já existe
"""
from __future__ import annotations

import argparse
import os

import httpx
from bson.binary import Binary, BinaryVectorDtype

from pymongo import ReplaceOne

from common import get_db


def embed(textos: list[str], modelo: str, dims: int, chave: str) -> list[list[float]]:
    saida: list[list[float]] = []
    for i in range(0, len(textos), 96):
        lote = textos[i : i + 96]
        r = httpx.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {chave}"},
            json={"input": lote, "model": modelo, "output_dimension": dims, "input_type": "document"},
            timeout=120.0,
        )
        r.raise_for_status()
        saida.extend(d["embedding"] for d in r.json()["data"])
    return saida


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    modelo = os.getenv("EMBEDDING_MODEL", "voyage-3-lite")
    dims = int(os.getenv("EMBEDDING_DIMENSIONS", 512))
    chave = os.environ["VOYAGE_API_KEY"]

    db = get_db()
    # Setor junto: a análise usa para separar o próprio bloco do resto da base, e
    # tê-lo aqui evita voltar a `companies` só para descobrir de que setor é a
    # atividade.
    textos = list(db.companies.aggregate([
        {"$match": {"cnae_descricao": {"$ne": None}}},
        {"$group": {"_id": "$cnae_descricao", "setor": {"$first": "$setor"},
                    "cnae": {"$first": "$cnae"}, "empresas": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ], allowDiskUse=True))
    print(f"{len(textos)} descrições distintas em {db.companies.estimated_document_count():,} empresas")

    faltando = textos
    if not args.force:
        ja = {d["_id"] for d in db.activities.find({"embedding": {"$exists": True}}, {"_id": 1})}
        faltando = [t for t in textos if t["_id"] not in ja]
    if not faltando:
        print("nada a fazer: todas as atividades já têm vetor")
        return

    print(f"embeddando {len(faltando)} com {modelo} ({dims}d)...")
    vetores = embed([t["_id"] for t in faltando], modelo, dims, chave)

    db.activities.bulk_write([
        ReplaceOne(
            {"_id": t["_id"]},
            {
                "_id": t["_id"],
                "cnae": t["cnae"],
                "setor": t["setor"],
                "companies": t["empresas"],
                "embedding": Binary.from_vector(v, BinaryVectorDtype.FLOAT32),
            },
            upsert=True,
        )
        for t, v in zip(faltando, vetores)
    ], ordered=False)

    print(f"{db.activities.estimated_document_count()} atividades com vetor")
    print("  índice vetorial: .venv/bin/python schema/search_indexes.py")


if __name__ == "__main__":
    main()
