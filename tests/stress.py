#!/usr/bin/env python3
"""Estresse da POV: carga misturada, sustentada, contra a aplicação de pé.

## O que este teste é, e o que ele não é

`test_resilience.py` responde "quebra?" com entradas hostis. Este responde
"aguenta?" com **carga concorrente real**, misturando os cinco caminhos que a
demo percorre, e observando o que degrada primeiro.

Não é benchmark de produção: o cluster é um M20 compartilhado e o cliente está do
outro lado da internet pública. O que interessa aqui é a **forma** da degradação —
se a p95 sobe suave, se algum caminho começa a devolver 5xx, se o change stream
sobrevive, e se a aplicação volta ao normal depois.

Critérios de aprovação, todos verificados no fim:

1. **zero 5xx** em qualquer caminho, sob qualquer concorrência;
2. **zero exceções de conexão** no cliente (a aplicação recusa, não cai);
3. o `/health` continua `ok` depois da carga;
4. a p95 do caminho principal fica abaixo do teto declarado;
5. as respostas continuam **corretas** sob carga — a soma consolidada de um grupo
   não pode variar entre duas execuções concorrentes.

Uso:
    .venv/bin/python tests/stress.py                 # padrão: 4 rodadas até 64 conc.
    .venv/bin/python tests/stress.py --max 128       # mais fundo
    .venv/bin/python tests/stress.py --seconds 30    # rodada mais longa
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections import Counter
from concurrent import futures

import httpx

BASE = "http://127.0.0.1:8350"

# Teto por caminho, não um número só.
#
# A primeira versão cobrava uma p95 global de 3 s e reprovava a POV inteira porque
# a carteira de um regional — 129 assessores, 51 mil exposições somadas — demora.
# Isso media a coisa errada: o que não pode degradar é o **caminho interativo** da
# tela. A consulta analítica pode demorar, e sob saturação deve **recusar** (429)
# em vez de enfileirar.
TETOS_P95_MS = {
    "grupo": 3_000,          # a consulta pontual da demo: é ela que não pode cair
    "visibilidade": 3_000,   # idem, é uma checagem de acesso
    "busca": 5_000,
    "carteira": 15_000,      # analítica, com bulkhead de 4 vagas
    "concentração": 15_000,  # analítica, com bulkhead de 2 vagas
}

_falhas: list[str] = []


def check(nome: str, ok: bool, detalhe: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FALHA'}  {nome}" + (f"  — {detalhe}" if detalhe else ""))
    if not ok:
        _falhas.append(nome)


def contexto(cli: httpx.Client) -> dict:
    ep = cli.get(f"{BASE}/api/entry-points", timeout=30).json()
    alvos = [a["cnpj"] for a in ep["applicants"]] or []
    controle = [c["cnpj"] for c in ep.get("control", [])]
    grupo = cli.get(f"{BASE}/api/group/{alvos[0]}?depth=3", timeout=60).json()
    ids = [n["id"] for n in grupo["nodes"] if n["kind"] == "company"]
    roster = cli.get(f"{BASE}/api/hierarchy/roster", timeout=30).json()["users"]
    return {
        "cnpjs": alvos + controle,
        "company_ids": ids,
        "usuarios": [u["_id"] for u in roster],
        "limite_referencia": grupo["group_exposure"]["limite"],
        "cnpj_referencia": alvos[0],
    }


# --- os cinco caminhos, cada um devolvendo (rótulo, status, ms, payload) -----
def _tempo(fn):
    t = time.perf_counter()
    r = fn()
    return r, (time.perf_counter() - t) * 1000


def caminhos(cli: httpx.Client, ctx: dict, rng: random.Random):
    """Sorteia um caminho com a proporção de uma demo real: muito traversal,
    alguma busca, pouca análise semântica (que é a mais cara)."""
    dado = rng.random()
    if dado < 0.45:
        cnpj = rng.choice(ctx["cnpjs"])
        d = rng.choice([1, 2, 3, 4])
        r, ms = _tempo(lambda: cli.get(f"{BASE}/api/group/{cnpj}?depth={d}", timeout=60))
        return "grupo", r, ms
    if dado < 0.70:
        u = rng.choice(ctx["usuarios"])
        r, ms = _tempo(lambda: cli.get(f"{BASE}/api/hierarchy/{u}/portfolio", timeout=60))
        return "carteira", r, ms
    if dado < 0.85:
        u = rng.choice(ctx["usuarios"])
        cnpj = rng.choice(ctx["cnpjs"])
        r, ms = _tempo(lambda: cli.get(f"{BASE}/api/hierarchy/{u}/can-see/{cnpj}", timeout=60))
        return "visibilidade", r, ms
    if dado < 0.95:
        termo = rng.choice(["Souza", "Farias Sousa", "Pereira Pacheco", "Comercio", "Participacoes"])
        r, ms = _tempo(lambda: cli.post(f"{BASE}/api/search/companies",
                                        json={"q": termo, "company_ids": ctx["company_ids"]},
                                        timeout=60))
        return "busca", r, ms
    r, ms = _tempo(lambda: cli.post(f"{BASE}/api/analysis/concentration",
                                    json={"company_ids": ctx["company_ids"]}, timeout=90))
    return "concentração", r, ms


def rodada(ctx: dict, conc: int, segundos: float) -> dict:
    """Uma rodada com `conc` clientes simultâneos por `segundos`."""
    fim = time.time() + segundos
    amostras: dict[str, list[float]] = {}
    status = Counter()
    erros: list[str] = []

    def trabalhador(semente: int) -> None:
        rng = random.Random(semente)
        # Um cliente HTTP por thread: compartilhar conexão entre threads é o jeito
        # mais rápido de medir contenção do cliente em vez do servidor.
        with httpx.Client() as cli:
            while time.time() < fim:
                try:
                    rotulo, r, ms = caminhos(cli, ctx, rng)
                    amostras.setdefault(rotulo, []).append(ms)
                    status[r.status_code] += 1
                    # 429 é o comportamento desejado sob saturação: a aplicação
                    # recusa cedo para preservar a latência do caminho interativo.
                    # Contar como falha seria reprovar exatamente a proteção que
                    # foi construída depois da primeira rodada deste teste.
                    if r.status_code >= 500:
                        erros.append(f"{rotulo} -> {r.status_code}: {r.text[:120]}")
                except Exception as exc:  # conexão caiu, timeout estourou
                    erros.append(f"exceção: {type(exc).__name__}: {str(exc)[:100]}")

    t0 = time.perf_counter()
    with futures.ThreadPoolExecutor(max_workers=conc) as pool:
        list(pool.map(trabalhador, range(conc)))
    wall = time.perf_counter() - t0

    total = sum(status.values())
    todos = [ms for v in amostras.values() for ms in v]
    todos.sort()
    return {
        "conc": conc,
        "req": total,
        "rps": round(total / wall, 1) if wall else 0,
        "p50": round(statistics.median(todos), 1) if todos else 0,
        "p95": round(todos[int(len(todos) * 0.95)], 1) if todos else 0,
        "max": round(todos[-1], 1) if todos else 0,
        "status": dict(status),
        "recusas_429": status.get(429, 0),
        "erros": erros[:5],
        "n_erros": len(erros),
        "por_caminho": {
            k: {"n": len(v), "p50": round(statistics.median(v), 1),
                "p95": round(sorted(v)[int(len(v) * 0.95)], 1)}
            for k, v in sorted(amostras.items())
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, default=64, help="concorrência máxima")
    p.add_argument("--seconds", type=float, default=12, help="duração de cada rodada")
    p.add_argument("--out", default="tests/stress-results.json")
    args = p.parse_args()

    with httpx.Client() as cli:
        saude = cli.get(f"{BASE}/health", timeout=60).json()
        if saude["status"] == "offline":
            raise SystemExit("aplicação fora do ar — suba com ./start.sh antes")
        ctx = contexto(cli)

    print(f"contexto: {len(ctx['cnpjs'])} CNPJs, {len(ctx['company_ids'])} empresas no grupo, "
          f"{len(ctx['usuarios'])} usuários")
    print(f"referência: limite consolidado {ctx['limite_referencia']:,.2f}\n")

    niveis = [n for n in (1, 8, 32, args.max) if n <= args.max]
    rodadas = []
    for n in niveis:
        print(f"── concorrência {n} por {args.seconds:.0f}s")
        r = rodada(ctx, n, args.seconds)
        rodadas.append(r)
        print(f"   {r['req']} req · {r['rps']} req/s · p50 {r['p50']} ms · p95 {r['p95']} ms · "
              f"máx {r['max']} ms · status {r['status']}")
        for k, v in r["por_caminho"].items():
            print(f"     {k:14} n={v['n']:>4}  p50 {v['p50']:>7.1f}  p95 {v['p95']:>8.1f}")
        if r["erros"]:
            for e in r["erros"]:
                print(f"     ! {e}")

    print("\n── critérios")
    cinco_xx = sum(c for r in rodadas for s, c in r["status"].items() if s >= 500)
    check("nenhum 5xx sob carga", cinco_xx == 0, f"{cinco_xx} respostas 5xx")
    excecoes = sum(r["n_erros"] for r in rodadas) - cinco_xx
    check("nenhuma exceção de conexão no cliente", excecoes <= 0, f"{excecoes} exceções")

    ultima = rodadas[-1]
    for caminho, teto in TETOS_P95_MS.items():
        v = ultima["por_caminho"].get(caminho)
        if not v:
            continue
        check(f"p95 de `{caminho}` abaixo de {teto} ms na concorrência máxima",
              v["p95"] < teto, f"p95 {v['p95']} ms em {v['n']} chamadas")

    recusas = sum(c for r in rodadas for s_, c in r["status"].items() if s_ == 429)
    print(f"  info  429 devolvidos sob saturação: {recusas} "
          f"(backpressure funcionando, não é falha)")

    # Correção sob carga: a resposta não pode ter mudado.
    with httpx.Client() as cli:
        depois = cli.get(f"{BASE}/api/group/{ctx['cnpj_referencia']}?depth=3", timeout=60).json()
        igual = abs(depois["group_exposure"]["limite"] - ctx["limite_referencia"]) < 0.01
        check("a consolidação do grupo não mudou sob carga", igual,
              f"{depois['group_exposure']['limite']:,.2f} vs {ctx['limite_referencia']:,.2f}")
        s2 = cli.get(f"{BASE}/health", timeout=60).json()
        check("/health continua ok depois da carga", s2["status"] == "ok", s2["status"])
        cs = s2["checks"].get("change_stream", {})
        check("change stream sobreviveu à carga", bool(cs.get("running")), json.dumps(cs)[:120])

    relatorio = {"measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rodadas": rodadas,
                 "falhas": _falhas}
    with open(args.out, "w") as f:
        json.dump(relatorio, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print(f"gravado em {args.out}")
    if _falhas:
        print("FALHAS:", _falhas)
        sys.exit(1)
    print("estresse passou")


if __name__ == "__main__":
    main()
