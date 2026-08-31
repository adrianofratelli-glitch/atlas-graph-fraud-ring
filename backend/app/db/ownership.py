"""Cadeia societária e grupo econômico.

## A pergunta que este módulo responde

"Dado este CNPJ, qual é o grupo econômico dele e quanto o grupo já deve para o
banco?" É uma consulta **pontual, por chave de negócio, sobre uma árvore rasa** —
não exploração analítica de um grafo denso. A distinção importa e a tabela de
critério está no `README.md`: é neste padrão que `$graphLookup` ganha de um banco
de grafo dedicado, porque o gargalo do cliente não é o algoritmo, é carregar o
volume e operar a infraestrutura.

## Direção da aresta, que é o detalhe que mais confunde

`ownership` é dirigida: `owner_id` participa de `owned_id`. Isso significa dois
traversals opostos, e trocar os campos por engano devolve silenciosamente o
conjunto errado:

- **subir** (quem é dono de mim): `connectToField: "owned_id"`, e continua pelo
  `owner_id` encontrado — `connectFromField: "owner_id"`;
- **descer** (de quem eu sou dono): `connectToField: "owner_id"`, e continua pelo
  `owned_id` — `connectFromField: "owned_id"`.

Por isso a aresta **não** é materializada nos dois sentidos como as do padrão
anterior desta POV: aqui a direção é a informação.

## Como o grupo é montado

1. Sobe do CNPJ consultado até os controladores. O topo pode ser uma holding
   (pessoa jurídica) ou terminar em pessoa física.
2. Desce das holdings encontradas para pegar todas as controladas — incluindo as
   "irmãs" da empresa consultada, que são exatamente as que o analista não vê
   olhando o cadastro dela.
3. Soma a exposição de crédito de todo o conjunto.

São duas agregações e não uma porque as duas direções partem de conjuntos
diferentes: a segunda precisa das raízes que a primeira descobriu. Fundir isso num
`$facet` só economizaria uma ida ao cluster e esconderia o raciocínio.
"""
from __future__ import annotations

import time
from typing import Any

from pymongo.errors import ExecutionTimeout, OperationFailure

from app.config import get_settings
from app.db.client import get_db, with_retry


# Teto de raízes das quais se desce. Um grupo com mais controladores no topo é
# caso de processamento em lote, não de consulta interativa na tela.
MAX_RAIZES = 5


def clamp_depth(requested: int | None) -> int:
    """Mesma regra do resto da POV: o frontend sugere, o backend decide."""
    s = get_settings()
    if requested is None:
        return 3
    return max(1, min(int(requested), s.depth_cap))


def _falha(exc: OperationFailure) -> str:
    if isinstance(exc, ExecutionTimeout):
        return "tempo limite: o traversal não terminou dentro do teto configurado"
    msg = str(exc)
    if "exceeds" in msg and "bytes" in msg:
        return "limite de 100 MB do documento de saída do $graphLookup"
    return f"agregação falhou: {msg.split(', full error')[0]}"


def _graph_lookup(direcao: str, depth: int, start: str) -> dict[str, Any]:
    """Estágio `$graphLookup` de uma das duas direções.

    `restrictSearchWithMatch` não entra aqui de propósito: filtrar `owner_type`
    cortaria o sócio pessoa física, e é justamente o sócio PF que costuma ser a
    ponte entre duas empresas que o cadastro não relaciona.
    """
    if direcao == "up":
        de, para = "owner_id", "owned_id"
    else:
        de, para = "owned_id", "owner_id"
    return {
        "from": "ownership",
        "startWith": start,
        "connectFromField": de,
        "connectToField": para,
        "as": "cadeia",
        "maxDepth": depth,
        "depthField": "nivel",
    }


_ARESTA = {
    "owner_id": "$$e.owner_id",
    "owner_type": "$$e.owner_type",
    "owned_id": "$$e.owned_id",
    "percentage": "$$e.percentage",
    "qualificacao": "$$e.qualificacao",
    "nivel": "$$e.nivel",
}


