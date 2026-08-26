#!/usr/bin/env python3
"""Suíte hostil: tenta quebrar a API de todas as formas que uma demo quebra.

Não é teste de unidade. Cada caso corresponde a uma forma real de a POV falhar na
frente de um cliente, e o critério de aprovação é **degradar com mensagem clara**,
nunca 500, nunca travar, nunca devolver dado inconsistente.

    ../.venv/bin/python tests/test_resilience.py            # tudo
    ../.venv/bin/python tests/test_resilience.py --quick    # sem os casos lentos
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import sys
import time
from typing import Any, Callable

import httpx

BASE = os.getenv("GFR_BASE", "http://127.0.0.1:8350")
TIMEOUT = 60.0

PASS, FAIL = "\033[32m  ok \033[0m", "\033[31mFALHA\033[0m"
_results: list[tuple[str, bool, str]] = []


def check(nome: str, cond: bool, detalhe: str = "") -> None:
    _results.append((nome, cond, detalhe))
    print(f"{PASS if cond else FAIL}  {nome}" + (f"  — {detalhe}" if detalhe else ""))


def get(path: str, **kw) -> httpx.Response:
    return httpx.get(BASE + path, timeout=TIMEOUT, **kw)


def post(path: str, payload: Any = None) -> httpx.Response:
    return httpx.post(BASE + path, json=payload if payload is not None else {}, timeout=TIMEOUT)


# --------------------------------------------------------------- entradas hostis
def entradas_hostis(ctx: dict) -> None:
    pid = ctx["person_id"]

    r = get(f"/api/network/{'x' * 500}")
    check("id gigante devolve 404, não 500", r.status_code == 404, f"HTTP {r.status_code}")

    r = get("/api/network/nao-existe-mesmo")
    check("id inexistente devolve 404", r.status_code == 404, f"HTTP {r.status_code}")

    r = get(f"/api/network/{pid}?depth=999")
    ok = r.status_code == 200 and r.json()["depth"] <= 6
    check("profundidade 999 é limitada pelo cap", ok, f"depth={r.json().get('depth') if r.status_code == 200 else r.status_code}")

    r = get(f"/api/network/{pid}?depth=-5")
    check("profundidade negativa não quebra", r.status_code == 200, f"HTTP {r.status_code}")

    r = get(f"/api/network/{pid}?depth=abc")
    check("profundidade não-numérica devolve 422", r.status_code == 422, f"HTTP {r.status_code}")

    r = get(f"/api/network/{pid}?edge_types=shares_device,tipo_inventado")
    check("tipo de aresta inválido devolve 400", r.status_code == 400, f"HTTP {r.status_code}")

    r = get(f"/api/network/{pid}?edge_types=")
    check("edge_types vazio não quebra", r.status_code == 200, f"HTTP {r.status_code}")

    # Injeção de operador: o _id vem do path e vai direto para o $match.
    r = get('/api/network/{"$ne": null}')
    ok = r.status_code == 404
    check("payload de operador no _id não vira query", ok, f"HTTP {r.status_code}")

    r = get("/api/search/people?q=")
    check("busca vazia devolve 400", r.status_code == 400, f"HTTP {r.status_code}")

    r = get("/api/search/people?q=" + "a" * 5000)
    check("busca gigante não quebra", r.status_code in (200, 400), f"HTTP {r.status_code}")

    r = get("/api/search/people?q=%22%2F%5C%5E%24.%7C%3F*%2B()%5B%5D%7B%7D")
    check("busca com metacaracteres regex não quebra", r.status_code == 200, f"HTTP {r.status_code}")

    r = get("/api/search/people?q=teste&limit=99999")
    check("limit acima do teto devolve 422", r.status_code == 422, f"HTTP {r.status_code}")

    r = post("/api/search/similar-reasons", {"text": ""})
    check("vetor com texto vazio é erro de cliente", r.status_code in (400, 422), f"HTTP {r.status_code}")

    r = post("/api/search/similar-reasons", {})
    check("vetor sem campo `text` é erro de cliente", r.status_code in (400, 422), f"HTTP {r.status_code}")

    r = post("/api/search/similar-reasons", {"text": "   "})
    check("vetor com texto só de espaço é erro de cliente", r.status_code in (400, 422), f"HTTP {r.status_code}")

    r = post("/api/search/similar-reasons", {"text": "x", "limit": -1})
    check("vetor com limit negativo é erro de cliente, não 500", r.status_code in (400, 422), f"HTTP {r.status_code}")

    r = post("/api/investigation/flag", {"person_ids": []})
    check("marcar lista vazia é erro de cliente", r.status_code in (400, 422), f"HTTP {r.status_code}")

    r = post("/api/investigation/flag", {"person_ids": ["nao-existe"]})
    ok = r.status_code == 200 and r.json()["people_flagged"] == 0
    check("marcar id inexistente não inventa efeito", ok, f"HTTP {r.status_code}")

    r = post("/api/investigation/flag", {"person_ids": ["x"] * 5001})
    check("marcar acima do teto é erro de cliente", r.status_code in (400, 422), f"HTTP {r.status_code}")

    r = post("/api/investigation/flag", {"person_ids": "não é lista"})
    check("person_ids com tipo errado vira 422, não 409/500", r.status_code == 422, f"HTTP {r.status_code}")

    r = post("/api/investigation/close/nao-existe")
    check("fechar caso inexistente devolve 404", r.status_code == 404, f"HTTP {r.status_code}")

    r = get("/api/hops?source=a&target=b")
    check("hops entre ids inexistentes não quebra", r.status_code == 200, f"HTTP {r.status_code}")

    r = get("/api/hops")
    check("hops sem parâmetros devolve 422", r.status_code == 422, f"HTTP {r.status_code}")

    r = get("/api/network-by-device/nao-existe")
    check("padrão A com conta inexistente devolve 404", r.status_code == 404, f"HTTP {r.status_code}")


# ------------------------------------------------------------------ consistência
def consistencia(ctx: dict) -> None:
    pid = ctx["person_id"]

    r = get(f"/api/network/{pid}?depth=3")
    d = r.json()
    ids = {n["id"] for n in d["nodes"]}
    orfas = [e for e in d["edges"] if e["from"] not in ids or e["to"] not in ids]
    check("nenhuma aresta aponta para nó ausente do payload", not orfas, f"{len(orfas)} órfãs")

    check("stats.nodes bate com len(nodes)", d["stats"]["nodes"] == len(d["nodes"]))
    raiz = [n for n in d["nodes"] if n["is_root"]]
    check("existe exatamente uma raiz", len(raiz) == 1, f"{len(raiz)} raízes")
    check("a raiz está a 0 saltos", raiz and raiz[0]["hops"] == 0)

    # Monotonicidade: aumentar profundidade nunca pode reduzir o alcance.
    tamanhos = []
    for prof in (1, 2, 3, 4):
        s = get(f"/api/network/{pid}?depth={prof}").json()["stats"]["nodes"]
        tamanhos.append(s)
    check("alcance é monotônico com a profundidade", tamanhos == sorted(tamanhos), str(tamanhos))

    # Podar só pode remover, nunca acrescentar.
    com = get(f"/api/network/{pid}?depth=4&prune_hubs=true").json()["stats"]["nodes"]
    sem = get(f"/api/network/{pid}?depth=4&prune_hubs=false").json()["stats"]["nodes"]
    check("poda nunca aumenta o alcance", com <= sem, f"com={com} sem={sem}")

    # Um subconjunto de tipos de aresta nunca alcança mais do que todos.
    todos = get(f"/api/network/{pid}?depth=3").json()["stats"]["nodes"]
    um = get(f"/api/network/{pid}?depth=3&edge_types=shares_device").json()["stats"]["nodes"]
    check("subconjunto de arestas nunca alcança mais", um <= todos, f"um tipo={um} todos={todos}")

    # Determinismo: a mesma pergunta duas vezes dá a mesma resposta.
    a = get(f"/api/network/{pid}?depth=3").json()["stats"]["nodes"]
    b = get(f"/api/network/{pid}?depth=3").json()["stats"]["nodes"]
    check("expansão é determinística", a == b, f"{a} vs {b}")


# --------------------------------------------------------------------- ACID
def acid(ctx: dict) -> None:
    post("/api/demo/reset")
    pid = ctx["person_id"]
    rede = [n["id"] for n in get(f"/api/network/{pid}?depth=4").json()["nodes"] if n["ring_id"]]

    r = post("/api/investigation/flag", {"person_ids": rede, "reason": "resiliência"})
    check("marcação ACID responde 200", r.status_code == 200, f"HTTP {r.status_code}")
    caso = r.json()
    check("marcou o número de pessoas pedido", caso["people_flagged"] == len(rede), f"{caso['people_flagged']}/{len(rede)}")

    # Idempotência: repetir não deve duplicar efeito nem explodir.
    r2 = post("/api/investigation/flag", {"person_ids": rede, "reason": "de novo"})
    check("marcar duas vezes não quebra", r2.status_code == 200, f"HTTP {r2.status_code}")

    # Concorrência: N transações simultâneas sobre os MESMOS documentos.
    # É aqui que aparece WriteConflict se o isolamento estiver errado.
    def marca(i: int) -> int:
        return post("/api/investigation/flag", {"person_ids": rede, "reason": f"conc-{i}"}).status_code

    with futures.ThreadPoolExecutor(max_workers=8) as ex:
        codigos = list(ex.map(marca, range(8)))
    ok = all(c in (200, 409) for c in codigos)
    check("8 transações concorrentes: sem 500", ok, f"códigos={sorted(set(codigos))}")

    r = post(f"/api/investigation/close/{caso['case_id']}")
    check("fechar o caso responde 200", r.status_code == 200, f"HTTP {r.status_code}")
    r = post(f"/api/investigation/close/{caso['case_id']}")
    check("fechar duas vezes não quebra", r.status_code in (200, 404), f"HTTP {r.status_code}")

    post("/api/demo/reset")


# ------------------------------------------------------------------ change stream
def change_stream(ctx: dict) -> None:
    post("/api/demo/reset")
    pid = ctx["person_id"]
    rede = [n["id"] for n in get(f"/api/network/{pid}?depth=4").json()["nodes"] if n["ring_id"]]
    post("/api/investigation/flag", {"person_ids": rede, "reason": "cs"})

    antes = get("/api/alerts/recent?limit=1").json()["listener"]["alerts"]
    post("/api/demo/simulate-transaction")
    prazo = time.time() + 25
    depois = antes
    while time.time() < prazo:
        depois = get("/api/alerts/recent?limit=1").json()["listener"]["alerts"]
        if depois > antes:
            break
        time.sleep(1)
    check("change stream dispara alerta em <25s", depois > antes, f"{antes} -> {depois}")

    # Rajada: o listener não pode perder eventos nem morrer.
    with futures.ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(lambda _: post("/api/demo/simulate-transaction"), range(12)))
    prazo = time.time() + 40
    while time.time() < prazo:
        est = get("/api/alerts/recent?limit=1").json()["listener"]
        if est["alerts"] >= depois + 12:
            break
        time.sleep(1)
    est = get("/api/alerts/recent?limit=1").json()["listener"]
    check("listener sobrevive a rajada de 12 transações", est["running"] and est["last_error"] is None,
          f"running={est['running']} erro={est['last_error']}")
    check("nenhum alerta perdido na rajada", est["alerts"] >= depois + 12, f"{est['alerts']} (esperado >= {depois + 12})")

    post("/api/demo/reset")


# ------------------------------------------------- manutenção incremental de aresta
def manutencao_de_aresta(ctx: dict) -> None:
    """O grafo se mantém sozinho, sem esperar o próximo job em lote."""
    r = post("/api/demo/link-accounts")
    check("link-accounts responde 200", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        return
    d = r.json()
    a, b = d["person_a"]["id"], d["person_b"]["id"]
    check("as duas pessoas não tinham vínculo antes", not d["edge_existed_before"])

    prazo, ligado, esperou = time.time() + 30, False, 0.0
    while time.time() < prazo:
        t0 = time.time()
        if get(f"/api/connections/between?a={a}&b={b}").json()["connected"]:
            ligado = True
            break
        time.sleep(1)
        esperou += time.time() - t0
    check("aresta materializada pelo change stream em <30s", ligado, f"~{esperou:.0f}s")

    if ligado:
        arestas = get(f"/api/connections/between?a={a}&b={b}").json()["edges"]
        check("aresta marcada com a origem `change_stream`",
              any(e.get("source") == "change_stream" for e in arestas))
        # E o traversal precisa enxergar a aresta nova, senão a manutenção não serve.
        rede = get(f"/api/network/{a}?depth=1").json()
        check("o traversal seguinte já percorre a aresta nova",
              any(n["id"] == b for n in rede["nodes"]),
              f"{rede['stats']['nodes']} nós a 1 salto")

    # Repetir o mesmo evento não pode duplicar aresta (`_id` determinístico).
    antes = len(get(f"/api/connections/between?a={a}&b={b}").json()["edges"])
    post("/api/demo/link-accounts")
    time.sleep(4)
    depois = len(get(f"/api/connections/between?a={a}&b={b}").json()["edges"])
    check("reprocessar não duplica aresta", depois <= max(1, antes), f"{antes} -> {depois}")

    est = get("/api/alerts/recent?limit=1").json()["listener"]
    check("listener continua vivo depois da manutenção",
          est["running"] and est["last_error"] is None, f"erro={est['last_error']}")


# ----------------------------------------------------------------------- carga
def carga(ctx: dict, n: int = 40) -> None:
    pid = ctx["person_id"]

    def uma(i: int) -> tuple[int, float]:
        t0 = time.perf_counter()
        r = get(f"/api/network/{pid}?depth={(i % 4) + 1}")
        return r.status_code, (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    with futures.ThreadPoolExecutor(max_workers=10) as ex:
        res = list(ex.map(uma, range(n)))
    total = time.perf_counter() - t0
    codigos = [c for c, _ in res]
    tempos = sorted(t for _, t in res)
    p50 = tempos[len(tempos) // 2]
    p95 = tempos[int(len(tempos) * 0.95) - 1]
    check(f"{n} expansões concorrentes: todas 200", all(c == 200 for c in codigos),
          f"códigos={sorted(set(codigos))}")
    check("p95 sob concorrência abaixo de 5s", p95 < 5000, f"p50={p50:.0f}ms p95={p95:.0f}ms em {total:.1f}s")
    ctx["carga"] = {"n": n, "p50_ms": round(p50, 1), "p95_ms": round(p95, 1), "wall_s": round(total, 1)}

    # Saúde continua respondendo durante/depois da carga.
    r = get("/health")
    check("/health continua ok depois da carga", r.status_code == 200 and r.json()["status"] in ("ok", "degraded"),
          f"HTTP {r.status_code}")


# ------------------------------------------------------------------ plano de query
def plano_de_query(ctx: dict) -> None:
    """Guarda de regressão para o bug mais caro encontrado nesta POV.

    `materialize_connections.py --rebuild` faz `drop()`, e `drop()` leva os
    índices junto. Durante um bom tempo o traversal rodou por COLLSCAN em cada
    nível do BFS e ninguém percebeu: nada quebra, só fica lento — e "lento" numa
    demo é indistinguível de "é assim mesmo".
    """
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "data-generator"))
    from common import get_db  # noqa: E402

    db = get_db()
    nomes = {i["name"] for i in db.connections.list_indexes()}
    check("connections tem índice em `from`", "from_1" in nomes, str(sorted(nomes)))
    check("connections tem índice em `to`", "to_1" in nomes, str(sorted(nomes)))

    # PyMongo: Cursor.explain() não aceita verbosidade; o comando cru aceita.
    plano = db.command(
        {
            "explain": {"find": "connections", "filter": {"from": ctx["person_id"]}},
            "verbosity": "executionStats",
        }
    )
    usou_ixscan = "IXSCAN" in json.dumps(plano["queryPlanner"]["winningPlan"])
    check("aresta do traversal usa IXSCAN, não COLLSCAN", usou_ixscan)

    est = plano["executionStats"]
    razao = est["totalDocsExamined"] / max(1, est["nReturned"])
    check("examina ~1 documento por documento devolvido", razao <= 1.5,
          f"{est['totalDocsExamined']} examinados / {est['nReturned']} devolvidos")


# ------------------------------------------------------------------- degradação
def degradacao(ctx: dict) -> None:
    r = get("/health")
    d = r.json()
    check("/health reporta status dos índices de busca", "search_indexes" in d["checks"])
    check("/health reporta estado do change stream", "change_stream" in d["checks"])
    check("/health/live não depende do banco", get("/health/live").status_code == 200)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    try:
        ep = get("/api/entry-points").json()
    except Exception as exc:
        print(f"Backend indisponível em {BASE}: {exc}")
        sys.exit(2)
    ctx = {"person_id": ep["suspects"][0]["person_id"]}
    print(f"alvo: {ep['suspects'][0]['person_name']} ({ep['suspects'][0]['ring_id']})\n")

    blocos: list[tuple[str, Callable]] = [
        ("Entradas hostis", entradas_hostis),
        ("Consistência do payload", consistencia),
        ("Transação ACID e concorrência", acid),
        ("Plano de execução", plano_de_query),
        ("Degradação e saúde", degradacao),
    ]
    if not args.quick:
        blocos += [
            ("Change Streams sob rajada", change_stream),
            ("Manutenção incremental de aresta", manutencao_de_aresta),
            ("Carga concorrente", carga),
        ]

    for titulo, fn in blocos:
        print(f"\n── {titulo}")
        try:
            fn(ctx)
        except Exception as exc:
            check(f"{titulo}: bloco não levantou exceção", False, repr(exc))

    ok = sum(1 for _, c, _ in _results if c)
    print(f"\n{'=' * 60}\n{ok}/{len(_results)} passaram")
    if ctx.get("carga"):
        print(f"carga: {json.dumps(ctx['carga'])}")
    falhas = [(n, d) for n, c, d in _results if not c]
    if falhas:
        print("\nfalhas:")
        for n, d in falhas:
            print(f"  - {n}  {d}")
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
