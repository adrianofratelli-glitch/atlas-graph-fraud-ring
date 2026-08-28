"""Atlas Search sobre razão social.

## O problema real que isto resolve

Cadastro de empresa é escrito de N formas: "Construtora Alfa S.A.", "CONSTRUTORA
ALFA SA", "Construtora Alpha SA". Um `$graphLookup` por igualdade nunca liga as
três, e a esteira de crédito trata como empresas distintas — que é como um grupo
econômico passa despercebido.

O ponto da POV aqui não é que MongoDB faz busca: é que faz busca **no mesmo motor
e no mesmo cluster** que guarda a cadeia societária. Resolver razão social não
exige um segundo sistema, nem um pipeline sincronizando cadastro para ele.

## Degradação

Se o índice não existe ou está `BUILDING`, levanta `IndexUnavailable`, a rota
devolve 503 e o frontend mostra um aviso naquele painel. O traversal continua
funcionando: degradação é por recurso, não por tela.
"""
from __future__ import annotations

import time
from typing import Any

from app.config import get_settings
from app.db.client import get_db, with_retry


class IndexUnavailable(RuntimeError):
    def __init__(self, index_name: str, status: str) -> None:
        self.index_name = index_name
        self.status = status
        super().__init__(f"index `{index_name}` unavailable (status: {status})")


def index_status(collection: str, name: str) -> str:
    coll = get_db()[collection]
    try:
        found = next((i for i in coll.list_search_indexes() if i["name"] == name), None)
    except Exception:
        # `mongod` community não tem `list_search_indexes`. Não é erro de
        # operação, é ausência de capacidade — a tela precisa distinguir os dois.
        return "UNSUPPORTED"
    return found["status"] if found else "MISSING"


def _require(collection: str, name: str) -> None:
    st = index_status(collection, name)
    if st != "READY":
        raise IndexUnavailable(name, st)


def _clausulas(campo: str, query: str) -> dict[str, Any]:
    """As três cláusulas que resolvem grafia divergente, iguais nos dois índices."""
    return {
        "should": [
            {"text": {"query": query, "path": campo, "score": {"boost": {"value": 3}}}},
            {"text": {"query": query, "path": campo, "fuzzy": {"maxEdits": 2, "prefixLength": 1}}},
            {"autocomplete": {"query": query, "path": campo}},
        ],
        "minimumShouldMatch": 1,
    }


def _busca_socios(
    query: str, limit: int, node_ids: set[str], escopo_apenas: bool = True
) -> list[dict[str, Any]]:
    """Sócios pessoa física.

    Buscar só razão social deixava metade do grafo inalcançável: quem descobre um
    grupo econômico frequentemente parte do **nome de uma pessoa** — o sócio que
    aparece em empresas de grupos diferentes —, não do CNPJ. Digitar o nome de um
    sócio que está desenhado na tela e não receber nada é o tipo de furo que
    encerra a credibilidade da demonstração.

    Degrada sozinho: se o índice de pessoas não estiver pronto, a busca de
    empresas continua respondendo.
    """
    s = get_settings()
    if index_status("people", s.people_search_index) != "READY":
        return []
    compound = dict(_clausulas("name", query))
    if escopo_apenas:
        pessoas_na_tela = [i for i in node_ids if isinstance(i, str) and i.startswith("person_")]
        if not pessoas_na_tela:
            return []
        compound["filter"] = [{"in": {"path": "_id", "value": pessoas_na_tela}}]
    pipeline = [
        {"$search": {"index": s.people_search_index, "compound": compound}},
        {"$limit": limit},
        {
            "$project": {
                "name": 1,
                "occupation": 1,
                "age_band": 1,
                "income_band": 1,
                "score": {"$meta": "searchScore"},
            }
        },
    ]
    try:
        achados = with_retry(lambda: list(get_db().people.aggregate(pipeline)), "resolve_person")
    except Exception:  # noqa: BLE001 — sócio é complemento, nunca derruba a busca
        return []

    db = get_db()
    saida = []
    for p in achados:
        # Quantas empresas essa pessoa controla? É o número que diz se vale olhar:
        # um sócio-administrador de sete empresas em grupos diferentes é uma
        # pergunta, um sócio de uma é cadastro.
        n = db.ownership.count_documents({"owner_id": p["_id"], "owner_type": "individual"})
        saida.append(
            {
                "_id": p["_id"],
                "kind": "person",
                "label": p.get("name"),
                "occupation": p.get("occupation"),
                "age_band": p.get("age_band"),
                "income_band": p.get("income_band"),
                "companies": n,
                "score": p["score"],
                "in_group": p["_id"] in node_ids,
            }
        )
    return saida


