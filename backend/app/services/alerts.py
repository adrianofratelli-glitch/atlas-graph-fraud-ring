"""Alerta em tempo real sobre alteração societária, via Change Streams.

## Por que isto importa numa decisão de crédito

Uma decisão de crédito é tomada sobre a fotografia de um grupo econômico num
instante. O grupo, porém, muda: uma alteração contratual registrada na Junta
Comercial acrescenta uma controlada, e a exposição consolidada que sustentou a
decisão deixa de valer — sem que ninguém na mesa saiba.

O caminho comum para isso é uma rotina noturna que recalcula grupos. O change
stream faz melhor: a participação nova entra em `ownership`, o listener acorda,
verifica se alguma das pontas está sob revisão de crédito e publica o alerta em
segundos. Sem polling e sem job agendado.

## Detalhes que importam numa demo ao vivo

- o listener roda em thread própria e o `watch` usa `max_await_time_ms`, então o
  loop não bloqueia o shutdown do uvicorn indefinidamente;
- guarda o `resume_token`: se o cursor cair por rede ou failover, retoma de onde
  parou em vez de perder eventos;
- `full_document="updateLookup"` para que um `update` também traga o documento.

## Os dois tipos de evento, e por que o segundo existe

`group_changed` é o alerta. `checked` é a alteração que **não** tocou grupo
nenhum sob revisão: diz quantas empresas foram verificadas e em quantos
milissegundos.

O segundo existe porque silêncio não pode parecer demo travada. Injetar uma
alteração num grupo liberado é metade do A/B que prova que o alerta lê o estado
em vez de disparar sozinho — e sem evento na tela o apresentador fica sem prova de
que o listener sequer acordou. Ambos são limitados a alterações simuladas: uma
carga em lote insere milhões de arestas, e publicar cada uma afogaria o SSE.
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

log = logging.getLogger(__name__)

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
            "checks_published": 0,
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
                with db.ownership.watch(**kwargs) as stream:
                    self.state["running"] = True
                    self.state["last_error"] = None
                    log.info("change stream ativo em ownership")
                    while not self._stop.is_set():
                        change = stream.try_next()
                        if change is None:
                            continue
                        resume_token = stream.resume_token
                        self.state["events_seen"] += 1
                        evento = self._evaluate(change)
                        if evento:
                            chave = "alerts" if evento["type"] == "group_changed" else "checks_published"
                            self.state[chave] += 1
                            self._publish(evento)
            except PyMongoError as exc:
                self.state["running"] = False
                self.state["last_error"] = str(exc)
                log.warning("change stream caiu (%s); retomando em 2s", exc)
                time.sleep(2)
        self.state["running"] = False

    # ---- regra do alerta ----
    def _evaluate(self, change: dict[str, Any]) -> dict[str, Any] | None:
        doc = change.get("fullDocument")
        if not doc or not doc.get("simulated"):
            # Só alteração da demo. Uma carga em lote insere milhões de arestas e
            # publicar cada uma afogaria o SSE em ruído.
            return None

        db = get_db()
        empresas = [c for c in (doc.get("owner_id"), doc.get("owned_id")) if c]
        if not empresas:
            return None

        started = time.perf_counter()
        sob_revisao = list(
            db.companies.find(
                {"_id": {"$in": empresas}, "credit_status": "under_review"},
                {"razao_social": 1, "cnpj": 1, "case_id": 1},
            )
        )
        lookup_ms = round((time.perf_counter() - started) * 1000, 1)
        agora = datetime.now(timezone.utc).isoformat()

        if not sob_revisao:
            return {
                "type": "checked",
                "edge_id": doc["_id"],
                "checked_companies": len(empresas),
                "percentage": doc.get("percentage"),
                "lookup_ms": lookup_ms,
                "at": agora,
            }

        adquirida = db.companies.find_one(
            {"_id": doc["owned_id"]}, {"razao_social": 1, "cnpj": 1}
        )
        exposicao = db.credit_exposure.find_one({"company_id": doc["owned_id"]})
        alerta = {
            "type": "group_changed",
            "edge_id": doc["_id"],
            "case_id": sob_revisao[0].get("case_id"),
            "acquirer": sob_revisao[0].get("razao_social"),
            "acquired": (adquirida or {}).get("razao_social"),
            "acquired_cnpj": (adquirida or {}).get("cnpj"),
            "percentage": doc.get("percentage"),
            # O número que muda a decisão: a exposição que entrou no grupo depois
            # de a decisão ter sido tomada.
            "added_limite": (exposicao or {}).get("limite", 0.0),
            "added_vencido": (exposicao or {}).get("vencido", 0.0),
            "operation": change.get("operationType"),
            "lookup_ms": lookup_ms,
            "at": agora,
        }
        db.ownership_alerts.replace_one({"_id": f"alert_{doc['_id']}"},
                                        {"_id": f"alert_{doc['_id']}", **alerta,
                                         "created_at": datetime.now(timezone.utc)},
                                        upsert=True)
        return alerta


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
