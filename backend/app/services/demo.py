"""Pontos de entrada da demo e simulação de transação.

O roteiro depende de sempre haver um caso bom à mão. `entry_points()` devolve,
a partir do ground truth em `rings`, uma conta limpa e um nó de rede — nunca uma
busca ao vivo que pode não retornar nada na frente do cliente.

**Este endpoint é o primeiro que a tela chama, então ele é o tempo de abertura da
demo.** A versão ingênua levava 10 a 20 segundos: seis agregações separadas sobre
`connections`, uma consulta por anel em `people` e um `$sample` varrendo 150 mil
documentos — cada ida ao cluster pagando ~270 ms de rede. Vinte segundos de tela
vazia na abertura destrói a demo antes dela começar.

Três mudanças resolveram: uma única agregação para todos os anéis em vez de seis,
seleção determinística por `seed_index` (indexado) no lugar do `$sample`, e cache
em memória — o ground truth não muda durante uma apresentação.
"""
from __future__ import annotations

import random
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.client import get_db, with_retry

# O resultado é estável enquanto o dataset não é regerado. `/api/demo/reset`
# invalida, porque ele mexe em `status` e `risk_flags`.
_cache: dict[str, Any] | None = None
_cache_lock = threading.Lock()


def invalidate_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _entry_nodes(db, rings: list[dict[str, Any]]) -> dict[str, str]:
    """Nó de entrada de cada anel — **garantidamente com arestas**, em uma consulta.

    O líder é a raiz da árvore, então é dele que a revelação por profundidade fica
    mais legível. Medido no dataset padrão, entrando pelo líder de uma rede de 30:
    7 nós na profundidade 1, 15 na 2 e os 30 na 3.

    Um membro sorteado, porém, pode ser um nó isolado e abrir o grafo vazio na
    frente do cliente. Por isso a escolha passa pelo grau: o líder só é usado se
    tiver arestas materializadas; senão cai para o membro de maior grau.

    Todos os anéis são resolvidos num único `$group` por `ring_id`, em vez de uma
    agregação por anel — é a diferença entre uma ida ao cluster e seis.
    """
    ring_ids = [r["ring_id"] for r in rings]
    membros = list(
        db.people.find({"ring_id": {"$in": ring_ids}}, {"_id": 1, "ring_id": 1})
    )
    ring_of = {m["_id"]: m["ring_id"] for m in membros}

    # Ordena por **variedade de vínculo** antes de grau.
    #
    # Grau sozinho escolhia um nó que tinha só arestas de endereço. Na tela isso
    # quebra o passo dos toggles: o apresentador desmarca "Endereço" para mostrar
    # que o vínculo real é o dispositivo, e o grafo desaba para um nó só — parece
    # bug, não argumento. Um ponto de entrada que toca os três tipos é o que faz
    # ligar e desligar cada aresta dizer alguma coisa.
    graus = db.connections.aggregate(
        [
            {"$match": {"from": {"$in": list(ring_of)}}},
            {"$group": {"_id": "$from", "grau": {"$sum": 1}, "tipos": {"$addToSet": "$type"}}},
            {"$set": {"variedade": {"$size": "$tipos"}}},
            # Ordem do fallback: variedade de vínculo primeiro, grau depois.
            #
            # Nenhum dos dois sozinho serve. Só grau alto escolhe o coletor do
            # funil, e entrar por ele traz os 30 membros já na profundidade 1 —
            # a revelação por saltos, que é a espinha da demo, deixa de existir.
            # Só grau baixo escolhe uma folha, e aí o anel só fecha no salto 5.
            #
            # Este ramo só decide quando o líder não serve; o caso normal é o
            # líder, que é a raiz da árvore e dá a curva mais legível.
            {"$sort": {"variedade": -1, "grau": -1}},
        ],
        allowDiskUse=True,
    )
    melhor: dict[str, str] = {}
    variedade_de: dict[str, int] = {}
    com_arestas: set[str] = set()
    for g in graus:
        com_arestas.add(g["_id"])
        variedade_de[g["_id"]] = g["variedade"]
        rid = ring_of.get(g["_id"])
        if rid and rid not in melhor:  # já vem ordenado por variedade, depois grau
            melhor[rid] = g["_id"]

    escolha: dict[str, str] = {}
    for r in rings:
        rid = r["ring_id"]
        candidato = melhor.get(rid)
        # O líder é a raiz da árvore, e é dele que a revelação por profundidade
        # fica legível: poucos vizinhos diretos, o anel fechando alguns saltos
        # depois. Ele só é trocado se não tiver aresta materializada nenhuma —
        # entrar por um nó isolado abriria o grafo vazio na frente do cliente.
        if r["leader"] in com_arestas:
            escolha[rid] = r["leader"]
        elif candidato:
            escolha[rid] = candidato
    return escolha


