"""Pontos de entrada da demo de crédito.

O roteiro depende de haver sempre um caso bom à mão: uma empresa que, olhada
sozinha, passa na esteira — rating bom, sem atraso, limite modesto — e que
pertence a um grupo com inadimplência relevante alguns níveis acima. Esse caso é
preparado pelo gerador e registrado em `economic_groups`; a tela não sai
procurando por ele ao vivo na frente do cliente.

Há também um controle: uma empresa sem grupo, para o primeiro passo mostrar o
caso comum antes de mostrar o interessante.
"""
from __future__ import annotations

import threading
from typing import Any

from app.db.client import get_db, with_retry

_cache: dict[str, Any] | None = None
_lock = threading.Lock()


def invalidate_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def entry_points(limit: int = 6) -> dict[str, Any]:
    global _cache
    # O lock cobre a checagem do cache E o cálculo inteiro, não só a checagem.
    # Antes disso ele era liberado logo após ver `_cache is None`, e duas
    # requisições concorrentes batendo no cache vazio (típico logo após
    # `POST /api/demo/reset`) disparavam o cálculo caro em paralelo e
    # redundante — exatamente no momento de maior carga no cluster. Mantendo o
    # lock durante todo o cálculo, a segunda requisição espera a primeira
    # terminar e reaproveita o resultado dela.
    with _lock:
        if _cache is not None:
            return _cache
        result = _compute_entry_points(limit)
        _cache = result
        return result


def _compute_entry_points(limit: int) -> dict[str, Any]:
    db = get_db()
    # A lista tem **um grupo por profundidade societária**, do mais raso ao mais
    # fundo. É isso que dá função ao controle de profundidade da tela: cada
    # entrada tem um fundo de árvore diferente, e o apresentador mostra o slider
    # revelando cada vez mais empresas em vez de repetir a mesma resposta.
    #
    # Dentro de cada profundidade a preferência é o setor **construção**.
    #
    # Todo grupo de vitrine tem um setor só, e em quase todos a análise semântica
    # encontra o que precisa. No setor agro, porém, os três CNAEs do grupo
    # ("cultivo de cereais", "moagem de grãos", "criação de bovinos") são
    # genuinamente três negócios diferentes, e o painel — corretamente — não
    # acusa concentração. Abrir a demo por ali é abrir pelo caso em que o
    # argumento não aparece.
    #
    # Construção é o exemplo limpo: "construção de edifícios", "obras de
    # alvenaria" e "serviços de engenharia" são três códigos e um negócio só, e
    # nenhuma palavra em comum entre eles.
    candidatos = with_retry(
        lambda: list(db.economic_groups.find({"showcase": True})),
        "economic_groups",
    )
    candidatos.sort(key=lambda g: (g.get("levels", 0), g.get("sector") != "construcao", g["_id"]))
    por_nivel: dict[int, dict[str, Any]] = {}
    for g in candidatos:
        por_nivel.setdefault(g.get("levels", 0), g)
    grupos = [por_nivel[n] for n in sorted(por_nivel)][:limit]

    solicitantes = []
    for g in grupos:
        emp = db.companies.find_one(
            {"_id": g["applicant_id"]}, {"cnpj": 1, "razao_social": 1, "uf": 1}
        )
        if not emp:
            continue
        cred = db.credit_exposure.find_one({"company_id": g["applicant_id"]})
        solicitantes.append(
            {
                "group_id": g["_id"],
                "company_id": g["applicant_id"],
                "cnpj": emp["cnpj"],
                "razao_social": emp["razao_social"],
                "uf": emp.get("uf"),
                "group_size": g["size"],
                # Profundidade real da árvore deste grupo. A tela mostra para o
                # apresentador saber até onde o slider ainda revela empresa nova.
                "group_levels": g.get("levels"),
                # O que a esteira vê olhando só a empresa: é isto que a demo
                # começa mostrando, para o contraste existir.
                "own_rating": cred["rating"] if cred else None,
                "own_limite": cred["limite"] if cred else 0.0,
                "own_vencido": cred["vencido"] if cred else 0.0,
            }
        )

    # Controle: empresas sem participação societária de PJ, o caso comum.
    #
    # Antes disto era um laço Python sobre até 400 empresas, cada uma disparando
    # um `count_documents` separado em `ownership` só para descobrir se tinha
    # dono corporativo — até 400 idas ao cluster no pior caso. Como o resultado
    # é cacheado, o custo só reaparecia a cada `POST /api/demo/reset`, mas ali é
    # exatamente onde o cluster já está sob carga de reconstrução do dataset.
    #
    # Uma única agregação com `$lookup` correlacionado resolve isso no servidor,
    # no mesmo padrão de `app/db/ownership.py`: para cada candidata, verifica se
    # existe alguma aresta em `ownership` com `owned_id` igual e `owner_type`
    # corporate, e filtra por `$match` no resultado — uma ida ao cluster, não
    # centenas.
    controle = with_retry(
        lambda: list(
            db.companies.aggregate(
                [
                    {"$match": {"is_holding": {"$ne": True}, "situacao": "ATIVA"}},
                    {"$limit": 400},
                    {
                        "$lookup": {
                            "from": "ownership",
                            "let": {"cid": "$_id"},
                            "pipeline": [
                                {
                                    "$match": {
                                        "$expr": {
                                            "$and": [
                                                {"$eq": ["$owned_id", "$$cid"]},
                                                {"$eq": ["$owner_type", "corporate"]},
                                            ]
                                        }
                                    }
                                },
                                {"$limit": 1},
                            ],
                            "as": "dono_corporativo",
                        }
                    },
                    {"$match": {"dono_corporativo": {"$size": 0}}},
                    {"$limit": 3},
                    {"$project": {"cnpj": 1, "razao_social": 1}},
                ]
            )
        ),
        "companies (controle sem dono corporativo)",
    )
    controle = [
        {"company_id": doc["_id"], "cnpj": doc["cnpj"], "razao_social": doc["razao_social"]}
        for doc in controle
    ]

    return {"applicants": solicitantes, "control": controle}
