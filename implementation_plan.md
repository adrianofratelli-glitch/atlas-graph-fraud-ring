# Como este projeto foi construído

Capa do briefing de construção. Os três arquivos em `docs/briefing/` guardam
arquitetura, modelagem e interface; este arquivo diz o que o projeto prova, o que ela
não prova, e em que ordem foi construída.

## O que ele demonstra

Que traversal multi-hop, entity resolution difusa, similaridade semântica,
marcação transacional e alerta em tempo real cabem **na mesma plataforma que já
guarda o dado operacional** — sem um banco de grafo ao lado e sem pipeline de
sincronização entre dois sistemas.

## O que ele não demonstra

Comportamento em bilhões de arestas, algoritmos de grafo pesados rodando
continuamente (PageRank, Louvain, centralidade) e `$graphLookup` sobre coleção
shardada. Isso está em `LIMITATIONS.md` e é leitura obrigatória antes de qualquer
apresentação — a credibilidade da conversa depende de levantar essas limitações
antes que o arquiteto do cliente as levante.

## Índice do briefing

| Arquivo | Conteúdo |
|---|---|
| [`docs/briefing/01-arquitetura.md`](docs/briefing/01-arquitetura.md) | camadas, invariantes, ordem de construção, como rodar |
| [`docs/briefing/02-mongodb.md`](docs/briefing/02-mongodb.md) | coleções, índices, pipelines, materialização de arestas, seeds |
| [`docs/briefing/03-interface-fluxos.md`](docs/briefing/03-interface-fluxos.md) | telas, estados, degradação, roteiro da demo |

Documentos de posicionamento, fora do briefing de construção:

| Arquivo | Conteúdo |
|---|---|
| [`README.md`](README.md) | capa pública do repositório |
| [`LIMITATIONS.md`](LIMITATIONS.md) | onde a tese **não** se aplica |
| [`COMPETITIVE.md`](COMPETITIVE.md) | MongoDB × Neo4j × Neptune, e as perguntas de qualificação |
| [`queries/benchmarks.md`](queries/benchmarks.md) | números medidos neste cluster, não estimados |
| [`docs/demo-script.md`](docs/demo-script.md) | roteiro de 12-15 minutos e checklist pré-demo |
| [`docs/adr/`](docs/adr/) | decisões de arquitetura registradas |
| [`tests/`](tests/) | suíte hostil, teste de escala e ajuste de índice |

## Ordem de construção

1. **Dados antes de código.** O gerador (`data-generator/`) veio primeiro porque a
   topologia do dado sintético é o que determina se a demo funciona. Três versões
   da topologia foram descartadas por medição, não por opinião — ver
   `docs/adr/0001-topologia-do-dado-sintetico.md`.
2. **Índices e materialização de arestas** (`schema/`, `materialize_connections.py`).
3. **Queries em `mongosh`** (`queries/`), legíveis sem subir nada.
4. **Backend** (`backend/`), com a camada de acesso isolada em `app/db/`.
5. **Frontend** (`frontend/`), seguindo o conjunto de tokens visuais compartilhado.
6. **Benchmarks medidos** (`queries/bench.py`) e preenchimento de
   `benchmarks.md` e das tabelas do `LIMITATIONS.md`.
7. **Tentativa de quebrar** (`tests/test_resilience.py`), que achou três bugs.
8. **Medição de escala** (`tests/scale_graph.py`), que revelou o teto de 100 MB do
   `$graphLookup` e mostrou que a poda é o que torna o traversal profundo
   possível.

## Estado

Construída e executada contra um cluster Atlas M20. A API sobe na porta 8350 e a
interface na 5350.
