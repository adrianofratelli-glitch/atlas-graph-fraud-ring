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
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReadPreference
from pymongo.errors import OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.db.client import get_client, get_db

FLAG = "under_investigation"


def flag_network(person_ids: list[str], reason: str, analyst: str = "demo") -> dict[str, Any]:
    if not person_ids:
        raise ValueError("no nodes provided")

    client = get_client()
    db = get_db()

    # Recusa abrir um segundo caso sobre nós que já estão em um caso aberto.
    #
    # Sem isto, marcar duas vezes sobrescrevia `case_id` nas contas e o primeiro
    # caso virava uma casca: `status: "open"` em `investigations`, zero contas
    # apontando para ele. Encerrar o segundo liberava tudo, e ficava no banco um
    # registro de auditoria afirmando investigação aberta sobre nada.
    #
    # Para uma POV cujo argumento é justamente a coerência do registro, esse
    # estado é pior do que um erro. E é também o comportamento correto num banco:
    # não se abrem dois casos sobre as mesmas pessoas — reabre-se o que existe.
    ja_aberto = db.accounts.find_one(
        {"person_id": {"$in": person_ids}, "status": FLAG, "case_id": {"$ne": None}},
        {"case_id": 1},
    )
    if ja_aberto:
        return {
            "ok": False,
            "case_id": ja_aberto["case_id"],
            "already_open": True,
            "error": (
                f"a case is already open ({ja_aberto['case_id']}) covering nodes in this network; "
                "close it before opening another"
            ),
            "elapsed_ms": 0.0,
        }
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
        return {"ok": False, "error": "case not found"}
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


def case_detail(case_id: str) -> dict[str, Any]:
    """O caso como ele está no banco, com o efeito da transação ACID visível.

    A tela precisa mostrar o *resultado* da ação, não só o `case_id`: quantas
    contas saíram de `active` para `under_investigation`, quando, e por ordem de
    quem. É esse documento que sustenta a auditoria exigida pela Circular BCB
    3.978/2020 e o registro de tratamento previsto na LGPD.
    """
    db = get_db()
    case = db.investigations.find_one({"_id": case_id})
    if not case:
        return {"ok": False, "error": "case not found"}

    contas = list(
        db.accounts.find(
            {"case_id": case_id},
            {"person_id": 1, "status": 1, "account_type": 1, "opened_at": 1, "flagged_at": 1},
        ).limit(200)
    )
    pessoas = {
        p["_id"]: p.get("name")
        for p in db.people.find({"_id": {"$in": case["person_ids"]}}, {"name": 1})
    }
    return {
        "ok": True,
        "case": {**case, "opened_at": case["opened_at"].isoformat()},
        "accounts": [
            {
                **c,
                "person_name": pessoas.get(c["person_id"]),
                "opened_at": c["opened_at"].isoformat() if c.get("opened_at") else None,
                "flagged_at": c["flagged_at"].isoformat() if c.get("flagged_at") else None,
                "status_before": "active",
            }
            for c in contas
        ],
        "people": [{"id": pid, "name": pessoas.get(pid)} for pid in case["person_ids"]],
    }


def coaf_report(case_id: str) -> dict[str, Any]:
    """Monta a comunicação estruturada de operação suspeita.

    Não integra com nada — e é esse o ponto. O documento sai **inteiro** do que a
    transação ACID já gravou, o que é a resposta técnica para duas obrigações:

    - **Circular BCB 3.978/2020**: comunicar operação suspeita ao COAF em até 24h
      da decisão, e manter o registro que sustenta a decisão.
    - **Resolução BCB 6/2023 (MED)**: bloquear e devolver recurso de fraude dentro
      da janela do Mecanismo Especial de Devolução — o bloqueio tem relógio.

    Se a marcação não fosse atômica, este relatório poderia descrever um estado
    que nunca existiu: contas bloqueadas sem auditoria, ou auditoria sem bloqueio.
    """
    detalhe = case_detail(case_id)
    if not detalhe.get("ok"):
        return detalhe

    db = get_db()
    caso = detalhe["case"]
    contas_ids = [c["_id"] for c in detalhe["accounts"]]
    movimento = list(
        db.transactions.aggregate(
            [
                {"$match": {"$or": [
                    {"from_account": {"$in": contas_ids}},
                    {"to_account": {"$in": contas_ids}},
                ]}},
                {"$group": {"_id": None, "n": {"$sum": 1}, "total": {"$sum": "$amount"}}},
            ],
            allowDiskUse=True,
        )
    )
    agregado = movimento[0] if movimento else {"n": 0, "total": 0.0}
    aberto = datetime.fromisoformat(caso["opened_at"])

    return {
        "ok": True,
        "case_id": case_id,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "comunicacao": {
            "tipo": "Suspicious activity report (COAF)",
            "fundamento": "BCB Circular 3.978/2020, art. 63 — report within 24h of the decision",
            "prazo_limite": (aberto + timedelta(hours=24)).isoformat(),
            "envolvidos": len(detalhe["people"]),
            "contas_bloqueadas": len(detalhe["accounts"]),
            "operacoes_analisadas": agregado["n"],
            "volume_analisado": round(agregado.get("total") or 0.0, 2),
            "tipologia": "pass-through account network (money mules) linked by device, address and shared destination PIX key",
            "motivo_registrado": caso.get("reason"),
            "responsavel": caso.get("analyst"),
            "decidido_em": caso["opened_at"],
        },
        "med": {
            "fundamento": "BCB Resolution 6/2023 — Special Refund Mechanism (MED)",
            "bloqueio_efetivado_em": caso["opened_at"],
            "prazo_analise": (aberto + timedelta(hours=72)).isoformat(),
            "observacao": "precautionary block recorded alongside the decision, in the same transaction",
        },
        "lgpd": {
            "fundamento": "Law 13.709/2018 (LGPD), art. 7 II and art. 37 — record of processing operations",
            "registro": f"investigations/{case_id}",
            "observacao": (
                "flagging a person as a risk is processing of personal data and requires an "
                "auditable record; it was written in the same transaction that applied the block"
            ),
        },
        "pessoas": detalhe["people"],
    }