def entry_points(limit: int = 6) -> dict[str, Any]:
    global _cache
    with _cache_lock:
        if _cache is not None:
            return _cache

    db = get_db()
    rings = with_retry(lambda: list(db.rings.find().sort("size", -1).limit(limit)), "rings")
    escolha = _entry_nodes(db, rings)

    # Uma consulta para todos os nomes, outra para todas as contas.
    ids = list(escolha.values()) + [r["leader"] for r in rings]
    nomes = {p["_id"]: p for p in db.people.find({"_id": {"$in": ids}}, {"name": 1, "ring_id": 1})}
    contas: dict[str, str] = {}
    for a in db.accounts.find({"person_id": {"$in": ids}}, {"person_id": 1}):
        contas.setdefault(a["person_id"], a["_id"])

    suspects = []
    for r in rings:
        pid = escolha.get(r["ring_id"])
        if not pid or pid not in nomes:
            continue
        suspects.append(
            {
                "ring_id": r["ring_id"],
                "ring_size": r["size"],
                "person_id": pid,
                "person_name": nomes[pid].get("name"),
                "account_id": contas.get(pid),
                "leader_id": r["leader"],
            }
        )

    # `$sample` sobre `{ring_id: None}` varre a coleção inteira. `seed_index` é
    # indexado e determinístico, então três `find` por faixa custam um índice cada.
    rng = random.Random(len(rings))
    alvos = [rng.randrange(0, 45_000) for _ in range(3)]
    clean = []
    for alvo in alvos:
        doc = db.people.find_one(
            {"seed_index": {"$gte": alvo}, "ring_id": None}, {"name": 1}, sort=[("seed_index", 1)]
        )
        if doc:
            clean.append(doc)
    limpos_ids = [c["_id"] for c in clean]
    contas_limpas: dict[str, str] = {}
    for a in db.accounts.find({"person_id": {"$in": limpos_ids}}, {"person_id": 1}):
        contas_limpas.setdefault(a["person_id"], a["_id"])
    for c in clean:
        c["account_id"] = contas_limpas.get(c["_id"])

    twin = db.people.find_one({"near_duplicate_of": {"$exists": True}}, {"name": 1, "near_duplicate_of": 1})
    twin_of = db.people.find_one({"_id": twin["near_duplicate_of"]}, {"name": 1, "ring_id": 1}) if twin else None

    result = {
        "suspects": suspects,
        "clean": clean,
        "entity_resolution_case": (
            {
                "typo_name": twin["name"],
                "typo_id": twin["_id"],
                "real_name": twin_of["name"],
                "real_id": twin_of["_id"],
                "real_ring": twin_of["ring_id"],
            }
            if twin and twin_of
            else None
        ),
    }
    with _cache_lock:
        _cache = result
    return result


