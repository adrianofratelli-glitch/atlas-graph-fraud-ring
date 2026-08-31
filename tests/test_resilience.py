#!/usr/bin/env python3
"""Suíte hostil. Não é teste de unidade.

Cada caso corresponde a uma forma real de esta POV falhar na frente de um
cliente, e o critério de aprovação é sempre o mesmo: **degradar com mensagem
clara**. Nunca 500, nunca travar, nunca devolver dado inconsistente.

Uso:
    python tests/test_resilience.py
    python tests/test_resilience.py --quick   # sem change stream e sem carga
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent import futures

import httpx

BASE = "http://127.0.0.1:8350"
c = httpx.Client(base_url=BASE, timeout=90.0)

_ok = 0
_falhas: list[str] = []
VERDE, VERMELHO, FIM = "\033[32m", "\033[31m", "\033[0m"


def check(nome: str, cond: bool, detalhe: str = "") -> None:
    global _ok
    if cond:
        _ok += 1
        print(f"{VERDE}  ok {FIM}  {nome}" + (f"  — {detalhe}" if detalhe else ""))
    else:
        _falhas.append(nome)
        print(f"{VERMELHO}  FALHA{FIM}  {nome}  — {detalhe}")


def get(p: str, **kw):
    return c.get(p, **kw)


def post(p: str, body=None):
    return c.post(p, json=body if body is not None else {})


# ------------------------------------------------------------------ contexto
def contexto() -> dict:
    ep = get("/api/entry-points").json()
    if not ep.get("applicants"):
        raise SystemExit("sem pontos de entrada: rode data-generator/run_all.sh")
    a = ep["applicants"][0]
    g = get(f"/api/group/{a['cnpj']}?depth=3").json()
    return {
        "cnpj": a["cnpj"],
        "grupo": g,
        "company_ids": [n["id"] for n in g["nodes"] if n["kind"] == "company"],
        "controle": ep["control"][0]["cnpj"] if ep.get("control") else None,
    }


# ------------------------------------------------------------------ entradas hostis
def entradas(ctx: dict) -> None:
    r = get("/api/group/" + "9" * 300)
    check("CNPJ gigante devolve 404, não 500", r.status_code == 404, f"HTTP {r.status_code}")
    r = get("/api/group/00000000000000")
    check("CNPJ inexistente devolve 404", r.status_code == 404, f"HTTP {r.status_code}")
    r = get(f"/api/group/{ctx['cnpj']}?depth=999")
    check("profundidade absurda é limitada pelo backend", r.status_code == 200
          and r.json()["depth"] <= 6, f"depth={r.json().get('depth') if r.status_code==200 else r.status_code}")
    r = get(f"/api/group/{ctx['cnpj']}?depth=-5")
    check("profundidade negativa não quebra", r.status_code == 200 and r.json()["depth"] >= 1,
          f"HTTP {r.status_code}")
    r = get("/api/group/" + '{"$ne": null}')
    check("operador do Mongo no CNPJ não é interpretado", r.status_code in (404, 400),
          f"HTTP {r.status_code}")

    r = post("/api/search/companies", {"q": ""})
    check("busca vazia é recusada", r.status_code == 422, f"HTTP {r.status_code}")
    r = post("/api/search/companies", {"q": "a" * 500})
    check("busca acima do limite é recusada", r.status_code == 422, f"HTTP {r.status_code}")
    r = post("/api/search/companies", {"q": "   "})
    check("busca só com espaço é recusada", r.status_code == 422, f"HTTP {r.status_code}")
    r = post("/api/search/companies", {"q": "(((", "limit": 5})
    check("metacaracteres de regex não quebram a busca", r.status_code == 200, f"HTTP {r.status_code}")
    r = post("/api/search/companies", {"q": "Ltda", "limit": -1})
    check("limit fora de faixa é recusado", r.status_code == 422, f"HTTP {r.status_code}")
    r = post("/api/search/companies", {"q": "Ltda", "company_ids": "nao-e-lista"})
    check("company_ids com tipo errado é recusado", r.status_code == 422, f"HTTP {r.status_code}")


# ------------------------------------------------------------------ consistência
def consistencia(ctx: dict) -> None:
    g = ctx["grupo"]
    ids = {n["id"] for n in g["nodes"]}
    orfas = [e for e in g["edges"] if e["from"] not in ids or e["to"] not in ids]
    check("nenhuma aresta aponta para nó ausente", not orfas, f"{len(orfas)} órfãs")

    sujeitos = [n for n in g["nodes"] if n.get("is_subject")]
    check("existe exatamente um sujeito", len(sujeitos) == 1, f"{len(sujeitos)}")

    empresas = [n for n in g["nodes"] if n["kind"] == "company"]
    check("contagem de empresas bate com o payload", len(empresas) == g["stats"]["companies"],
          f"{len(empresas)} vs {g['stats']['companies']}")

    soma = round(sum(n["limite"] for n in empresas), 2)
    check("exposição consolidada é a soma dos nós",
          abs(soma - g["group_exposure"]["limite"]) < 1.0,
          f"{soma} vs {g['group_exposure']['limite']}")

    # Profundidade maior nunca pode devolver menos empresas.
    rasos = get(f"/api/group/{ctx['cnpj']}?depth=1").json()["stats"]["companies"]
    fundos = get(f"/api/group/{ctx['cnpj']}?depth=4").json()["stats"]["companies"]
    check("mais níveis nunca devolve menos empresas", fundos >= rasos, f"d1={rasos} d4={fundos}")

    # Determinismo: a mesma consulta duas vezes devolve o mesmo conjunto.
    a = get(f"/api/group/{ctx['cnpj']}?depth=3").json()
    b = get(f"/api/group/{ctx['cnpj']}?depth=3").json()
    check("consulta é determinística",
          {n["id"] for n in a["nodes"]} == {n["id"] for n in b["nodes"]})

    if ctx["controle"]:
        ctrl = get(f"/api/group/{ctx['controle']}?depth=3").json()
        check("empresa sem grupo devolve cadeia pequena, não erro",
              ctrl["found"] and ctrl["stats"]["companies"] >= 1,
              f"{ctrl['stats']['companies']} empresas")


# ------------------------------------------------------------------- busca
def busca_escopada(ctx: dict) -> None:
    """A busca da tela procura **dentro do grafo**, e isso é contrato, não estilo.

    Antes, digitar um primeiro nome comum devolvia pessoas e empresas sem relação
    nenhuma com o grupo desenhado: o analista tinha de parear cada linha com o
    grafo de cabeça, e não tem como. O escopo é o padrão; abrir para a base
    inteira é uma ação deliberada de entity resolution.
    """
    grupo = get(f"/api/group/{ctx['cnpj']}?depth=3").json()
    ids = [n["id"] for n in grupo["nodes"] if n["kind"] == "company"]
    nos = [n["id"] for n in grupo["nodes"]]
    empresa = next(n for n in grupo["nodes"] if n["kind"] == "company")
    termo = empresa["label"].split()[0]

    r = post("/api/search/companies", {"q": termo, "company_ids": ids, "node_ids": nos}).json()
    check("busca escopada devolve resultado do grupo", len(r["results"]) > 0, f"{len(r['results'])}")
    check("**todo** resultado escopado está no grupo em tela",
          all(x["in_group"] for x in r["results"]),
          f"{sum(1 for x in r['results'] if not x['in_group'])} fora do grupo")
    check("a resposta declara que veio escopada", r.get("scoped") is True, str(r.get("scoped")))

    # Um termo que não existe no grupo tem de voltar vazio, não voltar ruído.
    r = post("/api/search/companies",
             {"q": "zzqx", "company_ids": ids, "node_ids": nos}).json()
    check("termo ausente do grupo devolve vazio, não ruído", len(r["results"]) == 0,
          f"{len(r['results'])} resultados")

    # E a saída para entity resolution continua existindo.
    r = post("/api/search/companies",
             {"q": termo, "company_ids": ids, "node_ids": nos, "scope_only": False}).json()
    fora = [x for x in r["results"] if not x["in_group"]]
    check("scope_only=False volta a procurar fora do grupo", len(fora) > 0,
          f"{len(fora)} fora do grupo")


# --------------------------------------------------------------- hierarquia
def hierarquia(ctx: dict) -> None:
    """Escopo de visibilidade: o servidor decide, e a fronteira é de verdade."""
    roster = get("/api/hierarchy/roster").json()["users"]
    check("roster devolve usuários de exemplo", len(roster) >= 2, f"{len(roster)}")
    if len(roster) < 2:
        return

    gerentes = [u for u in roster if u["papel"] in ("gerente", "regional")]
    assessores = [u for u in roster if u["papel"] == "assessor"]
    if not gerentes or not assessores:
        check("roster tem gerente e assessor", False, str([u["papel"] for u in roster]))
        return

    ger, ass = gerentes[0], assessores[0]
    pg = get(f"/api/hierarchy/{ger['_id']}/portfolio").json()
    pa = get(f"/api/hierarchy/{ass['_id']}/portfolio").json()

    check("gerente alcança mais de um assessor", pg["scope"]["advisors"] > 1,
          str(pg["scope"]))
    check("assessor alcança só a si mesmo", pa["scope"]["advisors"] == 1,
          str(pa["scope"]))
    check("carteira do gerente contém a do assessor",
          pg["portfolio"]["companies_with_credit"] >= pa["portfolio"]["companies_with_credit"],
          f"{pg['portfolio']['companies_with_credit']} vs {pa['portfolio']['companies_with_credit']}")
    check("soma de utilizado do gerente não é menor",
          pg["portfolio"]["utilizado"] >= pa["portfolio"]["utilizado"] - 1.0)

    # A checagem de visibilidade é a fronteira, e ela precisa negar de verdade:
    # uma implementação que sempre autoriza passaria em tudo acima.
    v = get(f"/api/hierarchy/{ass['_id']}/can-see/{ctx['cnpj']}").json()
    check("checagem de visibilidade responde com motivo",
          "allowed" in v and v.get("reason"), str(v)[:120])

    dono = v.get("owner", {}).get("id")
    if dono:
        vd = get(f"/api/hierarchy/{dono}/can-see/{ctx['cnpj']}").json()
        check("o próprio assessor da conta enxerga a conta", vd.get("allowed") is True, str(vd)[:120])

    outro = next((u for u in assessores if u["_id"] != dono), None)
    if outro:
        vo = get(f"/api/hierarchy/{outro['_id']}/can-see/{ctx['cnpj']}").json()
        check("assessor de outro ramo é negado", vo.get("allowed") is False, str(vo)[:120])

    # A escada da hierarquia sobre **o grupo que está na tela**.
    #
    # É o que a demo mostra e o que nenhuma asserção cobria: subir um nível tem
    # de revelar mais empresas do mesmo grupo econômico. Numa versão anterior os
    # três assessores do grupo caíram em ramos distintos da árvore e gerente e
    # assessor enxergavam exatamente a mesma fatia — a tela ficava correta e o
    # argumento, vazio.
    grupo = get(f"/api/group/{ctx['cnpj']}?depth=3").json()
    empresas = [n for n in grupo["nodes"] if n["kind"] == "company"]
    check("cada empresa do grupo tem assessor responsável",
          all(n.get("advisor_id") for n in empresas),
          f"{sum(1 for n in empresas if not n.get('advisor_id'))} sem assessor")

    def alcance(uid: str) -> int:
        p = get(f"/api/hierarchy/{uid}/portfolio").json()
        escopo = {p["user"]["id"]} | {t["id"] for t in p["team"]}
        return sum(1 for n in empresas if n.get("advisor_id") in escopo)

    por_papel = {}
    for u in roster:
        por_papel.setdefault(u["papel"], []).append(u["_id"])

    if {"assessor", "gerente", "regional"} <= set(por_papel):
        a = max(alcance(uid) for uid in por_papel["assessor"])
        g = alcance(por_papel["gerente"][0])
        r_ = alcance(por_papel["regional"][0])
        check("gerente enxerga mais do grupo que o assessor", g > a, f"assessor {a}, gerente {g}")
        check("regional enxerga o grupo inteiro", r_ == len(empresas), f"{r_}/{len(empresas)}")
        check("nenhum assessor sozinho enxerga o grupo inteiro", a < len(empresas),
              f"{a}/{len(empresas)}")

    r = get("/api/hierarchy/advisor_inexistente/portfolio")
    check("usuário inexistente vira 404, não 500", r.status_code == 404, str(r.status_code))
    r = get("/api/hierarchy/" + '{"$ne": null}' + "/portfolio")
    check("operador injetado no id não vira consulta", r.status_code == 404, str(r.status_code))


# ------------------------------------------------------------------ transação ACID
def acid(ctx: dict) -> None:
    post("/api/demo/reset")
    # Relê o grupo DEPOIS do reset. O contexto é montado no início da suíte, e o
    # reset remove as arestas simuladas de execuções anteriores — usar a lista
    # antiga fazia a suíte revisar uma empresa que já não pertencia ao grupo e
    # acusar divergência que era do teste, não do produto.
    grupo = get(f"/api/group/{ctx['cnpj']}?depth=3").json()
    ids = [n["id"] for n in grupo["nodes"] if n["kind"] == "company"]

    r = post("/api/credit/review", {"company_ids": ids, "reason": "resiliência"})
    check("revisão ACID responde 200", r.status_code == 200, f"HTTP {r.status_code}")
    caso = r.json()
    check("bloqueou o número de empresas pedido", caso["companies_blocked"] == len(ids),
          f"{caso['companies_blocked']}/{len(ids)}")
    check("garantias da transação vieram no payload",
          caso["read_concern"] == "snapshot" and caso["write_concern"] == "majority")

    r2 = post("/api/credit/review", {"company_ids": ids, "reason": "de novo"})
    check("segunda revisão sobre o mesmo grupo é recusada", r2.status_code == 409, f"HTTP {r2.status_code}")
    d2 = r2.json().get("detail", {})
    check("o 409 devolve o case_id já aberto", d2.get("case_id") == caso["case_id"],
          f"case_id={d2.get('case_id')}")

    detalhe = get(f"/api/credit/case/{caso['case_id']}").json()
    check("o caso original manteve suas empresas", detalhe["ok"] and len(detalhe["companies"]) > 0,
          f"{len(detalhe.get('companies', []))}")

    depois = get(f"/api/group/{ctx['cnpj']}?depth=3").json()
    marcadas = [n for n in depois["nodes"] if n.get("credit_status") == "under_review"]
    check("o grafo mostra as empresas sob revisão", len(marcadas) == len(ids),
          f"{len(marcadas)}/{len(ids)}")

    # Concorrência: N revisões simultâneas sobre os MESMOS documentos.
    def revisa(i: int) -> int:
        return post("/api/credit/review", {"company_ids": ids, "reason": f"conc-{i}"}).status_code

    with futures.ThreadPoolExecutor(max_workers=8) as ex:
        codigos = list(ex.map(revisa, range(8)))
    check("8 revisões concorrentes: sem 500", all(c_ in (200, 409) for c_ in codigos),
          f"códigos={sorted(set(codigos))}")

    r = post("/api/credit/review", {"company_ids": []})
    check("grupo vazio é recusado", r.status_code == 422, f"HTTP {r.status_code}")
    r = post("/api/credit/review", {"company_ids": ["x"] * 6000})
    check("acima do teto de 5000 é recusado", r.status_code == 422, f"HTTP {r.status_code}")
    r = post("/api/credit/review", {"company_ids": [{"$ne": None}]})
    check("operador do Mongo em company_ids é recusado", r.status_code == 422, f"HTTP {r.status_code}")

    r = post(f"/api/credit/close/{caso['case_id']}")
    check("encerrar a revisão responde 200", r.status_code == 200, f"HTTP {r.status_code}")
    r = post(f"/api/credit/close/{caso['case_id']}")
    check("encerrar duas vezes não quebra", r.status_code in (200, 404), f"HTTP {r.status_code}")
    r = post("/api/credit/close/nao_existe")
    check("encerrar caso inexistente devolve 404", r.status_code == 404, f"HTTP {r.status_code}")

    liberadas = get(f"/api/group/{ctx['cnpj']}?depth=3").json()
    check("encerrar libera todas as empresas",
          not [n for n in liberadas["nodes"] if n.get("credit_status") == "under_review"])
    post("/api/demo/reset")


# ------------------------------------------------------------------ plano de execução
def plano(ctx: dict) -> None:
    import os
    import subprocess

    uri = None
    for linha in open(".env"):
        if linha.startswith("MONGODB_URI="):
            uri = linha.split("=", 1)[1].strip()
    if not uri:
        check("MONGODB_URI disponível para o explain", False, "não encontrada em .env")
        return

    script = """
    db = db.getSiblingDB(process.env.MONGODB_DB || "graph_grupo_economico");
    const nomes = db.ownership.getIndexes().map(i => i.name);
    print(JSON.stringify({indices: nomes}));
    """
    out = subprocess.run(["mongosh", uri, "--quiet", "--eval", script],
                         capture_output=True, text=True, timeout=90)
    try:
        dados = json.loads([l for l in out.stdout.splitlines() if l.startswith("{")][-1])
    except Exception:
        check("consegue ler os índices de ownership", False, out.stdout[-120:] or out.stderr[-120:])
        return
    for req in ("owner_id_1", "owned_id_1"):
        check(f"índice {req} existe em ownership", req in dados["indices"])


# ------------------------------------------------------------------ change stream
def change_stream(ctx: dict) -> None:
    post("/api/demo/reset")
    ids = ctx["company_ids"]

    # A: abrir revisão marca as empresas numa transação -> o listener acorda
    antes = get("/api/alerts/recent").json()["listener"]
    caso = post("/api/credit/review", {"company_ids": ids, "reason": "cs"}).json()
    prazo = time.time() + 25
    st = antes
    while time.time() < prazo:
        st = get("/api/alerts/recent").json()["listener"]
        if st["alerts"] > antes["alerts"]:
            break
        time.sleep(1)
    check("abrir revisão dispara alerta em <25s",
          st["alerts"] > antes["alerts"], f"{antes['alerts']} -> {st['alerts']}")

    alertas = get("/api/alerts/recent").json()["alerts"]
    check("o alerta diz quanta exposição entrou sob revisão",
          bool(alertas) and "under_review_limite" in alertas[0])
    check("e coalesce a transação num evento só, com a contagem real",
          bool(alertas) and alertas[0].get("companies", 0) > 1,
          f"companies={alertas[0].get('companies') if alertas else None}")

    # B: encerrar devolve a contrapartida pelo mesmo mecanismo
    antes = get("/api/alerts/recent").json()["listener"]
    post(f"/api/credit/close/{caso['case_id']}")
    prazo = time.time() + 25
    while time.time() < prazo:
        st = get("/api/alerts/recent").json()["listener"]
        if st["checks_published"] > antes["checks_published"]:
            break
        time.sleep(1)
    check("encerrar a revisão também publica evento",
          st["checks_published"] > antes["checks_published"],
          f"{antes['checks_published']} -> {st['checks_published']}")

    check("listener continua vivo", get("/health").json()["checks"]["change_stream"]["running"])

    post(f"/api/credit/close/{caso['case_id']}")
    post("/api/demo/reset")


# ------------------------------------------------------------------ carga
def carga(ctx: dict) -> None:
    def uma(_):
        t0 = time.perf_counter()
        r = get(f"/api/group/{ctx['cnpj']}?depth=3")
        return r.status_code, (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    with futures.ThreadPoolExecutor(max_workers=10) as ex:
        res = list(ex.map(uma, range(40)))
    wall = time.perf_counter() - t0
    codigos = sorted({r[0] for r in res})
    tempos = sorted(r[1] for r in res)
    p50, p95 = tempos[len(tempos) // 2], tempos[int(len(tempos) * 0.95)]
    check("40 consultas concorrentes: todas 200", codigos == [200], f"códigos={codigos}")
    check("p95 sob concorrência abaixo de 5s", p95 < 5000,
          f"p50={p50:.0f}ms p95={p95:.0f}ms em {wall:.1f}s")
    check("/health continua ok depois da carga", get("/health").status_code == 200)
    return {"n": 40, "p50_ms": round(p50, 1), "p95_ms": round(p95, 1), "wall_s": round(wall, 1)}


# ------------------------------------------------------------------ saúde
def saude(ctx: dict) -> None:
    h = get("/health").json()
    check("/health reporta o estado do índice de busca", "search_index" in h["checks"])
    check("/health reporta o estado do change stream", "change_stream" in h["checks"])
    check("/health mede a latência de uma consulta de referência",
          "graphlookup_probe" in h["checks"])
    check("/health/live não depende do banco", get("/health/live").json()["status"] == "alive")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true", help="sem change stream e sem carga")
    args = p.parse_args()

    ctx = contexto()
    blocos = [
        ("Entradas hostis", entradas),
        ("Consistência do payload", consistencia),
        ("Busca escopada", busca_escopada),
        ("Hierarquia e escopo", hierarquia),
        ("Transação ACID", acid),
        ("Plano de execução", plano),
        ("Degradação e saúde", saude),
    ]
    if not args.quick:
        blocos += [("Change Streams", change_stream), ("Carga concorrente", carga)]

    carga_res = None
    for titulo, fn in blocos:
        print(f"\n── {titulo}")
        out = fn(ctx)
        if titulo.startswith("Carga"):
            carga_res = out

    total = _ok + len(_falhas)
    print("\n" + "=" * 60)
    print(f"{_ok}/{total} passaram")
    if carga_res:
        print("carga:", json.dumps(carga_res))
    if _falhas:
        print("falhas:", _falhas)
    sys.exit(1 if _falhas else 0)


if __name__ == "__main__":
    main()
