"""Marcar uma rede inteira como sob investigação, em uma transação ACID.

O argumento de compliance é este: ou a rede toda entra em investigação, ou
nenhum nó entra. Um estado intermediário — metade das contas bloqueada, metade
livre, sem registro de auditoria coerente — é pior do que não ter agido.

Escopo da transação: `accounts.status`, `people.risk_flags` e um documento em
`investigations`. As três escritas commitam juntas.
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

from app.db.client import get_client, get_db

FLAG = "under_investigation"


def flag_network(person_ids: list[str], reason: str, analyst: str = "demo") -> dict[str, Any]:
    if not person_ids:
        raise ValueError("nenhum nó informado")

    client = get_client()
    db = get_db()
    case_id = f"case_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    started = time.perf_counter()

    def txn(session) -> dict[str, Any]:
        accounts = db.accounts.update_many(
            {"person_id": {"$in": person_ids}},
            {"$set": {"status": FLAG, "case_id": case_id, "flagged_at": now}},
            session=session,
        )
        people = db.people.update_many(
            {"_id": {"$in": person_ids}},
            {"$addToSet": {"risk_flags": FLAG}, "$set": {"case_id": case_id}},
            session=session,
        )
        db.investigations.insert_one(
            {
                "_id": case_id,
                "person_ids": person_ids,
                "reason": reason,
                "analyst": analyst,
                "opened_at": now,
                "accounts_flagged": accounts.modified_count,
                "people_flagged": people.modified_count,
                "status": "open",
            },
            session=session,
        )
        return {
            "accounts_flagged": accounts.modified_count,
            "people_flagged": people.modified_count,
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

    return {
        "ok": True,
        "case_id": case_id,
        "nodes": len(person_ids),
        **result,
        "read_concern": "snapshot",
        "write_concern": "majority",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def close_case(case_id: str) -> dict[str, Any]:
    """Desfaz a marcação. Existe para a demo poder ser reapresentada."""
    db = get_db()
    case = db.investigations.find_one({"_id": case_id})
    if not case:
        return {"ok": False, "error": "caso não encontrado"}
    client = get_client()
    with client.start_session() as session:
        def txn(s):
            db.accounts.update_many(
                {"case_id": case_id}, {"$set": {"status": "active"}, "$unset": {"case_id": "", "flagged_at": ""}}, session=s
            )
            db.people.update_many(
                {"case_id": case_id}, {"$pull": {"risk_flags": FLAG}, "$unset": {"case_id": ""}}, session=s
            )
            db.investigations.update_one({"_id": case_id}, {"$set": {"status": "closed"}}, session=s)

        session.with_transaction(txn, write_concern=WriteConcern("majority"))
    return {"ok": True, "case_id": case_id}


def reset_all() -> dict[str, Any]:
    """Volta o dataset ao estado pré-demo. Idempotente."""
    db = get_db()
    a = db.accounts.update_many({"status": FLAG}, {"$set": {"status": "active"}, "$unset": {"case_id": "", "flagged_at": ""}})
    p = db.people.update_many({"risk_flags": FLAG}, {"$pull": {"risk_flags": FLAG}, "$unset": {"case_id": ""}})
    db.investigations.delete_many({})
    db.alerts.delete_many({})
    db.transactions.delete_many({"simulated": True})
    # As arestas criadas ao vivo pelo change stream também saem: senão a segunda
    # apresentação encontra o grafo já ligado e o passo perde o "antes".
    arestas = db.connections.delete_many({"source": "change_stream"})
    # O cache dos pontos de entrada carrega `status`/`risk_flags`; reset o invalida.
    from app.services.demo import invalidate_cache

    invalidate_cache()
    return {
        "ok": True,
        "accounts_restored": a.modified_count,
        "people_restored": p.modified_count,
        "live_edges_removed": arestas.deleted_count,
    }