def link_two_accounts(device_id: str | None = None) -> dict[str, Any]:
    """Insere uma transação que faz duas pessoas *sem vínculo* dividirem um device.

    É a prova de que o grafo se mantém sozinho: antes da inserção não existe
    aresta entre as duas, depois do change stream existe, e o `$graphLookup`
    seguinte já a percorre. Sem rodar job em lote nenhum.
    """
    db = get_db()
    # Par novo a cada chamada. Um par fixo faria a segunda apresentação seguida
    # falhar em silêncio: as duas pessoas já estariam ligadas pela primeira, e o
    # "antes" do antes/depois deixaria de ser verdade.
    rng = random.Random()
    for _ in range(12):
        ia, ib = rng.randrange(1_000, 40_000), rng.randrange(60_000, 100_000)
        a = db.people.find_one({"ring_id": None, "seed_index": {"$gte": ia}}, {"name": 1}, sort=[("seed_index", 1)])
        b = db.people.find_one({"ring_id": None, "seed_index": {"$gte": ib}}, {"name": 1}, sort=[("seed_index", 1)])
        if a and b and a["_id"] != b["_id"] and db.connections.count_documents({"from": a["_id"], "to": b["_id"]}) == 0:
            break
    else:
        return {"ok": False, "error": "could not find a pair that is still unlinked"}

    conta_a = db.accounts.find_one({"person_id": a["_id"]}, {"person_id": 1})
    conta_b = db.accounts.find_one({"person_id": b["_id"]}, {"person_id": 1})
    if not conta_a or not conta_b:
        return {"ok": False, "error": "accounts not found"}

    ja_ligadas = False  # garantido pelo laço acima
    device = device_id or f"device_demo_{uuid.uuid4().hex[:10]}"
    agora = datetime.now(timezone.utc)

    # Duas transações no mesmo dispositivo, uma de cada conta: é isso que faz as
    # duas aparecerem como `from_account` do mesmo device — a regra que o job em
    # lote usa e que a manutenção incremental preserva.
    docs = []
    for i, (origem, destino) in enumerate(((conta_a, conta_b), (conta_b, conta_a))):
        docs.append(
            {
                "_id": f"txn_link_{uuid.uuid4().hex[:12]}",
                "from_account": origem["_id"],
                "to_account": destino["_id"],
                "device_id": device,
                "amount": round(random.uniform(500, 3000), 2),
                "reason_text": "transferencia para conta propria",
                "timestamp": agora,
                "ring_id": None,
                "simulated": True,
            }
        )
    db.transactions.insert_many(docs)
    return {
        "ok": True,
        "device_id": device,
        "person_a": {"id": a["_id"], "name": a.get("name")},
        "person_b": {"id": b["_id"], "name": b.get("name")},
        "edge_existed_before": ja_ligadas,
        "transactions": [d["_id"] for d in docs],
    }


def simulate_transaction(
    account_id: str | None = None,
    amount: float | None = None,
    person_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Injeta uma transação que toca a rede, para acordar o change stream.

    Funciona **com e sem** a rede marcada, de propósito. É o A/B que prova que o
    alerta consulta o estado de verdade em vez de disparar sozinho: a mesma
    transação, na mesma rede, alerta depois de marcar e não alerta antes. Antes
    esta função recusava quando nada estava sob investigação, e esse contraste era
    impossível de mostrar.

    `expect_alert` diz o que a tela deve esperar, para que "nenhum alerta" apareça
    como resultado previsto e não como uma demo que travou.

    Marcada com `simulated: True` para que `reset_all()` consiga limpá-la.
    """
    db = get_db()
    if not account_id:
        # Quando a tela informa a rede, a busca fica **restrita a ela**.
        #
        # Sem essa restrição, o A/B mente: depois de encerrar o caso, um caso
        # aberto de uma apresentação anterior — em outra rede — seria encontrado,
        # a transação cairia lá e o alerta dispararia mesmo com a rede da tela
        # já liberada. Aconteceu no primeiro teste ponta a ponta.
        escopo = {"person_id": {"$in": person_ids}} if person_ids else {}
        flagged = db.accounts.find_one({**escopo, "status": "under_investigation"}, {"person_id": 1})
        if flagged:
            account_id = flagged["_id"]
        elif person_ids:
            # Rede na tela sem nada marcado: injeta nela mesma, para que o
            # antes/depois seja sobre a MESMA rede e portanto comparável.
            alvo = db.accounts.find_one({"person_id": {"$in": person_ids}}, {"person_id": 1})
            if not alvo:
                return {"ok": False, "error": "no account found for the network on screen"}
            account_id = alvo["_id"]
        else:
            return {"ok": False, "error": "no account under investigation and no network on screen"}

    destino = db.accounts.find_one({"_id": account_id}, {"status": 1})
    expect_alert = bool(destino and destino.get("status") == "under_investigation")

    counterpart = db.accounts.find_one({"status": "active", "ring_id": None}, {"person_id": 1})
    rng = random.Random()
    doc = {
        "_id": f"txn_sim_{rng.getrandbits(48):012x}",
        "from_account": counterpart["_id"] if counterpart else account_id,
        "to_account": account_id,
        "device_id": None,
        "amount": amount if amount is not None else round(rng.uniform(2000, 9000), 2),
        "reason_text": "transferencia para conta propria",
        "timestamp": datetime.now(timezone.utc),
        "ring_id": None,
        "simulated": True,
    }
    db.transactions.insert_one(doc)
    return {
        "ok": True,
        "expect_alert": expect_alert,
        "explanation": (
            "the destination account is under investigation: the listener should publish an alert"
            if expect_alert
            else "no account in the network is under investigation: the listener sees the transaction "
            "and does not alert — that is the correct outcome, not a failure"
        ),
        "transaction": {**doc, "timestamp": doc["timestamp"].isoformat()},
    }
