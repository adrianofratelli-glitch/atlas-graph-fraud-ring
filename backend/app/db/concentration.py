"""Análise semântica automática da concentração de um grupo econômico.

## A pergunta que o painel responde sozinho

Nada de caixa de texto. Dado o grupo que está na tela, ele responde: **este grupo
é tão diversificado quanto parece?**

Diversificação dilui risco de crédito — se um setor desacelera, os outros
sustentam. Por isso um grupo com oito CNAEs diferentes recebe leitura melhor que
um com dois. O problema é que o código CNAE mente por omissão: "construção de
edifícios", "obras de alvenaria e acabamento" e "serviços de engenharia e projeto
de obras" são três códigos e um negócio só.

Comparar códigos não encontra isso. Comparar palavras também não — as três frases
não dividem termo nenhum além de preposição. Comparar **significado** encontra.

## Como a análise é feita

1. Agrupa as atividades das empresas do grupo, com a exposição de cada uma.
2. Toma o vetor da atividade dominante e pergunta ao `$vectorSearch` quais das
   outras atividades **do próprio grupo** dizem a mesma coisa.
3. Reporta quantos negócios semanticamente distintos existem de fato, e quanto da
   exposição está no maior deles.

O número que importa é o último: *"o grupo tem oito CNAEs, três negócios, e 71%
da exposição num só"*. É concentração, e nenhuma consulta por igualdade a mostra.

## O que este módulo não faz

Não pontua risco e não recomenda decisão. Descreve o que está no dado. Virar isso
em score exigiria histórico de perda por setor, que esta POV não tem — e fingir
que tem seria inventar a parte que mais importa.
"""
from __future__ import annotations

import time
from typing import Any

from bson.binary import Binary, BinaryVectorDtype

from app.config import get_settings
from app.db.client import get_db, with_retry
from app.db.search import IndexUnavailable, index_status

# Acima disto, duas atividades são o mesmo negócio para efeito de concentração.
# Calibrado no dado desta POV: descrições do mesmo setor ficam acima de 0,80 e
# setores diferentes ficam abaixo. Não é um limiar universal, e a tela mostra o
# score de cada par para o número não virar caixa-preta.
LIMIAR_EQUIVALENCIA = 0.80
# Com `activities` tendo algumas dezenas de documentos, o teto de 10.000 do
# servidor não tem o que fazer: 200 já varre a coleção inteira várias vezes. O
# número alto só era necessário enquanto o índice vivia sobre 1,2 milhão de
# empresas e o filtro seletivo esgotava os candidatos antes de juntar resultado.
MAX_CANDIDATES = 200


def group_concentration(company_ids: list[str]) -> dict[str, Any]:
    s = get_settings()
    db = get_db()
    started = time.perf_counter()

    if not company_ids:
        return {"ok": True, "empty": True, "reason": "nenhum grupo na tela"}

    # --- 1. o que o grupo faz, por exposição ---
    atividades = with_retry(
        lambda: list(
            db.companies.aggregate(
                [
                    {"$match": {"_id": {"$in": company_ids}, "is_holding": {"$ne": True}}},
                    {
                        "$lookup": {
                            "from": "credit_exposure",
                            "localField": "_id",
                            "foreignField": "company_id",
                            "as": "cred",
                        }
                    },
                    {
                        "$group": {
                            "_id": "$cnae_descricao",
                            "companies": {"$sum": 1},
                            "limite": {"$sum": {"$ifNull": [{"$first": "$cred.limite"}, 0]}},
                            "vencido": {"$sum": {"$ifNull": [{"$first": "$cred.vencido"}, 0]}},
                            "exemplo": {"$first": "$_id"},
                        }
                    },
                    {"$sort": {"limite": -1}},
                ],
                allowDiskUse=True,
            )
        ),
        "concentration: atividades",
    )
    if not atividades:
        return {"ok": True, "empty": True, "reason": "nenhuma empresa operacional neste grupo"}

    total_limite = sum(a["limite"] for a in atividades)
    lista = [
        {
            "activity": a["_id"],
            "companies": a["companies"],
            "limite": round(a["limite"], 2),
            "vencido": round(a["vencido"], 2),
            "share": round(a["limite"] / total_limite, 4) if total_limite else 0.0,
        }
        for a in atividades
    ]

    # O índice vetorial vive em `activities`, não em `companies`: é lá que
    # está a coisa comparada.
    if index_status("activities", s.vector_index) != "READY":
        raise IndexUnavailable(s.vector_index, index_status("activities", s.vector_index))

    # --- 2. a atividade dominante vira a consulta ---
    #
    # A busca roda sobre `activities` — uma linha por descrição distinta —, não
    # sobre `companies`. Comparar significado entre atividades não precisa
    # percorrer 1,2 milhão de empresas que repetem 32 textos: media 29 s daquele
    # jeito e milissegundos deste. O erro é comum e vale citar na demo: indexar a
    # linha em vez de indexar a coisa comparada.
    dominante = atividades[0]
    semente = with_retry(
        lambda: db.activities.find_one(
            {"_id": dominante["_id"], "embedding": {"$exists": True}}, {"embedding": 1}
        ),
        "concentration: vetor semente",
    )
    if not semente:
        raise IndexUnavailable(s.vector_index, "NO_EMBEDDING_FOR_ACTIVITY")

    vetor = semente["embedding"]
    if not isinstance(vetor, Binary):
        vetor = Binary.from_vector(list(vetor), BinaryVectorDtype.FLOAT32)

    # `numCandidates` alto perde o sentido num universo de algumas dezenas de
    # documentos: o teto passa a ser o próprio tamanho da coleção.
    vizinhas = with_retry(
        lambda: list(
            db.activities.aggregate(
                [
                    {
                        "$vectorSearch": {
                            "index": s.vector_index,
                            "path": "embedding",
                            "queryVector": vetor,
                            "numCandidates": MAX_CANDIDATES,
                            "limit": 50,
                        }
                    },
                    {"$set": {"score": {"$meta": "vectorSearchScore"}}},
                    {"$project": {"_id": 1, "score": 1}},
                    {"$sort": {"score": -1}},
                ]
            )
        ),
        "concentration: atividades equivalentes",
    )
    score_por_atividade = {v["_id"]: round(v["score"], 3) for v in vizinhas}

    # --- 3. quantos negócios distintos, de verdade ---
    equivalentes = [
        {"activity": a["activity"], "score": score_por_atividade.get(a["activity"]), **a}
        for a in lista
        if score_por_atividade.get(a["activity"], 0) >= LIMIAR_EQUIVALENCIA
    ]
    limite_no_bloco = sum(e["limite"] for e in equivalentes)
    distintos = max(1, len(lista) - max(0, len(equivalentes) - 1))

    return {
        "ok": True,
        "empty": False,
        "currency": s.currency,
        "cnae_count": len(lista),
        "distinct_businesses": distintos,
        "dominant_activity": dominante["_id"],
        "activities": lista,
        "equivalent_to_dominant": equivalentes,
        "dominant_block_limite": round(limite_no_bloco, 2),
        # A leitura da mesa: quanto do crédito está num negócio só.
        "dominant_block_share": round(limite_no_bloco / total_limite, 4) if total_limite else 0.0,
        "total_limite": round(total_limite, 2),
        "threshold": LIMIAR_EQUIVALENCIA,
        "model": s.embedding_model,
        "dimensions": s.embedding_dimensions,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
