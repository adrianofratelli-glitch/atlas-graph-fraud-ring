# ADR 0003 — Manutenção incremental de arestas pelo change stream

**Status:** aceito · **Data:** 2026-08-26

## Contexto

`materialize_connections.py` reconstrói `connections` inteira e leva ~15 minutos
no volume padrão. Isso é aceitável para semear a POV e inaceitável em produção: a
primeira pergunta de um arquiteto sobre grafo materializado é **"e quando chega
uma transação nova, o grafo fica desatualizado até o próximo batch?"**.

Sem resposta para isso, a tese da POV fica pela metade. O argumento é que o grafo
vive junto do dado operacional — mas se o grafo só é atualizado de madrugada, ele
é um data mart com outro nome, e o cliente está certo em desconfiar.

## Alternativas

| Opção | Frescor | Custo | Por quê não |
|---|---|---|---|
| Batch noturno | horas | baixo | é o problema, não a solução |
| Batch de minuto em minuto | ~1 min | alto (varre tudo) | reconstrói 118k arestas para reagir a 3 transações |
| Trigger no lado da aplicação | imediato | médio | acopla a escrita do grafo a cada caminho de código que insere transação; qualquer produtor novo esquece |
| **Change stream** | **~2 s** | **baixo** | o evento já existe e já está sendo lido |

## Decisão

A manutenção roda em `backend/app/services/edge_maintenance.py`, **no mesmo
cursor de change stream** que já dispara os alertas. Abrir um segundo stream
dobraria a leitura do oplog para consumir exatamente os mesmos eventos.

As regras do job em lote são preservadas literalmente — se divergirem, o grafo
incremental e o reconstruído deixam de ser o mesmo grafo:

- só `from_account` conta (quem **operou** o dispositivo, não quem recebeu);
- fan-out acima de `HUB_FANOUT_THRESHOLD` não vira aresta;
- `_id` determinístico via `uuid5` no **mesmo namespace** do gerador, então
  reprocessar um evento reescreve o mesmo documento em vez de duplicar — e uma
  aresta criada aqui colide com a que o batch criaria para o mesmo par.

A aresta ganha `source: "change_stream"`, que é o que permite auditar depois
quanto do grafo veio de cada caminho.

## Consequência medida

`POST /api/demo/link-accounts` insere duas transações que fazem duas pessoas sem
nenhuma relação dividirem um dispositivo:

| | |
|---|---|
| Aresta antes da inserção | não existe |
| Aresta depois | **~2 s**, `source: change_stream`, `weight: 2` |
| Traversal seguinte | já percorre a aresta nova |

Coberto por `tests/test_resilience.py`, bloco *Manutenção incremental de aresta*,
que também verifica que reprocessar não duplica e que o listener sobrevive.

## O que isto ainda não resolve — dizer na demo

- **Remoção.** Uma aresta que deixou de fazer sentido (o dispositivo virou hub
  depois de crescer) só some no próximo batch. O incremental adiciona e atualiza;
  não poda retroativamente.
- **Backfill.** Change stream só vê o que chega depois que ele está de pé. Carga
  inicial e recuperação de janela longa continuam sendo trabalho do batch.
- **Ordem de grandeza.** Foi medido com uma transação por vez. Um pico de milhares
  de transações por segundo no mesmo dispositivo exigiria agrupar por janela em
  vez de reagir evento a evento.

O padrão correto é **os dois**: change stream para o frescor, batch periódico para
consistência e poda. Isso é uma escolha de arquitetura, não uma limitação do
MongoDB — e é a mesma que se faria com qualquer banco de grafo dedicado do outro
lado de um pipeline.
