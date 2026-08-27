"""API da POV de análise de grafos.

Camadas: `app/db/` fala com o Mongo, `app/services/` orquestra, este arquivo só
expõe HTTP e traduz exceção em código de status. Nenhuma rota importa pymongo.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from pymongo.errors import PyMongoError

from app.config import get_settings
from app.db import exposure, graph, investigation, search
from app.db.client import get_db
from app.services import demo
from app.services.alerts import hub, sse_stream

s = get_settings()
logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("graph-fraud-ring")


@asynccontextmanager
async def lifespan(_: FastAPI):
    hub.start()
    yield
    hub.stop()


app = FastAPI(title="Atlas Graph Fraud Ring", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://127.0.0.1:{os.getenv('FRONTEND_PORT', 5350)}", f"http://localhost:{os.getenv('FRONTEND_PORT', 5350)}"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- modelos
# Os corpos de POST passam por schema, não por checagem manual. Duas falhas reais
# encontradas em `tests/test_resilience.py` vinham exatamente disso: `limit: -1`
# chegava ao `$vectorSearch` e virava 500, e `person_ids` como string era iterado
# caractere a caractere até a transação abortar com 409.


class SimilarReasonsIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=50)
    ring_only: bool = False
    # Escopo do painel: "rede" restringe às redes que estão na tela, "base" varre
    # tudo. `ring_ids` é filtro do índice vetorial, então o corte é no ANN.
    scope: Literal["rede", "base"] = "rede"
    ring_ids: list[str] | None = Field(default=None, max_length=50)

    @field_validator("text")
    @classmethod
    def nao_so_espaco(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("`text` cannot be whitespace only")
        return v


class ExposureIn(BaseModel):
    person_ids: list[str] = Field(min_length=1, max_length=2000)


class SearchPeopleIn(BaseModel):
    q: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)
    scope: Literal["rede", "base"] = "base"
    person_ids: list[str] | None = Field(default=None, max_length=2000)
    ring_ids: list[str] | None = Field(default=None, max_length=50)

    @field_validator("q")
    @classmethod
    def q_nao_so_espaco(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("`q` cannot be whitespace only")
        return v


class FlagIn(BaseModel):
    person_ids: list[str] = Field(min_length=1, max_length=5000)
    reason: str = Field(default="rede identificada por traversal", max_length=500)
    analyst: str = Field(default="demo", max_length=120)


class SimulateIn(BaseModel):
    account_id: str | None = None
    amount: float | None = Field(default=None, ge=0, le=10_000_000)
    # A rede que está na tela. Permite injetar a transação **sem** nada marcado,
    # que é a metade "não alerta" do teste A/B do change stream.
    person_ids: list[str] | None = Field(default=None, max_length=5000)


# --------------------------------------------------------------------------- health
@app.get("/health/live")
def live() -> dict:
    """Liveness puro: o processo está de pé. Não toca no banco."""
    return {"status": "ok"}


@app.get("/health")
def health() -> dict:
    """Checagem profunda. É este o endpoint do checklist pré-demo.

    Valida conexão, presença dos índices de busca e a latência de um
    `$graphLookup` de referência — os três jeitos de a demo falhar em público.
    """
    out: dict = {"status": "ok", "checks": {}}

    try:
        started = time.perf_counter()
        get_db().command("ping")
        out["checks"]["mongodb"] = {"ok": True, "ping_ms": round((time.perf_counter() - started) * 1000, 1)}
    except PyMongoError as exc:
        out["status"] = "degraded"
        out["checks"]["mongodb"] = {"ok": False, "error": str(exc)}
        return out

    db = get_db()
    counts = {c: db[c].estimated_document_count() for c in ["people", "accounts", "transactions", "connections", "rings"]}
    out["checks"]["counts"] = counts
    if counts["connections"] == 0:
        out["status"] = "degraded"
        out["checks"]["counts_note"] = "connections vazia — rode data-generator/materialize_connections.py"

    idx = {
        s.search_index: search.index_status("people", s.search_index),
        s.vector_index: search.index_status("transactions", s.vector_index),
    }
    out["checks"]["search_indexes"] = idx
    if any(v != "READY" for v in idx.values()):
        out["status"] = "degraded" if out["status"] == "ok" else out["status"]

    ring = db.rings.find_one()
    if ring:
        probe = graph.expand_network(ring["leader"], depth=2)
        out["checks"]["graphlookup_probe"] = {
            "ok": probe.get("found", False),
            "depth": 2,
            "elapsed_ms": probe.get("stats", {}).get("elapsed_ms"),
            "nodes": probe.get("stats", {}).get("nodes"),
        }
    else:
        out["status"] = "degraded"
        out["checks"]["graphlookup_probe"] = {"ok": False, "error": "sem ground truth em `rings`"}

    out["checks"]["change_stream"] = hub.state
    return out


# --------------------------------------------------------------------------- demo
@app.get("/api/entry-points")
def entry_points():
    return demo.entry_points()


# --------------------------------------------------------------------------- grafo
@app.get("/api/network/{person_id}")
def network(
    person_id: str,
    depth: int | None = Query(None, description=f"limitado a {s.depth_cap} pelo backend"),
    edge_types: str | None = Query(None, description="lista separada por vírgula"),
    prune_hubs: bool = Query(True),
):
    # `edge_types` ausente = todos os tipos. `edge_types=` presente e vazio =
    # nenhum tipo marcado, que precisa devolver grafo vazio.
    types = (
        [t.strip() for t in edge_types.split(",") if t.strip()]
        if edge_types is not None
        else None
    )
    if types and not set(types) <= set(graph.ALL_EDGE_TYPES):
        raise HTTPException(400, f"valid edge types: {graph.ALL_EDGE_TYPES}")
    try:
        result = graph.expand_network(person_id, depth=depth, edge_types=types, prune_hubs=prune_hubs)
    except PyMongoError as exc:
        raise HTTPException(503, f"MongoDB unavailable: {exc}") from exc
    if result.get("too_large"):
        # Não é erro do cliente nem falha do servidor: é o traversal pedido não
        # caber. A resposta carrega o que fazer a respeito.
        raise HTTPException(
            503,
            detail={
                "feature": "graph_traversal",
                "error": result["error"],
                "hint": result["hint"],
                "depth": result["depth"],
                "elapsed_ms": result["elapsed_ms"],
            },
        )
    if not result.get("found"):
        raise HTTPException(404, f"person `{person_id}` not found")
    return result


@app.get("/api/network-by-device/{account_id}")
def network_by_device(account_id: str, depth: int | None = Query(None)):
    """Padrão A, para comparação lado a lado com `/api/network`."""
    try:
        result = graph.expand_by_shared_device(account_id, depth=depth)
    except PyMongoError as exc:
        raise HTTPException(503, f"MongoDB unavailable: {exc}") from exc
    if not result.get("found"):
        raise HTTPException(404, f"no transaction for account `{account_id}`")
    return result


@app.get("/api/hops")
def hops(source: str, target: str, max_depth: int | None = Query(None)):
    return graph.hops_between(source, target, max_depth=max_depth)


# --------------------------------------------------------------------------- busca
@app.get("/api/search/people")
def search_people(q: str, limit: int = Query(10, le=50)):
    """Busca sem escopo. Mantida para `mongosh`, curl e os testes."""
    if not q.strip():
        raise HTTPException(400, "empty `q` parameter")
    try:
        return search.resolve_entity(q, limit=limit)
    except search.IndexUnavailable as exc:
        raise HTTPException(503, detail={"feature": "atlas_search", "index": exc.index_name, "status": exc.status}) from exc


@app.post("/api/search/people")
def search_people_scoped(payload: SearchPeopleIn):
    """Busca ciente do grafo na tela: escopa e/ou anota cada resultado."""
    if not payload.q.strip():
        raise HTTPException(400, "empty `q` parameter")
    try:
        return search.resolve_entity(
            payload.q.strip(),
            limit=payload.limit,
            person_ids=payload.person_ids,
            ring_ids=payload.ring_ids,
            scope=payload.scope,
        )
    except search.IndexUnavailable as exc:
        raise HTTPException(503, detail={"feature": "atlas_search", "index": exc.index_name, "status": exc.status}) from exc


@app.post("/api/search/similar-reasons")
def similar_reasons(payload: SimilarReasonsIn):
    try:
        return search.similar_reasons(
            payload.text.strip(),
            limit=payload.limit,
            ring_only=payload.ring_only,
            ring_ids=payload.ring_ids,
            scope=payload.scope,
        )
    except search.IndexUnavailable as exc:
        raise HTTPException(503, detail={"feature": "vector_search", "index": exc.index_name, "status": exc.status}) from exc


# --------------------------------------------------------------------------- ação
@app.post("/api/investigation/flag")
def flag(payload: FlagIn):
    result = investigation.flag_network(
        payload.person_ids, reason=payload.reason, analyst=payload.analyst
    )
    if not result.get("ok"):
        # O `case_id` vai junto: a tela precisa poder oferecer "encerrar aquele
        # caso" em vez de só dizer que não deu.
        raise HTTPException(
            409,
            detail={
                "error": result.get("error", "transaction aborted"),
                "case_id": result.get("case_id"),
                "already_open": result.get("already_open", False),
            },
        )
    return result


@app.post("/api/investigation/close/{case_id}")
def close(case_id: str):
    result = investigation.close_case(case_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error"))
    return result


@app.post("/api/exposure")
def network_exposure(payload: ExposureIn):
    """Quanto dinheiro passou pela rede que está na tela.

    Endpoint separado de propósito, chamado depois que o grafo já desenhou: a
    agregação sobre `transactions` custa uma ida a mais ao cluster, e pendurá-la
    na expansão atrasaria o passo que o apresentador está mostrando ao vivo.
    """
    result = exposure.network_exposure(payload.person_ids)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "falha ao calcular exposição"))
    return result


@app.get("/api/investigation/case/{case_id}")
def case(case_id: str):
    """O caso aberto, com o antes/depois das contas que a transação ACID mudou."""
    result = investigation.case_detail(case_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error"))
    return result


@app.get("/api/investigation/coaf/{case_id}")
def coaf(case_id: str):
    """Comunicação de operação suspeita, montada do que a transação já gravou.

    Circular BCB 3.978/2020 (COAF, 24h), Resolução BCB 6/2023 (MED) e o registro
    de tratamento da LGPD. Nada é integrado: o ponto é que o documento sai inteiro
    da auditoria que commitou junto com o bloqueio.
    """
    result = investigation.coaf_report(case_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error"))
    return result


@app.post("/api/demo/reset")
def reset():
    return investigation.reset_all()


# --------------------------------------------------------------------------- tempo real
@app.post("/api/demo/simulate-transaction")
def simulate(payload: SimulateIn = SimulateIn()):
    result = demo.simulate_transaction(
        payload.account_id, payload.amount, person_ids=payload.person_ids
    )
    if not result.get("ok"):
        raise HTTPException(409, result.get("error"))
    return result


@app.post("/api/demo/link-accounts")
def link_accounts(payload: dict | None = None):
    """Cria o vínculo ao vivo: duas pessoas sem relação passam a dividir um device.

    A aresta não é escrita por este endpoint — ele só insere as transações. Quem
    materializa é o change stream, em `app/services/edge_maintenance.py`.
    """
    result = demo.link_two_accounts()
    if not result.get("ok"):
        raise HTTPException(409, result.get("error"))
    return result


@app.get("/api/connections/between")
def connections_between(a: str, b: str):
    """Existe aresta entre duas pessoas? Usado para provar o antes/depois."""
    db = get_db()
    arestas = list(db.connections.find({"from": a, "to": b}, {"type": 1, "weight": 1, "source": 1}))
    return {"a": a, "b": b, "connected": bool(arestas), "edges": arestas}


@app.get("/api/alerts/stream")
def alerts_stream():
    q = hub.subscribe()

    def gen():
        try:
            yield from sse_stream(q)
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/alerts/recent")
def alerts_recent(limit: int = Query(20, le=100)):
    docs = list(get_db().alerts.find().sort("created_at", -1).limit(limit))
    return {"alerts": docs, "listener": hub.state}
