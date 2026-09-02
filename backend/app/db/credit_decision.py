"""Decisão de crédito sobre o grupo econômico inteiro, em uma transação ACID.

## O argumento

Ou o grupo inteiro entra em revisão, ou nenhuma empresa entra. Um estado
intermediário — metade das empresas do grupo bloqueada para novas operações,
metade liberada, sem registro de decisão coerente — é pior do que não ter
decidido: a mesa de crédito aprova pela porta que ficou aberta, e a auditoria
depois não consegue reconstruir o que foi decidido nem quando.

Esse é exatamente o cenário que uma escrita não-atômica produz, e é por isso que
o escopo da transação cobre as três escritas juntas:

- `companies.credit_status` — o bloqueio operacional que a esteira de crédito lê;
- `credit_exposure.review_flag` — a marca na exposição, que os relatórios leem;
- um documento em `credit_decisions` — o registro de auditoria.

`readConcern: snapshot` e `writeConcern: majority`: a decisão é tomada sobre uma
fotografia consistente do grupo, e só é considerada tomada quando a maioria do
conjunto de réplicas confirmou.

## Por que recusa um segundo caso

Abrir duas revisões sobre as mesmas empresas sobrescreveria o `case_id` e deixaria
a primeira como casca: aberta, sem empresa nenhuma apontando para ela. Para um
processo de crédito isso é pior do que um erro — e recusar também é o
comportamento correto de mesa: não se abrem duas revisões sobre o mesmo grupo,
reabre-se a que existe.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo import ReadPreference
from pymongo.errors import OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.config import get_settings
from app.db.client import get_client, get_db

FLAG = "under_review"


def open_review(
    company_ids: list[str],
    reason: str,
    analyst: str = "demo",
    exposure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not company_ids:
        raise ValueError("no companies provided")

    client = get_client()
    db = get_db()
    s = get_settings()

    case_id = f"credit_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    started = time.perf_counter()

    # Sentinela para carregar o resultado da checagem "já aberto" para fora da
    # transação sem depender do valor de retorno de `with_transaction` (que o
    # pymongo pode reexecutar em caso de erro transiente, e não deve ser usado
    # para decidir o que aconteceu fora da retry loop).
    estado: dict[str, Any] = {}

    def txn(session) -> dict[str, Any]:
        # A checagem de "já existe revisão aberta" precisa estar DENTRO da
        # mesma transação/snapshot que a escrita: se ficar fora, duas
        # requisições concorrentes passam ambas pela checagem antes de
        # qualquer uma escrever `credit_status=under_review`, e a segunda
        # sobrescreve o `case_id` da primeira sem que nenhuma veja erro.
        ja_aberto = db.companies.find_one(
            {"_id": {"$in": company_ids}, "credit_status": FLAG, "case_id": {"$ne": None}},
            {"case_id": 1},
            session=session,
        )
        if ja_aberto:
            estado["already_open"] = ja_aberto["case_id"]
            return {}

        empresas = db.companies.update_many(
            {"_id": {"$in": company_ids}},
            {"$set": {"credit_status": FLAG, "case_id": case_id, "reviewed_at": now}},
            session=session,
        )
        exposicoes = db.credit_exposure.update_many(
            {"company_id": {"$in": company_ids}},
            {"$set": {"review_flag": True, "case_id": case_id}},
            session=session,
        )
        db.credit_decisions.insert_one(
            {
                "_id": case_id,
                "company_ids": company_ids,
                "reason": reason,
                "analyst": analyst,
                "opened_at": now,
                "companies_blocked": empresas.modified_count,
                "exposures_flagged": exposicoes.modified_count,
                "group_exposure": exposure or {},
                "status": "open",
            },
            session=session,
        )
        return {
            "companies_blocked": empresas.modified_count,
            "exposures_flagged": exposicoes.modified_count,
        }

    with client.start_session() as session:
        try:
            result = session.with_transaction(
                txn,
                read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
                read_preference=ReadPreference.PRIMARY,
            )
        except OperationFailure as exc:
            return {
                "ok": False,
                "case_id": None,
                "error": str(exc),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }

    if "already_open" in estado:
        return {
            "ok": False,
            "case_id": estado["already_open"],
            "already_open": True,
            "error": (
                f"já existe uma revisão de crédito aberta ({estado['already_open']}) cobrindo empresas "
                "deste grupo; encerre-a antes de abrir outra"
            ),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    return {
        "ok": True,
        "case_id": case_id,
        "companies": len(company_ids),
        **result,
        "currency": s.currency,
        "read_concern": "snapshot",
        "write_concern": "majority",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def close_review(case_id: str) -> dict[str, Any]:
    """Libera o grupo. O documento da decisão permanece: auditoria não some."""
    db = get_db()
    if not db.credit_decisions.find_one({"_id": case_id}):
        return {"ok": False, "error": "caso não encontrado"}
    client = get_client()
    with client.start_session() as session:

        def txn(s_):
            db.companies.update_many(
                {"case_id": case_id},
                {"$set": {"credit_status": "active"}, "$unset": {"case_id": "", "reviewed_at": ""}},
                session=s_,
            )
            db.credit_exposure.update_many(
                {"case_id": case_id}, {"$unset": {"review_flag": "", "case_id": ""}}, session=s_
            )
            db.credit_decisions.update_one(
                {"_id": case_id}, {"$set": {"status": "closed"}}, session=s_
            )

        session.with_transaction(
            txn,
            read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority"),
            read_preference=ReadPreference.PRIMARY,
        )
    return {"ok": True, "case_id": case_id}


CASE_DETAIL_LIMIT = 200


def case_detail(case_id: str) -> dict[str, Any]:
    db = get_db()
    caso = db.credit_decisions.find_one({"_id": case_id})
    if not caso:
        return {"ok": False, "error": "caso não encontrado"}
    total = db.companies.count_documents({"case_id": case_id})
    empresas = list(
        db.companies.find(
            {"case_id": case_id},
            {"razao_social": 1, "cnpj": 1, "credit_status": 1, "is_holding": 1},
        ).limit(CASE_DETAIL_LIMIT)
    )
    return {
        "ok": True,
        "case": {**caso, "opened_at": caso["opened_at"].isoformat()},
        "companies": [{**c, "status_before": "active"} for c in empresas],
        "stats": {
            "total": total,
            "truncated": total > CASE_DETAIL_LIMIT,
        },
    }


def reset_all() -> dict[str, Any]:
    """Volta a base ao estado pré-demo. Idempotente."""
    db = get_db()
    c = db.companies.update_many(
        {"credit_status": FLAG},
        {"$set": {"credit_status": "active"}, "$unset": {"case_id": "", "reviewed_at": ""}},
    )
    e = db.credit_exposure.update_many(
        {"review_flag": True}, {"$unset": {"review_flag": "", "case_id": ""}}
    )
    db.credit_decisions.delete_many({})
    db.ownership_alerts.delete_many({})
    simuladas = db.ownership.delete_many({"simulated": True})
    return {
        "ok": True,
        "companies_restored": c.modified_count,
        "exposures_restored": e.modified_count,
        "simulated_edges_removed": simuladas.deleted_count,
    }
