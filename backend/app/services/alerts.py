"""Alerta em tempo real sobre revisão de crédito, via Change Streams.

## Por que isto importa numa decisão de crédito

Abrir revisão sobre um grupo econômico marca **dezenas de empresas de uma vez**,
numa transação. Quem precisa saber disso não é só a tela que disparou: é a mesa
inteira, o time de monitoramento, o sistema de limites. O caminho comum é uma
rotina noturna que varre `companies` procurando o que mudou.

O change stream faz melhor: a transação confirma, o listener acorda no evento de
cada empresa marcada, agrupa por `case_id` e publica em segundos. Sem polling e
sem job agendado — e o número que ele mostra, o intervalo entre o commit da
transação e a chegada do evento, é a diferença entre os dois modelos medida ao
vivo.

## Detalhes que importam numa demo ao vivo

- o listener roda em thread própria e o `watch` usa `max_await_time_ms`, então o
  loop não bloqueia o shutdown do uvicorn indefinidamente;
- guarda o `resume_token`: se o cursor cair por rede ou failover, retoma de onde
  parou em vez de perder eventos;
- `full_document="updateLookup"` para que o `update` traga o documento inteiro;
- o `$match` roda **no servidor**, sobre `updateDescription.updatedFields`: só
  mudança de `credit_status` sobe o fio. Uma carga em lote toca `companies` sem
  encostar nesse campo e não gera ruído nenhum no SSE.

## Os dois tipos de evento, e por que o segundo existe

`review_opened` é o alerta: um caso novo, com quantas empresas entraram sob
revisão e a exposição que elas somam. `review_closed` é a contrapartida — o caso
foi encerrado e as empresas voltaram a `active`.

O segundo existe porque o A/B é o que prova que o listener lê o estado em vez de
disparar sozinho: o mesmo mecanismo publica os dois lados, e encerrar a revisão
na frente do cliente devolve evento na mesma janela de tempo.

## Coalescência

`update_many` sobre 40 empresas gera 40 eventos de change stream, não um. Publicar
os 40 seria ruído. O listener agrupa por `case_id` numa janela curta
(`JANELA_COALESCE_S`) e publica **um** evento por caso, com a contagem real —
que é também o número que o apresentador quer ver.
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

# Janela de coalescência: `update_many` sobre um grupo de 40 empresas chega como
# 40 eventos em poucos milissegundos. Meio segundo é folgado para juntá-los e
# curto o bastante para a tela ainda parecer instantânea.
JANELA_COALESCE_S = 0.5


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
            "collection": "companies",
            "last_error": None,
        }
        # empresa -> caso aberto. O evento de encerramento chega com `case_id`
        # em `removedFields`, sem valor: é aqui que o valor é recuperado.
        self._ultimo_caso: dict[str, str] = {}

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
        # Casos vistos na janela atual: case_id -> acumulador. Ver a seção
        # "Coalescência" no topo do módulo.
        pendentes: dict[str, dict[str, Any]] = {}
        while not self._stop.is_set():
            try:
                db = get_db()
                kwargs: dict[str, Any] = {
                    # O filtro roda no servidor. `updatedFields` só traz o que o
                    # `$set`/`$unset` tocou, então isto é exatamente "mudou o
                    # estado de revisão de crédito desta empresa" — e uma carga
                    # em lote, que não toca o campo, não sobe o fio.
                    "pipeline": [
                        {
                            "$match": {
                                "operationType": "update",
                                "$or": [
                                    {"updateDescription.updatedFields.credit_status": {"$exists": True}},
                                    {"updateDescription.removedFields": "case_id"},
                                ],
                            }
                        }
                    ],
                    "full_document": "updateLookup",
                    "max_await_time_ms": 250,
                }
                if resume_token:
                    kwargs["resume_after"] = resume_token
                with db.companies.watch(**kwargs) as stream:
                    self.state["running"] = True
                    self.state["last_error"] = None
                    log.info("change stream ativo em companies")
                    while not self._stop.is_set():
                        change = stream.try_next()
                        if change is not None:
                            resume_token = stream.resume_token
                            self.state["events_seen"] += 1
                            self._acumula(change, pendentes)
                        self._descarrega(pendentes)
            except PyMongoError as exc:
                self.state["running"] = False
                self.state["last_error"] = str(exc)
                log.warning("change stream caiu (%s); retomando em 2s", exc)
                pendentes.clear()
                time.sleep(2)
        self.state["running"] = False

    # ---- regra do alerta ----
    def _acumula(self, change: dict[str, Any], pendentes: dict[str, dict[str, Any]]) -> None:
        """Junta os eventos de uma mesma transação num acumulador por caso."""
        doc = change.get("fullDocument") or {}
        campos = (change.get("updateDescription") or {}).get("updatedFields") or {}
        status = campos.get("credit_status") or doc.get("credit_status")

        # Abrir revisão grava `case_id` junto com o status. Encerrar remove os
        # dois, e aí o `case_id` só existe no evento — não no documento, que já
        # voltou a `active`. Por isso o `$unset` é lido de `removedFields` e o
        # caso, nesse ramo, vem do que o listener já tinha visto abrir.
        removidos = (change.get("updateDescription") or {}).get("removedFields") or []
        encerrando = "case_id" in removidos or status == "active"
        case_id = campos.get("case_id") or doc.get("case_id")
        if encerrando and not case_id:
            # Sem `case_id` no evento de encerramento, agrupa por documento-chave
            # estável: o único caso aberto que continha esta empresa.
            case_id = self._ultimo_caso.get(change["documentKey"]["_id"])
        if not case_id:
            return

        agora = time.monotonic()
        acc = pendentes.get(case_id)
        if acc is None:
            acc = pendentes[case_id] = {
                "case_id": case_id,
                "kind": "review_closed" if encerrando else "review_opened",
                "companies": 0,
                "company_ids": [],
                "primeiro_em": agora,
            }
        acc["ultimo_em"] = agora
        acc["companies"] += 1
        empresa_id = change["documentKey"]["_id"]
        if len(acc["company_ids"]) < 5000:
            acc["company_ids"].append(empresa_id)
        if not encerrando:
            self._ultimo_caso[empresa_id] = case_id
        if acc["companies"] == 1 and doc.get("razao_social"):
            acc["first_company"] = doc["razao_social"]

    def _descarrega(self, pendentes: dict[str, dict[str, Any]]) -> None:
        """Publica os casos cuja janela de coalescência fechou."""
        if not pendentes:
            return
        agora = time.monotonic()
        maduros = [c for c, a in pendentes.items() if agora - a["ultimo_em"] >= JANELA_COALESCE_S]
        for case_id in maduros:
            acc = pendentes.pop(case_id)
            evento = self._monta(acc)
            if not evento:
                continue
            chave = "alerts" if evento["type"] == "review_opened" else "checks_published"
            self.state[chave] += 1
            self._publish(evento)

    def _monta(self, acc: dict[str, Any]) -> dict[str, Any] | None:
        db = get_db()
        agora_iso = datetime.now(timezone.utc).isoformat()
        # Latência real do mecanismo: do primeiro evento da transação até aqui.
        # Inclui a janela de coalescência de propósito — é o tempo que a tela
        # espera de verdade, e apresentar um número menor que o vivido seria
        # mentira de demo.
        stream_ms = round((time.monotonic() - acc["primeiro_em"]) * 1000, 1)

        if acc["kind"] == "review_closed":
            for empresa_id in acc["company_ids"]:
                self._ultimo_caso.pop(empresa_id, None)
            return {
                "type": "review_closed",
                "case_id": acc["case_id"],
                "companies": acc["companies"],
                "stream_ms": stream_ms,
                "at": agora_iso,
            }

        started = time.perf_counter()
        soma = list(
            db.credit_exposure.aggregate(
                [
                    {"$match": {"company_id": {"$in": acc["company_ids"]}}},
                    {
                        "$group": {
                            "_id": None,
                            "limite": {"$sum": "$limite"},
                            "vencido": {"$sum": "$vencido"},
                        }
                    },
                ]
            )
        )
        lookup_ms = round((time.perf_counter() - started) * 1000, 1)
        totais = soma[0] if soma else {}
        caso = db.credit_decisions.find_one({"_id": acc["case_id"]}, {"reason": 1, "analyst": 1})

        alerta = {
            "type": "review_opened",
            "case_id": acc["case_id"],
            "companies": acc["companies"],
            "first_company": acc.get("first_company"),
            "reason": (caso or {}).get("reason"),
            "analyst": (caso or {}).get("analyst"),
            # O número que muda a decisão: a exposição inteira que a marcação
            # colocou sob revisão de uma vez.
            "under_review_limite": totais.get("limite", 0.0),
            "under_review_vencido": totais.get("vencido", 0.0),
            "lookup_ms": lookup_ms,
            "stream_ms": stream_ms,
            "at": agora_iso,
        }
        db.ownership_alerts.replace_one(
            {"_id": f"alert_{acc['case_id']}"},
            {"_id": f"alert_{acc['case_id']}", **alerta, "created_at": datetime.now(timezone.utc)},
            upsert=True,
        )
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
