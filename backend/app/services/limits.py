"""Contenção por classe de consulta (bulkhead).

## O problema que isto resolve, medido

`tests/stress.py` com 64 clientes simultâneos contra um M20 compartilhado:
nenhuma resposta 5xx, nenhum dado errado — mas a p95 geral foi a **8,8 s**, e a
culpa não era distribuída. Duas classes de consulta puxaram todo o resto:

| Caminho | p50 @64 | p95 @64 |
|---|---|---|
| carteira de um escopo grande (regional: 129 assessores, 51 mil exposições) | 854 ms | 8,8 s |
| concentração (agregação do grupo + busca vetorial) | 8,2 s | 12,6 s |
| cadeia societária | 879 ms | 2,0 s |
| visibilidade de uma conta | 530 ms | 1,9 s |

Sem contenção, uma consulta analítica pesada ocupa a mesma fila da consulta
pontual que a tela precisa responder em milissegundos. O resultado é o pior dos
dois mundos: a pesada não fica rápida e a leve fica lenta.

## A decisão

Um semáforo por classe. Consulta analítica tem poucas vagas; quem não consegue
vaga em `ESPERA_S` recebe **429 com `Retry-After`** — rápido e explícito — em vez
de entrar numa fila que ninguém consegue estimar.

Isso é deliberado e vale dizer na conversa técnica: um sistema honesto sob
saturação **recusa cedo**. Enfileirar tudo transforma um pico de carga em
timeouts espalhados por toda a aplicação, incluindo os caminhos que ainda
teriam capacidade de responder.

O traversal da cadeia societária e a checagem de visibilidade **não** passam por
aqui: são o caminho interativo da tela, custam poucos milissegundos de banco, e
degradar eles para proteger uma agregação analítica seria proteger a coisa errada.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

from fastapi import HTTPException

# Vagas simultâneas por classe. Números pequenos de propósito: o cluster da demo é
# um M20 compartilhado, e o objetivo é preservar a latência do caminho interativo,
# não maximizar throughput analítico.
VAGAS = {"carteira": 4, "concentracao": 2}
ESPERA_S = 0.75

_semaforos = {nome: threading.BoundedSemaphore(n) for nome, n in VAGAS.items()}
_recusas: dict[str, int] = {nome: 0 for nome in VAGAS}


@contextmanager
def vaga(classe: str):
    """Ocupa uma vaga da classe, ou recusa com 429 em `ESPERA_S`."""
    sem = _semaforos[classe]
    if not sem.acquire(timeout=ESPERA_S):
        _recusas[classe] += 1
        raise HTTPException(
            429,
            detail={
                "feature": classe,
                "error": "consulta analítica saturada",
                "hint": (
                    f"há {VAGAS[classe]} execuções simultâneas desta classe. "
                    "Repita em alguns segundos — recusar cedo protege a latência "
                    "da consulta interativa."
                ),
            },
            headers={"Retry-After": "2"},
        )
    try:
        yield
    finally:
        sem.release()


def estado() -> dict[str, dict[str, int]]:
    """Para o /health: quantas vagas existem e quantas recusas já aconteceram."""
    return {
        nome: {"vagas": VAGAS[nome], "recusas": _recusas[nome]}
        for nome in VAGAS
    }