def resolve_company(
    query: str,
    limit: int = 10,
    company_ids: list[str] | None = None,
    node_ids: list[str] | None = None,
    escopo_apenas: bool = True,
) -> dict[str, Any]:
    """Busca difusa em `razao_social` e no nome dos sócios, **escopada ao grafo**.

    `compound.should` com três cláusulas: termo exato (pontua mais alto), fuzzy
    com `maxEdits: 2` (pega grafia divergente) e autocomplete (pega digitação
    parcial).

    ## Por que o padrão é escopado

    A tela mostra um grupo econômico. Digitar "ana" e receber vinte pessoas e
    empresas que não têm relação nenhuma com o que está desenhado não é busca, é
    ruído: o analista precisa parear cada linha com o grafo mentalmente, e não tem
    como. Por padrão a busca filtra pelos ids que estão na tela — empresas e
    sócios —, usando `compound.filter` no próprio índice.

    `escopo_apenas=False` abre para a base inteira, e existe por um motivo
    específico: **entity resolution**. Uma empresa que ainda não está no grafo pode
    pertencer ao grupo por um vínculo que o cadastro não registra, e achá-la é
    exatamente o trabalho de quem investiga. Isso é uma ação deliberada, com o
    resultado marcado como "fora do grupo", não o comportamento padrão.

    Cada resultado diz se está no grupo da tela. A nota do Lucene não desempata
    homônimos — empate entre linhas que casam do mesmo jeito é correto, não é
    defeito —, então "está no grupo" é o critério de ordenação mais forte.
    """
    s = get_settings()
    _require("companies", s.company_search_index)

    # Duas buscas, escopada primeiro.
    #
    # A busca global sozinha é honesta mas inútil na tela: "Farias Sousa" casa com
    # milhares de razões sociais, todas com a mesma nota, e a empresa do grupo que
    # o analista está olhando não aparece em lugar nenhum das dez primeiras. O
    # `compound.filter` por `_id` restringe a primeira busca ao grupo em tela — é
    # por isso que `_id` está mapeado como `token` no índice.
    escopo = [i for i in (company_ids or []) if isinstance(i, str)]

    def _busca(filtro_ids: list[str] | None) -> list[dict[str, Any]]:
        compound = dict(_clausulas("razao_social", query))
        if filtro_ids:
            compound["filter"] = [{"in": {"path": "_id", "value": filtro_ids}}]
        return [
            {"$search": {"index": s.company_search_index, "compound": compound}},
            {"$limit": limit},
        ]

    # Escopado: uma consulta só, filtrada. Global: a busca ampla, com os acertos
    # do grupo promovidos ao topo (ver abaixo).
    pipeline: list[dict[str, Any]] = _busca(escopo if escopo_apenas else None) + [
        {
            "$lookup": {
                "from": "credit_exposure",
                "localField": "_id",
                "foreignField": "company_id",
                "as": "cred",
            }
        },
        {
            "$project": {
                "razao_social": 1,
                "cnpj": 1,
                "uf": 1,
                "porte": 1,
                "situacao": 1,
                "is_holding": 1,
                "cnae_descricao": 1,
                "credit_status": 1,
                "limite": {"$first": "$cred.limite"},
                "vencido": {"$first": "$cred.vencido"},
                "rating": {"$first": "$cred.rating"},
                "score": {"$meta": "searchScore"},
            }
        },
    ]

    started = time.perf_counter()
    db = get_db()
    results = with_retry(lambda: list(db.companies.aggregate(pipeline)), "resolve_company")

    if escopo and not escopo_apenas:
        # O restante do pipeline (hidratação e projeção) é o mesmo; só a primeira
        # etapa muda. Os acertos do grupo vão para o topo, sem reordenar por nota:
        # "está no grupo que você está olhando" é um critério mais forte do que
        # qualquer diferença de relevância entre dois homônimos.
        no_grupo = with_retry(
            lambda: list(db.companies.aggregate(_busca(escopo) + pipeline[2:])),
            "resolve_company escopado",
        )
        vistos = {r["_id"] for r in no_grupo}
        results = no_grupo + [r for r in results if r["_id"] not in vistos]
        results = results[:limit]
    na_tela = set(company_ids or [])
    todos_na_tela = set(node_ids or []) | na_tela
    for r in results:
        r["kind"] = "company"
        r["label"] = r.get("razao_social")
        r["in_group"] = r["_id"] in na_tela

    socios = _busca_socios(query, limit, todos_na_tela, escopo_apenas)
    results = results + socios
    # Quem está no grupo da tela vem primeiro, e empresa antes de sócio em caso de
    # empate: o CNPJ é a chave de negócio da esteira de crédito.
    results.sort(key=lambda r: (not r["in_group"], r["kind"] != "company", -r["score"]))
    results = results[: limit * 2]

    return {
        "query": query,
        "index": s.company_search_index,
        "people_index": s.people_search_index,
        "companies_found": sum(1 for r in results if r["kind"] == "company"),
        "people_found": sum(1 for r in results if r["kind"] == "person"),
        "scoped": escopo_apenas,
        "score_note": (
            "Relevância do Lucene: não tem unidade nem teto. Linhas que casam do mesmo "
            "jeito no mesmo campo tiram a mesma nota — empate aqui é correto, não é defeito."
        ),
        "results": results,
        "in_group": sum(1 for r in results if r["in_group"]),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