def _pipeline_grupo(cnpj: str, depth: int, teto_arestas: int, teto_raizes: int) -> list[dict[str, Any]]:
    """Grupo econômico inteiro em **uma** ida ao cluster.

    A versão anterior fazia o mesmo trabalho em até dez chamadas em série: o
    `find` do CNPJ, o traversal para cima, um traversal para baixo **por raiz num
    laço Python**, e três `find` de hidratação. Com RTT de 8 ms ao cluster isso
    era ~50 ms de resposta para ~3 ms de trabalho de banco — a latência era a
    serialização, não o `$graphLookup`.

    Aqui as descidas viram um `$lookup` correlacionado que roda no servidor sobre
    todas as raízes de uma vez, e a hidratação de empresas, sócios e exposição
    vira mais três `$lookup` no mesmo pipeline. Uma volta de rede, não dez.
    """
    corporativos = {
        "$map": {
            "input": {"$filter": {"input": "$acima", "as": "e",
                                  "cond": {"$eq": ["$$e.owner_type", "corporate"]}}},
            "as": "e", "in": "$$e.owner_id",
        }
    }
    controladas_por_pj = {
        "$map": {
            "input": {"$filter": {"input": "$acima", "as": "e",
                                  "cond": {"$eq": ["$$e.owner_type", "corporate"]}}},
            "as": "e", "in": "$$e.owned_id",
        }
    }

    return [
        {"$match": {"cnpj": cnpj}},
        {"$limit": 1},

        # 1. sobe até os controladores
        {"$graphLookup": _graph_lookup("up", depth, "$_id")},
        {"$set": {"acima": {"$slice": ["$cadeia", teto_arestas]}}},
        {"$unset": "cadeia"},

        # 2. raízes do grupo: PJ sem **dono corporativo** acima.
        #    O critério não é "não aparece como controlada" — a holding aparece,
        #    porque os sócios pessoa física dela produzem arestas em que ela é a
        #    controlada. Usar aquele critério devolvia o grupo pela metade, sem
        #    erro nenhum. É a ausência de dono PJ que define o topo.
        {"$set": {
            "raizes": {"$let": {
                "vars": {"cand": {"$setDifference": [
                    {"$setUnion": [["$_id"], corporativos]}, controladas_por_pj]}},
                "in": {"$cond": [{"$gt": [{"$size": "$$cand"}, 0]},
                                 {"$slice": ["$$cand", teto_raizes]}, ["$_id"]]},
            }}
        }},

        # 2b. raiz de verdade é a que não tem dono corporativo **no banco**, não
        #     apenas dentro do trecho percorrido.
        #
        #     Sem esta checagem, um nó do meio da árvore vira raiz sempre que a
        #     profundidade acaba antes de alcançar o dono dele — e a descida a
        #     partir dele devolvia meio grupo já na profundidade 1, como se o
        #     controle de profundidade não fizesse nada. Custa um `$lookup`
        #     indexado por `owned_id`, dentro da mesma ida ao cluster.
        {"$lookup": {
            "from": "ownership",
            "let": {"raizes": "$raizes"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$in": ["$owned_id", "$$raizes"]},
                    {"$eq": ["$owner_type", "corporate"]},
                ]}}},
                {"$group": {"_id": "$owned_id"}},
            ],
            "as": "com_dono",
        }},
        {"$set": {"raizes": {"$let": {
            "vars": {"reais": {"$setDifference": ["$raizes", "$com_dono._id"]}},
            # Sem raiz verdadeira dentro do alcance, desce a partir da própria
            # empresa consultada: é a resposta honesta para "não cheguei ao topo".
            "in": {"$cond": [{"$gt": [{"$size": "$$reais"}, 0]}, "$$reais", ["$_id"]]},
        }}}},
        {"$unset": "com_dono"},

        # 3. desce de todas as raízes de uma vez, no servidor
        {"$lookup": {
            "from": "companies",
            "let": {"raizes": "$raizes"},
            "pipeline": [
                {"$match": {"$expr": {"$in": ["$_id", "$$raizes"]}}},
                {"$graphLookup": _graph_lookup("down", depth, "$_id")},
                {"$project": {"_id": 0, "cadeia": {"$slice": ["$cadeia", teto_arestas]}}},
            ],
            "as": "descidas",
        }},
        {"$set": {"abaixo": {"$reduce": {
            "input": "$descidas.cadeia", "initialValue": [],
            "in": {"$concatArrays": ["$$value", "$$this"]},
        }}}},
        {"$unset": "descidas"},

        # 4. arestas do grupo, projetadas para o que o frontend desenha
        {"$set": {"arestas": {"$map": {
            "input": {"$slice": [{"$concatArrays": ["$acima", "$abaixo"]}, teto_arestas]},
            "as": "e", "in": _ARESTA,
        }}}},
        {"$unset": ["acima", "abaixo"]},

        # 5. conjuntos de ids para hidratar
        {"$set": {
            "ids_empresa": {"$setUnion": [
                ["$_id"],
                {"$map": {"input": "$arestas", "as": "e", "in": "$$e.owned_id"}},
                {"$map": {"input": {"$filter": {"input": "$arestas", "as": "e",
                                                "cond": {"$eq": ["$$e.owner_type", "corporate"]}}},
                          "as": "e", "in": "$$e.owner_id"}},
            ]},
            "ids_pessoa": {"$setUnion": [[], {"$map": {
                "input": {"$filter": {"input": "$arestas", "as": "e",
                                      "cond": {"$eq": ["$$e.owner_type", "individual"]}}},
                "as": "e", "in": "$$e.owner_id"}}]},
        }},

        # 6. hidratação — três `$lookup` no lugar de três `find` em série
        {"$lookup": {
            "from": "companies",
            "let": {"ids": "$ids_empresa"},
            "pipeline": [
                {"$match": {"$expr": {"$in": ["$_id", "$$ids"]}}},
                {"$project": {"cnpj": 1, "razao_social": 1, "uf": 1, "cnae_descricao": 1,
                              "porte": 1, "situacao": 1, "is_holding": 1,
                              "capital_social": 1, "case_id": 1, "credit_status": 1,
                              "advisor_id": 1}},
            ],
            "as": "empresas",
        }},
        {"$lookup": {
            "from": "people",
            "let": {"ids": "$ids_pessoa"},
            "pipeline": [
                {"$match": {"$expr": {"$in": ["$_id", "$$ids"]}}},
                {"$project": {"name": 1, "occupation": 1, "age_band": 1, "income_band": 1}},
            ],
            "as": "pessoas",
        }},
        # Quem atende cada empresa do grupo. Entra aqui porque a pergunta
        # "quem responde por esta conta?" é a primeira que um gerente faz ao ver
        # o grupo na tela — e porque é ela que liga os dois cenários da POV: a
        # cadeia societária e a hierarquia comercial.
        {"$lookup": {
            "from": "advisors",
            "let": {"ids": {"$setUnion": [[], "$empresas.advisor_id"]}},
            "pipeline": [
                {"$match": {"$expr": {"$in": ["$_id", "$$ids"]}}},
                {"$project": {"nome": 1, "papel": 1, "matricula": 1, "reports_to": 1}},
            ],
            "as": "assessores",
        }},
        {"$lookup": {
            "from": "credit_exposure",
            "let": {"ids": "$ids_empresa"},
            "pipeline": [{"$match": {"$expr": {"$in": ["$company_id", "$$ids"]}}}],
            "as": "creditos",
        }},

        {"$project": {
            "cnpj": 1, "razao_social": 1, "arestas": 1, "empresas": 1,
            "pessoas": 1, "creditos": 1, "raizes": 1, "assessores": 1,
        }},
    ]


