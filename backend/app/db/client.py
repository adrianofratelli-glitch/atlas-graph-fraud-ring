"""Camada de acesso ao Mongo. Nenhuma rota importa pymongo diretamente.

Duas garantias vivem aqui:

- `serverSelectionTimeoutMS` explícito, para que uma indisponibilidade vire erro
  rápido em vez de uma tela travada no meio da apresentação;
- `with_retry`, que repete apenas falhas transitórias de rede (`AutoReconnect`,
  `NetworkTimeout`) com backoff exponencial. Erro de lógica, de validação ou de
  escrita conflitante **não** é repetido: repetir esconde bug.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Callable, TypeVar

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import AutoReconnect, ConnectionFailure, NetworkTimeout

from app.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T")

TRANSIENT = (AutoReconnect, NetworkTimeout, ConnectionFailure)
MAX_ATTEMPTS = 3


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    s = get_settings()
    return MongoClient(
        s.mongodb_uri,
        serverSelectionTimeoutMS=s.server_selection_timeout_ms,
        connectTimeoutMS=s.server_selection_timeout_ms,
        retryWrites=True,
        appname="atlas-graph-grupo-economico",
    )


def get_db() -> Database:
    return get_client()[get_settings().db_name]


def with_retry(fn: Callable[[], T], what: str = "operação") -> T:
    """Backoff exponencial só para falha transitória de rede."""
    delay = 0.25
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fn()
        except TRANSIENT as exc:
            if attempt == MAX_ATTEMPTS:
                log.error("%s falhou após %d tentativas: %s", what, attempt, exc)
                raise
            log.warning("%s: falha transitória (%s), tentativa %d/%d", what, exc, attempt, MAX_ATTEMPTS)
            time.sleep(delay)
            delay *= 2
    raise AssertionError("inalcançável")
