#!/usr/bin/env python3
"""Injeta os números de `bench-results.json` no template de `benchmarks.md`.

Existe para que a tabela publicada nunca seja digitada à mão — o único jeito de
ela divergir da medição é alguém editar o markdown, e aí o próximo `bench.py`
corrige.

    .venv/bin/python queries/bench.py --runs 20
    .venv/bin/python queries/fill_benchmarks.py
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
TEMPLATE = HERE / "benchmarks.template.md"
OUT = HERE / "benchmarks.md"


def main() -> None:
    data = json.loads((HERE / "bench-results.json").read_text())
    src = (TEMPLATE if TEMPLATE.exists() else OUT).read_text()

    counts = data["counts"]
    # Separador de milhar no padrão en-US: os documentos publicados são em inglês,
    # e trocar por ponto produziria "150.000" no meio de um texto que usa vírgula.
    counts_str = " · ".join(f"{v:,} {k}" for k, v in counts.items())

    table_b = "\n".join(
        f"| {d} | {v['mean_ms']} | {v['p95_ms']} | {v['nodes']} |" for d, v in sorted(data["pattern_b"].items())
    )
    table_a = "\n".join(
        f"| {d} | {v['mean_ms']} | {v['p95_ms']} | {v['accounts']} |" for d, v in sorted(data["pattern_a"].items())
    )
    rotulo = {
        "sem_poda": "without `restrictSearchWithMatch`",
        "com_poda": "with weight pruning (`weight <= 50`)",
    }
    table_prune = "\n".join(
        f"| {rotulo[k]} | {v['mean_ms']} | {v['nodes']} |" for k, v in data["prune"].items()
    )

    medido = datetime.fromisoformat(data["measured_at"]).strftime("%Y-%m-%d")
    for needle, value in [
        ("PING_MEAN", str(data["network_ping"]["mean_ms"])),
        ("PING_P95", str(data["network_ping"]["p95_ms"])),
        ("COUNTS", counts_str),
        ("MEASURED_AT", medido),
        ("TABLE_B", table_b),
        ("TABLE_A", table_a),
        ("TABLE_PRUNE", table_prune),
    ]:
        src = src.replace(needle, value)

    if not TEMPLATE.exists():
        TEMPLATE.write_text((HERE / "benchmarks.md").read_text())
    OUT.write_text(src)
    print(f"{OUT} atualizado com a medição de {medido}")


if __name__ == "__main__":
    main()
