# Roteiro da apresentação

## Antes de começar

1. `curl -s localhost:8350/health | jq` retorna `"status": "ok"`. Ele valida, de
   uma vez: conexão com o Atlas, contagem das coleções, status dos índices de
   Search/Vector e a latência de um `$graphLookup` de referência.
2. Se `status` vier `degraded`, o campo `checks` diz exatamente o quê. Índice em
   `BUILDING` não é bloqueio: o traversal funciona, só corte os passos 6 e 7.
3. `POST /api/demo/reset` para garantir que nada ficou marcado de uma apresentação
   anterior. Ele também remove as arestas criadas ao vivo no passo 10, então o
   "antes" daquele passo volta a ser verdade.
4. Abrir a UI e conferir que uma conta da **população limpa** renderiza um grafo
   pequeno. Começar a demo já com o resultado na tela mata o passo 2.
5. Anotar um `ring_id` do seletor. Se algo falhar ao vivo, `mongosh` com
   `queries/01_graphlookup_explicit_edges.js` mostra os mesmos números.
6. Testar os passos 8 e 9 uma vez antes: marcar a rede, injetar a transação, ver o
   alerta. Depois `reset`.
7. A primeira chamada de `/api/entry-points` leva cerca de um segundo e as
   seguintes são instantâneas, porque o resultado fica em cache. Abrir a tela uma
   vez antes de começar tira esse segundo do caminho.

## Roteiro (≈15 minutos)

### 1. Abertura — o problema (2 min)
Selecione uma conta da população limpa. Grafo pequeno, sem nada vermelho.
Pergunte: *"com os dados que vocês têm hoje, o que diria sobre essa conta?"*

### 2. A pergunta que muda tudo (1 min)
*"E se essa conta dividisse dispositivo, endereço e chave PIX com outras vinte que
também parecem normais isoladamente?"* Troque para a conta sob suspeita,
profundidade 1. Ainda parece pouco.

### 3. Rodar o traversal ao vivo (3 min)
Profundidade 2, depois 3. Narre o que está acontecendo: **uma** query de
agregação, sem sair do MongoDB, sem sincronizar com outro sistema. O tempo na tela
vem da execução, não de `benchmarks.md`.

### 4. A rede completa (2 min)
Na profundidade 3 a rede injetada fecha. Nós vermelhos, borda grossa. Aponte a
métrica "nós em rede" — é o ground truth batendo.

### 5. Onde começa a doer, e é você quem levanta (2 min)
Suba para 4, 5 e 6. **O grafo não cresce**: satura nos 30 membros. Diga isso em voz
alta, porque é o contrário do que se espera ouvir numa demonstração. A rede é um
componente fechado, e profundidade extra não alcança mais ninguém.

Aí mostre onde o custo mora de verdade. Deixe os números anotados, de
`queries/benchmarks.md`, seção Escala. Num grafo de 2,4 milhões de arestas:

- dois saltos alcançam 21 mil nós em 254 ms;
- três saltos, sem poda, **não terminam**: estouram o limite de 100 MB que o
  `$graphLookup` tem para montar o resultado, depois de moer entre 40 e 180
  segundos;
- os mesmos três saltos, com poda, terminam em **1 segundo**.

Esse par é o argumento inteiro. A poda não é um filtro de conveniência, é o que
torna a consulta possível — e ela funciona porque acontece durante o traversal,
antes de o resultado crescer. Um filtro depois do estágio não salvaria nada.

Ligue e desligue o toggle **Podar hubs** enquanto fala. Na rede pequena da demo a
diferença é de milissegundos, porque a materialização já descartou os hubs:
`materialize_connections.py --report-only` mostra os 3 endereços (8.618 pessoas) e
os 5 dispositivos (35.251 contas) que ficaram de fora. Diga isso também. Mostrar
onde a otimização **não** faz diferença é o que dá crédito ao caso em que ela faz.

