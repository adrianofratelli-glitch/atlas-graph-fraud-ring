"""Traversal de grafo. É o núcleo da POV.

Dois padrões convivem, e a diferença entre eles é parte do argumento técnico:

- **Padrão B** (`connections`): arestas materializadas, com tipo e peso. Barato,
  podável via `restrictSearchWithMatch`. É o padrão da investigação.
- **Padrão A** (`transactions.device_id`): aresta inferida em tempo de query, sem
  coleção intermediária. Mais fiel ao dado operacional, mais caro.

Ambos estão expostos para que a demo mostre a diferença medida, não afirmada.
"""
from __future__ import annotations

import time
from typing import Any, Literal

from pymongo.errors import ExecutionTimeout, OperationFailure

from app.config import get_settings
from app.db.client import get_db, with_retry

EdgeType = Literal["shares_device", "shares_address", "same_pix_counterparty"]
ALL_EDGE_TYPES: list[str] = ["shares_device", "shares_address", "same_pix_counterparty"]


def _classificar_falha(exc: OperationFailure) -> str:
    """Traduz a falha do servidor no que o apresentador precisa saber."""
    if isinstance(exc, ExecutionTimeout):
        return "timeout: the traversal did not finish within the configured ceiling"
    msg = str(exc)
    if "exceeds" in msg and "bytes" in msg:
        return "100 MB limit on the $graphLookup output document"
    return f"aggregation failed: {msg.split(', full error')[0]}"


def clamp_depth(requested: int | None) -> int:
    """O frontend sugere; o backend decide. Nunca acima do cap."""
    s = get_settings()
    if requested is None:
        return s.default_depth
    return max(0, min(int(requested), s.depth_cap))


