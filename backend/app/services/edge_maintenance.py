"""Manutenção incremental das arestas, dirigida pelo change stream.

O buraco que isto fecha: `materialize_connections.py` é um job em lote que
reconstrói `connections` inteira e leva minutos. Numa demo tudo bem; em produção
não — a pergunta que um arquiteto faz é "e quando chega uma transação nova, o
grafo fica desatualizado até o próximo batch?".

A resposta é que não precisa ficar. O mesmo change stream que dispara o alerta
também materializa a aresta: transação nova entra, o vínculo por dispositivo
aparece em `connections` em seguida, e o próximo `$graphLookup` já o enxerga.

Regras preservadas do job em lote — se divergirem, o grafo incremental e o
reconstruído deixam de ser o mesmo grafo:

- só `from_account` conta (quem *operou* o dispositivo, não quem recebeu);
- fan-out acima do limiar não vira aresta (hub não é evidência de vínculo);
- aresta é bidirecional e tem `_id` determinístico, então reprocessar o mesmo
  evento reescreve o mesmo documento em vez de duplicar.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo import ReplaceOne

from app.config import get_settings
from app.db.client import get_db

log = logging.getLogger(__name__)

# Mesmo namespace do gerador (`data-generator/common.py`), para que o `_id` de uma
# aresta criada aqui coincida com o que o batch geraria para o mesmo par.
NAMESPACE = uuid.UUID("6f6b8b6e-3d0f-5a4a-9a1e-2f0b7c4d1e55")


def _det_id(kind: str, *parts: Any) -> str:
    return f"{kind}_{uuid.uuid5(NAMESPACE, '|'.join(str(p) for p in parts))}"


def maintain(change: dict[str, Any]) -> dict[str, Any] | None:
    """Materializa as arestas implicadas por uma transação recém-chegada.

    Devolve um resumo quando escreveu algo, `None` quando não havia o que fazer —
    o caminho comum, já que a maioria das transações roda num dispositivo que só
    aquela conta usa.
    """
    doc = change.get("fullDocument")
    if not doc:
        return None
    device = doc.get("device_id")
    origem = doc.get("from_account")
    if not device or not origem:
        return None

    s = get_settings()
    db = get_db()

    contas = db.transactions.distinct("from_account", {"device_id": device})
    if len(contas) < 2:
        return None
    if len(contas) > s.hub_threshold:
        # Hub: exatamente o caso que o batch descarta. Materializar aqui criaria
        # um componente conexo gigante uma aresta por vez, que é pior do que
        # criá-lo de uma vez — porque ninguém veria acontecendo.
        log.debug("device %s é hub (%d contas); nenhuma aresta materializada", device, len(contas))
        return {"skipped": "hub", "device_id": device, "accounts": len(contas)}

    pessoas = sorted(
        {a["person_id"] for a in db.accounts.find({"_id": {"$in": contas}}, {"person_id": 1})}
    )
    if len(pessoas) < 2:
        return None

    nova = db.accounts.find_one({"_id": origem}, {"person_id": 1})
    if not nova:
        return None
    pivo = nova["person_id"]
    outras = [p for p in pessoas if p != pivo]
    if not outras:
        return None

    agora = datetime.now(timezone.utc)
    ops = []
    for outra in outras:
        for src, dst in ((pivo, outra), (outra, pivo)):
            aresta = {
                "_id": _det_id("conn", "shares_device", src, dst),
                "from": src,
                "to": dst,
                "type": "shares_device",
                "shared_key": device,
                "weight": len(pessoas),
                "created_at": agora,
                "source": "change_stream",
            }
            ops.append(ReplaceOne({"_id": aresta["_id"]}, aresta, upsert=True))

    res = db.connections.bulk_write(ops, ordered=False)
    escritas = res.upserted_count + res.modified_count
    log.info("device %s: %d arestas materizalizadas incrementalmente", device, escritas)
    return {
        "device_id": device,
        "accounts_on_device": len(contas),
        "people_linked": len(pessoas),
        "edges_written": escritas,
        "edges_upserted": res.upserted_count,
    }
