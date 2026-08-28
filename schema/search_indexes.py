#!/usr/bin/env python3
"""Cria o índice do Atlas Search sobre `companies.razao_social` e espera ficar READY.

Índice de busca não é índice de banco: tem ciclo de vida próprio
(`PENDING` → `BUILDING` → `READY`) e por isso não cabe em `schema/indexes.js`.

`razao_social` recebe dois analisadores: o padrão, para busca por termo, e
`autocomplete`, para digitação parcial. A combinação é o que resolve grafia
divergente de razão social — o caso em que a esteira de crédito trata "Construtora
Alfa S.A." e "CONSTRUTORA ALPHA SA" como empresas distintas.

Uso:
    python schema/search_indexes.py
    python schema/search_indexes.py --status
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data-generator"))
from common import get_db  # noqa: E402

INDEX_NAME = os.getenv("ATLAS_SEARCH_INDEX_NAME", "companies_name_resolution")
PEOPLE_INDEX = os.getenv("PEOPLE_SEARCH_INDEX_NAME", "people_name_resolution")

DEFINITION = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "razao_social": [
                {"type": "string", "analyzer": "lucene.standard"},
                {"type": "autocomplete", "minGrams": 3, "maxGrams": 15, "tokenization": "edgeGram"},
            ],
            "cnpj": {"type": "string", "analyzer": "lucene.keyword"},
            "uf": {"type": "token"},
            "situacao": {"type": "token"},
            # `_id` como token para o `compound.filter` da busca escopada ao grupo
            # que está na tela. Sem ele, procurar um sobrenome comum devolve dez
            # homônimos com a mesma nota e **nenhum** deles é o do grupo — o
            # Lucene não tem como saber qual importa, e a nota de relevância não
            # desempata isso. O filtro resolve no servidor.
            "_id": {"type": "token"},
        },
    }
}


# Sócio pessoa física precisa do mesmo tratamento que a empresa: quem descobre um
# grupo econômico frequentemente parte do nome de uma pessoa, não do CNPJ. Buscar
# só razão social deixava metade do grafo inalcançável pela busca.
PEOPLE_DEFINITION = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "name": [
                {"type": "string", "analyzer": "lucene.standard"},
                {"type": "autocomplete", "minGrams": 3, "maxGrams": 15, "tokenization": "edgeGram"},
            ],
            "document_id": {"type": "string", "analyzer": "lucene.keyword"},
            "occupation": {"type": "token"},
            # Mesmo motivo do `_id` em `companies`: a busca da tela é **escopada
            # ao grafo em uso**, e escopo se faz com `compound.filter`, não
            # filtrando no cliente depois de já ter perdido os resultados certos.
            "_id": {"type": "token"},
        },
    }
}


# Índice vetorial sobre a coleção `activities` — **uma linha por descrição
# distinta**, não uma por empresa.
#
# Indexar `companies.activity_embedding` significava percorrer 1,2 milhão de
# documentos para comparar 32 textos: 29 s por consulta. O objeto da comparação é
# a atividade. Com 32 documentos a mesma resposta sai em milissegundos.
VECTOR_INDEX = os.getenv("VECTOR_INDEX_NAME", "activities_vector")
DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", 512))

# `quantization: scalar` não é tuning: é requisito de capacidade. O cluster da
# demonstração é um M20 compartilhado, e a quantização escalar reduz o grafo HNSW
# residente para cerca de um quarto.
VECTOR_DEFINITION = {
    "fields": [
        {
            "type": "vector",
            "path": "embedding",
            "numDimensions": DIMENSIONS,
            "similarity": "cosine",
            "quantization": "scalar",
        },
        {"type": "filter", "path": "setor"},
    ]
}


def status(coll, nome: str) -> str:
    try:
        found = next((i for i in coll.list_search_indexes() if i["name"] == nome), None)
    except Exception as exc:
        return f"UNSUPPORTED ({str(exc)[:50]})"
    return found["status"] if found else "MISSING"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--status", action="store_true", help="só relata, não cria")
    p.add_argument("--timeout", type=int, default=600)
    args = p.parse_args()

    db = get_db()
    coll = db.companies

    alvos = [
        (coll, INDEX_NAME, DEFINITION),
        (db.people, PEOPLE_INDEX, PEOPLE_DEFINITION),
        (db.activities, VECTOR_INDEX, VECTOR_DEFINITION),
    ]

    if args.status:
        for c, nome, _ in alvos:
            print(f"{nome}: {status(c, nome)}")
        return

    for c, nome, definicao in alvos:
        existentes = {i["name"] for i in c.list_search_indexes()}
        tipo = "vectorSearch" if nome == VECTOR_INDEX else "search"
        if nome in existentes:
            c.update_search_index(nome, definicao)
            print(f"~ índice `{nome}` atualizado")
        else:
            c.create_search_index({"name": nome, "type": tipo, "definition": definicao})
            print(f"+ índice `{nome}` ({tipo}) criado")

    prazo = time.time() + args.timeout
    pendentes = {nome for _, nome, _ in alvos}
    while time.time() < prazo and pendentes:
        for c, nome, _ in alvos:
            if nome in pendentes and status(c, nome) == "READY":
                pendentes.discard(nome)
                print(f"  {nome}: READY   ")
        if pendentes:
            time.sleep(5)
    if pendentes:
        raise SystemExit(f"tempo esgotado esperando: {', '.join(sorted(pendentes))}")


if __name__ == "__main__":
    main()