def _restrict(edge_types: list[str] | None, prune_hubs: bool) -> dict[str, Any] | None:
    """Monta o `restrictSearchWithMatch`, que é onde a poda acontece.

    Podar hub por peso é o mesmo remédio que um banco de grafo dedicado usa; a
    diferença é que aqui ele é um filtro de documento comum.
    """
    clauses: list[dict[str, Any]] = []
    # `None` (parâmetro ausente) e `[]` (nenhum tipo marcado) são coisas
    # diferentes, e tratá-los igual era um bug visível na demo: desmarcar as três
    # caixas de aresta devolvia o grafo INTEIRO em vez de nenhum vínculo — o
    # oposto do que o apresentador acabou de dizer que ia acontecer.
    if edge_types is not None and set(edge_types) != set(ALL_EDGE_TYPES):
        clauses.append({"type": {"$in": list(edge_types)}})
    if prune_hubs:
        clauses.append({"weight": {"$lte": get_settings().hub_threshold}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def expand_network(
    person_id: str,
    depth: int | None = None,
    edge_types: list[str] | None = None,
    prune_hubs: bool = True,
) -> dict[str, Any]:
    """Padrão B: expande a rede de uma pessoa por arestas explícitas."""
    s = get_settings()
    d = clamp_depth(depth)
    graph_lookup: dict[str, Any] = {
        "from": "connections",
        "startWith": "$_id",
        "connectFromField": "to",
        "connectToField": "from",
        "as": "network",
        "maxDepth": d,
        "depthField": "hops",
    }
    restrict = _restrict(edge_types, prune_hubs)
    if restrict:
        graph_lookup["restrictSearchWithMatch"] = restrict

    pipeline = [
        {"$match": {"_id": person_id}},
        {"$graphLookup": graph_lookup},
        {
            "$project": {
                "name": 1,
                "ring_id": 1,
                "risk_flags": 1,
                "edges": {
                    "$map": {
                        "input": {"$slice": ["$network", s.max_nodes * 3]},
                        "as": "e",
                        "in": {
                            "from": "$$e.from",
                            "to": "$$e.to",
                            "type": "$$e.type",
                            "weight": "$$e.weight",
                            "hops": "$$e.hops",
                        },
                    }
                },
                "edge_count_total": {"$size": "$network"},
            }
        },
    ]

    started = time.perf_counter()
    try:
        docs = with_retry(
            lambda: list(
                get_db().people.aggregate(
                    pipeline, allowDiskUse=True, maxTimeMS=s.graph_max_time_ms
                )
            ),
            "expand_network",
        )
    except OperationFailure as exc:
        # Dois modos de falha do traversal grande, ambos com resposta acionável:
        # estourar o teto de 100 MB do documento de saída, e estourar o tempo.
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "found": False,
            "person_id": person_id,
            "too_large": True,
            "depth": d,
            "elapsed_ms": elapsed_ms,
            "error": _classificar_falha(exc),
            "hint": (
                "Reduce the depth, turn on hub pruning, or restrict the edge types. "
                "`$graphLookup` builds the whole traversal into a single document, and that "
                "document is capped at 100 MB — see LIMITATIONS.md §5."
            ),
        }
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    if not docs:
        return {"found": False, "person_id": person_id, "elapsed_ms": elapsed_ms}

    root = docs[0]
    return _assemble(root, person_id, d, elapsed_ms, restrict, prune_hubs, edge_types)


def _assemble(
    root: dict[str, Any],
    person_id: str,
    depth: int,
    elapsed_ms: float,
    restrict: dict | None,
    prune_hubs: bool,
    edge_types: list[str] | None,
) -> dict[str, Any]:
    s = get_settings()
    edges = root.get("edges", [])

    node_ids: list[str] = [person_id]
    seen = {person_id}
    hop_of = {person_id: 0}
    for e in edges:
        for side in ("from", "to"):
            nid = e[side]
            if nid not in seen:
                seen.add(nid)
                node_ids.append(nid)
            hop_of[nid] = min(hop_of.get(nid, 99), e["hops"] + 1)
    hop_of[person_id] = 0

    truncated = len(node_ids) > s.max_nodes
    if truncated:
        # Corta pelos saltos mais próximos: perder a periferia é menos danoso do
        # que entregar um grafo que o navegador não consegue desenhar.
        node_ids = sorted(node_ids, key=lambda n: hop_of[n])[: s.max_nodes]
        keep = set(node_ids)
        edges = [e for e in edges if e["from"] in keep and e["to"] in keep]

    people = {
        p["_id"]: p
        for p in with_retry(
            lambda: list(
                get_db().people.find(
                    {"_id": {"$in": node_ids}},
                    {
                        "name": 1,
                        "ring_id": 1,
                        "risk_flags": 1,
                        "addresses.city": 1,
                        "case_id": 1,
                        "created_at": 1,
                    },
                )
            ),
            "hidratação de nós",
        )
    }

    # As contas viajam junto com o nó por dois motivos de demonstração:
    #
    # 1. `status` é o que a transação ACID muda. Sem ele na tela, marcar a rede é
    #    uma linha de texto e o efeito da ação fica invisível no grafo.
    # 2. `opened_at` é evidência: quatro contas de CPFs diferentes abertas na
    #    mesma semana, operando do mesmo dispositivo, é fazenda de mulas. O
    #    analista lê isso sem precisar que o apresentador narre.
    contas: dict[str, list[dict[str, Any]]] = {}
    for a in with_retry(
        lambda: list(
            get_db().accounts.find(
                {"person_id": {"$in": node_ids}},
                {"person_id": 1, "status": 1, "opened_at": 1, "case_id": 1,
                 "account_type": 1, "pix_key_type": 1},
            )
        ),
        "hidratação de contas",
    ):
        contas.setdefault(a["person_id"], []).append(a)

    nodes = []
    for nid in node_ids:
        pessoa = people.get(nid, {})
        minhas = contas.get(nid, [])
        marcadas = [c for c in minhas if c.get("status") == "under_investigation"]
        aberturas = sorted(c["opened_at"] for c in minhas if c.get("opened_at"))
        nodes.append(
            {
                "id": nid,
                "label": pessoa.get("name", nid[:14]),
                "ring_id": pessoa.get("ring_id"),
                "risk_flags": pessoa.get("risk_flags", []),
                "hops": hop_of.get(nid, 0),
                "is_root": nid == person_id,
                "city": (pessoa.get("addresses") or [{}])[0].get("city"),
                "accounts": len(minhas),
                "account_types": sorted({c.get("account_type") for c in minhas if c.get("account_type")}),
                "first_account_opened_at": aberturas[0].isoformat() if aberturas else None,
                "last_account_opened_at": aberturas[-1].isoformat() if aberturas else None,
                # `flagged` é o que faz o nó aparecer tracejado no canvas.
                "flagged": bool(marcadas),
                "case_id": pessoa.get("case_id") or (marcadas[0].get("case_id") if marcadas else None),
            }
        )

    ring_nodes = sum(1 for n in nodes if n["ring_id"])
    flagged_nodes = sum(1 for n in nodes if n["flagged"])
    # Aresta bidirecional aparece duas vezes; a contagem visual usa o par ordenado.
    unique_edges = {tuple(sorted((e["from"], e["to"]))) + (e["type"],) for e in edges}

    return {
        "found": True,
        "root": {"id": person_id, "name": root.get("name"), "ring_id": root.get("ring_id")},
        "depth": depth,
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "nodes": len(nodes),
            "edges_directed": len(edges),
            "edges_unique": len(unique_edges),
            "edges_before_truncation": root.get("edge_count_total", len(edges)),
            "ring_nodes": ring_nodes,
            "flagged_nodes": flagged_nodes,
            "truncated": truncated,
            "max_nodes": s.max_nodes,
            "elapsed_ms": elapsed_ms,
        },
        "query": {
            "pattern": "B — explicit edges in `connections`",
            "edge_types": edge_types or ALL_EDGE_TYPES,
            "prune_hubs": prune_hubs,
            "hub_threshold": s.hub_threshold,
            "restrict_search_with_match": restrict,
        },
    }