### 6. Entity resolution difusa (2 min)
O painel de Atlas Search já mostra qual nome do dataset está grafado errado.
Busque por ele: o registro real da rede aparece no topo. Um `$graphLookup` por
igualdade exata nunca faria essa ligação — e não foi preciso um segundo motor de
busca.

### 7. Similaridade semântica (2 min, opcional)
Vector Search sobre `reason_text`. Corte este passo primeiro se o tempo apertar.

### 8. Ação — marcar a rede (2 min)
**Marcar N nós sob investigação.** Uma transação ACID atualiza contas, pessoas e
o registro de auditoria juntos. Mostre `readConcern: snapshot` e
`writeConcern: majority` na tela e explique por que um estado intermediário —
metade das contas bloqueada, auditoria incoerente — é pior do que não ter agido.

### 9. Tempo real (2 min)
**Injetar transação na rede.** O change stream dispara e o alerta aparece com a
latência da verificação. Nenhum polling, nenhum job agendado.

### 10. O grafo se atualiza sozinho (1 min)
Se alguém perguntar como as arestas ficam frescas em produção — e costumam
perguntar — chame `POST /api/demo/link-accounts`. Ele insere duas transações que
fazem duas pessoas sem nenhuma ligação passarem a usar o mesmo dispositivo.

Antes: `GET /api/connections/between?a=…&b=…` responde `connected: false`. Uns dois
segundos depois, `true`, com `source: "change_stream"`. E a expansão seguinte já
percorre a aresta nova.

Diga também o que esse caminho não faz: ele adiciona e atualiza aresta, não poda
retroativamente nem faz backfill. O padrão correto é os dois, change stream para o
frescor e batch para consistência. Está em
`docs/adr/0003-manutencao-incremental-de-arestas.md`.

### 11. Fechamento (1-2 min)
Um sistema a menos para operar, sincronizar, dar backup e proteger. Ofereça
`COMPETITIVE.md` para quem quiser aprofundar em quando isso **não** se aplica.

## Perguntas frequentes e respostas preparadas

**"Isso escala para bilhões de registros?"**
Não prometa escala ilimitada, e traga números em vez de adjetivos. Medido num
grafo de 2,4 milhões de arestas: dois saltos alcançam 21 mil nós em 254 ms, e três
saltos **não terminam** sem poda — estouram o limite de 100 MB que o
`$graphLookup` tem para montar o resultado. Com poda, o mesmo traversal leva um
segundo.

Há ainda a restrição de sharding: o `$graphLookup` não lê de forma distribuída de
uma coleção shardada. Para volumes maiores o padrão muda — isolar as coleções de
grafo como não-shardadas, ou pré-computar subgrafos. Ver `LIMITATIONS.md`, seções
1 e 5.

**"Vocês têm PageRank / detecção de comunidade nativo?"**
Não. Explique o padrão de integração com Spark/NetworkX de `LIMITATIONS.md §3` e
pergunte se o caso de uso precisa disso rodando continuamente ou se uma análise
periódica em batch resolve. Essa pergunta costuma mudar a conversa.

**"Por que não Neo4j direto, já que grafo é o núcleo do problema?"**
Se o caso for genuinamente *graph-first* com algoritmos pesados contínuos, a
pergunta é legítima. Use as quatro perguntas de qualificação no fim de
`COMPETITIVE.md` em vez de responder na defensiva.

**"Esse dado sintético não foi desenhado para funcionar?"**
Foi, e vale dizer: a topologia está documentada em
`docs/adr/0001-topologia-do-dado-sintetico.md`, inclusive as três alternativas
descartadas por medição. O que o dado sintético **não** faz é esconder a
degradação — ela aparece na tela no passo 5.

**"Quanto custou embeddar 600 mil transações?"**
Menos do que parece, e por um motivo que vale explicar:
`reason_text` vem de um pool de templates, então o embedder deduplica e chama a
Voyage algumas dezenas de vezes. Num dataset real de campo livre isso renderia bem
menos — o custo é proporcional aos textos distintos, não às linhas. Ver
`docs/adr/0002-vetores-em-512d-quantizados.md`.
