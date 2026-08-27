"""Configuração. Tudo que muda entre ambientes passa por aqui, nada lê os.environ direto."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Settings:
    mongodb_uri: str = os.environ["MONGODB_URI"]
    db_name: str = os.getenv("MONGODB_DB", "graph_fraud_ring")

    search_index: str = os.getenv("ATLAS_SEARCH_INDEX_NAME", "people_entity_resolution")
    vector_index: str = os.getenv("VECTOR_INDEX_NAME", "transactions_reason_vector")

    voyage_api_key: str = os.getenv("VOYAGE_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "voyage-3-lite")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", 512))

    # Cap de profundidade. O frontend propõe um valor; o backend nunca aceita mais
    # do que isto. Uma expansão sem teto é o jeito mais rápido de travar uma demo.
    depth_cap: int = int(os.getenv("GRAPH_MAX_DEPTH_CAP", 6))
    default_depth: int = int(os.getenv("GRAPH_DEFAULT_DEPTH", 4))
    hub_threshold: int = int(os.getenv("HUB_FANOUT_THRESHOLD", 50))

    # Teto de nós devolvidos ao frontend. vis-network fica inutilizável muito antes
    # disso, e o navegador é o gargalo, não o Atlas.
    max_nodes: int = int(os.getenv("GRAPH_MAX_NODES", 1200))

    server_selection_timeout_ms: int = 5_000

    # Teto de tempo de uma agregação de traversal.
    #
    # Medido: num grafo de 2,4 M de arestas, um `$graphLookup` de profundidade 2
    # a partir de um nó de grau alto moeu **97 segundos** antes de estourar o
    # limite de 100 MB do documento de saída. Sem teto, o cliente fica olhando uma
    # tela parada por um minuto e meio e o cluster queima CPU para depois falhar.
    # Com teto, vira erro em 15 s e a UI diz o que fazer.
    graph_max_time_ms: int = int(os.getenv("GRAPH_MAX_TIME_MS", 15_000))
    # --- Números do cliente, para o caso de negócio -------------------------
    #
    # Estes NÃO são medições e a tela diz isso. A exposição em reais e a contagem
    # de operações saem do dado real; quanto tempo uma investigação custa hoje só
    # o banco sabe, então é entrada do apresentador, não um número inventado por
    # nós. Deixar um palpite disfarçado de medição contamina a credibilidade de
    # tudo que foi medido de verdade.
    analyst_hours_per_case: float = float(os.getenv("ANALYST_HOURS_PER_CASE", 4))
    analyst_cost_per_hour: float = float(os.getenv("ANALYST_COST_PER_HOUR", 120))
    currency: str = os.getenv("CURRENCY", "R$")

    port: int = int(os.getenv("BACKEND_PORT", 8350))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