def expand_by_shared_device(account_id: str, depth: int | None = None) -> dict[str, Any]:
    """Padrão A: traversal direto sobre `transactions`, sem coleção de arestas.

    Existe para ser comparado com o Padrão B no mesmo dataset. Note que ele
    devolve transações, não pessoas — a aresta aqui é "mesma transação, mesmo
    dispositivo", e transformar isso em nós de pessoa exigiria os `$lookup` que o
    Padrão B já pagou uma vez, em batch.
    """
    d = clamp_depth(depth)
    pipeline = [
        {"$match": {"from_account": account_id}},
        {"$limit": 1},
        {
            "$graphLookup": {
                "from": "transactions",
                "startWith": "$device_id",
                "connectFromField": "device_id",
                "connectToField": "device_id",
                "as": "network",
                "maxDepth": d,
                "depthField": "hops",
            }
        },
        {
            "$project": {
                "device_id": 1,
                "from_account": 1,
                "reached_transactions": {"$size": "$network"},
                "reached_accounts": {"$size": {"$setUnion": ["$network.from_account", []]}},
            }
        },
    ]
    started = time.perf_counter()
    docs = with_retry(
        lambda: list(get_db().transactions.aggregate(pipeline, allowDiskUse=True)),
        "expand_by_shared_device",
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    if not docs:
        return {"found": False, "account_id": account_id, "elapsed_ms": elapsed_ms}
    out = docs[0]
    out.update(
        {
            "found": True,
            "depth": d,
            "elapsed_ms": elapsed_ms,
            "pattern": "A — shared attribute (device_id) as an implicit edge",
        }
    )
    return out


def hops_between(source: str, target: str, max_depth: int | None = None) -> dict[str, Any]:
    """Quantos saltos separam duas pessoas.

    Isto **não** é shortest-path genérico otimizado: é um BFS a partir de uma
    ponta que reporta o menor `depthField` em que a outra ponta apareceu. Resolve
    a pergunta que a investigação faz de verdade, e a diferença está documentada
    em LIMITATIONS.md §2 — dizer isso na demo vale mais do que escondê-la.
    """
    d = clamp_depth(max_depth)
    pipeline = [
        {"$match": {"_id": source}},
        {
            "$graphLookup": {
                "from": "connections",
                "startWith": "$_id",
                "connectFromField": "to",
                "connectToField": "from",
                "as": "network",
                "maxDepth": d,
                "depthField": "hops",
            }
        },
        {"$project": {"hit": {"$filter": {"input": "$network", "as": "e", "cond": {"$eq": ["$$e.to", target]}}}}},
        {"$project": {"hops": {"$min": "$hit.hops"}, "edges": {"$slice": ["$hit", 5]}}},
    ]
    started = time.perf_counter()
    docs = with_retry(lambda: list(get_db().people.aggregate(pipeline, allowDiskUse=True)), "hops_between")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    if not docs:
        return {"found": False, "elapsed_ms": elapsed_ms}
    hops = docs[0].get("hops")
    return {
        "found": True,
        "source": source,
        "target": target,
        "connected": hops is not None,
        "hops": None if hops is None else hops + 1,
        "searched_to_depth": d,
        "via": docs[0].get("edges", []),
        "elapsed_ms": elapsed_ms,
        "caveat": "BFS with depthField, not optimised shortest-path — see LIMITATIONS.md §2",
    }