def economic_group(cnpj: str, depth: int | None = None) -> dict[str, Any]:
    """Grupo econômico e exposição consolidada, a partir de um CNPJ."""
    s = get_settings()
    d = clamp_depth(depth)
    started = time.perf_counter()
    pipeline = _pipeline_grupo(cnpj, d, s.max_nodes * 3, MAX_RAIZES)

    try:
        docs = with_retry(
            lambda: list(get_db().companies.aggregate(
                pipeline,
                maxTimeMS=s.graph_max_time_ms,
            )),
            "grupo econômico",
        )
    except OperationFailure as exc:
        return {
            "found": False,
            "cnpj": cnpj,
            "too_large": True,
            "depth": d,
            "error": _falha(exc),
            "hint": (
                "Reduza a profundidade. `$graphLookup` monta o traversal inteiro num "
                "documento só, com teto de 100 MB — ver LIMITATIONS.md."
            ),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    if not docs:
        return {"found": False, "cnpj": cnpj}
    doc = docs[0]

    # Dedup de arestas: as descidas partem de raízes diferentes e podem cruzar a
    # mesma aresta. Barato aqui, e no servidor custaria um `$group` a mais.
    arestas: list[dict[str, Any]] = []
    vistas: set[tuple[str, str]] = set()
    for e in doc["arestas"]:
        chave = (e["owner_id"], e["owned_id"])
        if chave not in vistas:
            vistas.add(chave)
            arestas.append(e)

    empresas = {c["_id"]: c for c in doc["empresas"]}
    assessores = {a["_id"]: a for a in doc.get("assessores", [])}
    pessoas = {p["_id"]: p for p in doc["pessoas"]}
    creditos = {c["company_id"]: c for c in doc["creditos"]}

    ids_empresa = {doc["_id"]} | set(empresas)
    ids_pessoa = set(pessoas)
    truncado = len(ids_empresa) + len(ids_pessoa) > s.max_nodes

    nodes = []
    for cid in ids_empresa:
        c = empresas.get(cid, {})
        cr = creditos.get(cid)
        nodes.append(
            {
                "id": cid,
                "kind": "company",
                "label": c.get("razao_social", cid[:14]),
                "cnpj": c.get("cnpj"),
                "uf": c.get("uf"),
                "activity": c.get("cnae_descricao"),
                "porte": c.get("porte"),
                "situacao": c.get("situacao"),
                "is_holding": bool(c.get("is_holding")),
                "is_subject": cid == doc["_id"],
                "credit_status": c.get("credit_status"),
                "case_id": c.get("case_id"),
                "advisor_id": c.get("advisor_id"),
                "advisor": (
                    {
                        "id": c["advisor_id"],
                        "nome": assessores[c["advisor_id"]].get("nome"),
                        "papel": assessores[c["advisor_id"]].get("papel"),
                        "matricula": assessores[c["advisor_id"]].get("matricula"),
                    }
                    if c.get("advisor_id") in assessores
                    else None
                ),
                "limite": cr["limite"] if cr else 0.0,
                "utilizado": cr["utilizado"] if cr else 0.0,
                "vencido": cr["vencido"] if cr else 0.0,
                "rating": cr["rating"] if cr else None,
            }
        )
    for pid in ids_pessoa:
        p = pessoas.get(pid, {})
        nodes.append(
            {
                "id": pid,
                "kind": "person",
                "label": p.get("name", pid[:14]),
                "occupation": p.get("occupation"),
                "age_band": p.get("age_band"),
                "income_band": p.get("income_band"),
                "is_subject": False,
            }
        )

    limite = sum(n["limite"] for n in nodes if n["kind"] == "company")
    utilizado = sum(n["utilizado"] for n in nodes if n["kind"] == "company")
    vencido = sum(n["vencido"] for n in nodes if n["kind"] == "company")
    proprio = creditos.get(doc["_id"])

    return {
        "found": True,
        "depth": d,
        "subject": {
            "id": doc["_id"],
            "cnpj": doc["cnpj"],
            "razao_social": doc["razao_social"],
            "limite": proprio["limite"] if proprio else 0.0,
            "utilizado": proprio["utilizado"] if proprio else 0.0,
            "vencido": proprio["vencido"] if proprio else 0.0,
            "rating": proprio["rating"] if proprio else None,
        },
        "nodes": nodes,
        "edges": [
            {
                "from": e["owner_id"],
                "to": e["owned_id"],
                "type": e["owner_type"],
                "percentage": e["percentage"],
                "qualificacao": e.get("qualificacao"),
            }
            for e in arestas
        ],
        "stats": {
            "companies": len(ids_empresa),
            "partners": len(ids_pessoa),
            "edges": len(arestas),
            "with_credit": len(creditos),
            "truncated": truncado,
            "max_nodes": s.max_nodes,
            "round_trips": 1,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        },
        # A consolidação é o número da decisão de crédito: a soma de todo o grupo,
        # não da empresa que pediu.
        "group_exposure": {
            "currency": s.currency,
            "limite": round(limite, 2),
            "utilizado": round(utilizado, 2),
            "vencido": round(vencido, 2),
            "companies_with_credit": len(creditos),
        },
        "query": {
            "pattern": "árvore rasa, consulta pontual por CNPJ",
            "roots": len(doc.get("raizes", [])),
            "max_depth": d,
        },
        "query_details": {
            "operation": "aggregate",
            "namespace": f"{s.db_name}.companies",
            "pipeline": pipeline,
        },
    }
