"""Alerta em tempo real via Change Streams.

Uma transação nova entra em `transactions`. O listener acorda, verifica se
alguma das pontas pertence a uma rede já sinalizada e, se sim, publica um alerta
para os clientes SSE conectados.

Detalhes que importam numa demo ao vivo:

- o listener roda em thread própria e o `watch` usa `max_await_time_ms`, então o
  loop não bloqueia o shutdown do uvicorn indefinidamente;
- ele guarda o `resume_token`: se o cursor cair (rede, failover), retoma de onde
  parou em vez de perder eventos;
- `full_document="updateLookup"` para que um `update` também traga o documento.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterator

from pymongo.errors import PyMongoError

from app.db.client import get_db
from app.services import edge_maintenance

log = logging.getLogger(__name__)

# Fila por assinante SSE. Limitada de propósito: um cliente lento não pode fazer
# o listener crescer memória sem teto.
QUEUE_MAX = 200


class AlertHub:
    def __init__(self) -> None:
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state: dict[str, Any] = {
            "running": False,
            "events_seen": 0,
            "alerts": 0,
            "edges_materialized": 0,
            "last_error": None,
        }

    # ---- assinatura ----
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    log.warning("assinante SSE lento; evento descartado para ele")

    # ---- ciclo de vida ----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="change-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        resume_token = None
        while not self._stop.is_set():
            try:
                db = get_db()
                kwargs: dict[str, Any] = {
                    "pipeline": [{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}}}],
                    "full_document": "updateLookup",
                    "max_await_time_ms": 1000,
                }
                if resume_token:
                    kwargs["resume_after"] = resume_token
                with db.transactions.watch(**kwargs) as stream:
                    self.state["running"] = True
                    self.state["last_error"] = None
                    log.info("change stream ativo em transactions")
                    while not self._stop.is_set():
                        change = stream.try_next()
                        if change is None:
                            continue
                        resume_token = stream.resume_token
                        self.state["events_seen"] += 1

                        # Duas responsabilidades no mesmo cursor, de propósito:
                        # abrir um segundo change stream dobraria a carga no oplog
                        # para ler exatamente os mesmos eventos.
                        try:
                            manutencao = edge_maintenance.maintain(change)
                        except PyMongoError as exc:
                            # Falha de manutenção não pode derrubar o alerta: o
                            # alerta é o que está na tela, a aresta o batch refaz.
                            log.warning("manutenção de aresta falhou: %s", exc)
                            manutencao = None
                        if manutencao and manutencao.get("edges_written"):
                            self.state["edges_materialized"] += manutencao["edges_written"]
                            self._publish({"type": "edge_materialized", **manutencao,
                                           "at": datetime.now(timezone.utc).isoformat()})

                        alert = self._evaluate(change)
                        if alert:
                            self.state["alerts"] += 1
                            self._publish(alert)
            except PyMongoError as exc:
                self.state["running"] = False
                self.state["last_error"] = str(exc)
                log.warning("change stream caiu (%s); retomando em 2s", exc)
                time.sleep(2)
        self.state["running"] = False

    # ---- regra do alerta ----
    def _evaluate(self, change: dict[str, Any]) -> dict[str, Any] | None:
        doc = change.get("fullDocument")
        if not doc:
            return None
        db = get_db()
        account_ids = [a for a in (doc.get("from_account"), doc.get("to_account")) if a]
        if not account_ids:
            return None

        started = time.perf_counter()
        hits = list(
            db.accounts.find(
                {"_id": {"$in": account_ids}, "status": "under_investigation"},
                {"person_id": 1, "case_id": 1, "ring_id": 1},
            )
        )
        lookup_ms = round((time.perf_counter() - started) * 1000, 1)
        if not hits:
            return None

        # Upsert, não insert: um `update` na mesma transação passaria de novo por
        # aqui e a chave duplicada mataria a thread do listener no meio da demo.
        db.alerts.replace_one(
            {"_id": f"alert_{doc['_id']}"},
            {
                # _id textual: o payload do alerta é serializado como JSON para o
                # SSE e para /api/alerts/recent, e ObjectId não é serializável.
                "_id": f"alert_{doc['_id']}",
                "transaction_id": doc["_id"],
                "accounts": [h["_id"] for h in hits],
                "case_id": hits[0].get("case_id"),
                "ring_id": hits[0].get("ring_id"),
                "amount": doc.get("amount"),
                "created_at": datetime.now(timezone.utc),
            },
            upsert=True,
        )
        return {
            "type": "ring_touch",
            "transaction_id": doc["_id"],
            "amount": doc.get("amount"),
            "reason_text": doc.get("reason_text"),
            "matched_accounts": [h["_id"] for h in hits],
            "case_id": hits[0].get("case_id"),
            "ring_id": hits[0].get("ring_id"),
            "operation": change.get("operationType"),
            "lookup_ms": lookup_ms,
            "at": datetime.now(timezone.utc).isoformat(),
        }


hub = AlertHub()


def sse_stream(q: queue.Queue) -> Iterator[str]:
    """Gerador SSE. O heartbeat evita que proxy/navegador derrube a conexão ociosa."""
    yield ": conectado\n\n"
    while True:
        try:
            payload = q.get(timeout=15)
        except queue.Empty:
            yield ": heartbeat\n\n"
            continue
        import json

        yield f"data: {json.dumps(payload, default=str)}\n\n"
