# 03 — Interface e fluxos

O visual segue um conjunto de tokens compartilhado entre os projetos de
demonstração: fundo escuro, verde como sinal de ação e sucesso, azul para
informação, amarelo para alerta e vermelho para risco. `src/pov-signature.css`
carrega essa assinatura e é importado depois do stylesheet da aplicação.

## Modo palco

Uma tela. O primeiro viewport tem **uma tese** (a transação isolada parece
legítima), **uma ação** (a profundidade do traversal) e **uma evidência** (o grafo
com os nós de rede em vermelho e o tempo de resposta medido).

Tudo que não decide nada para o apresentador saiu do caminho: a query executada
fica em `<details>`, e os painéis de Search, Vector e alertas ficam abaixo do
grafo, na ordem do roteiro.

## Layout — uma tela, sem rolagem

```
┌─ topbar 52px: marca · tese em uma linha · saúde ───────────────────────┐
├──────────┬────────────────────────────────────────┬────────────────────┤
│ controles│ métricas: nós · arestas · em rede ·     │ inspetor (abas)    │
│          │           tempo · salto · legenda       │ ┌────────────────┐ │
│ entrada  │ ┌────────────────────────────────────┐  │ │ Nó │Busca│Sem…│ │
│ profund. │ │                                    │  │ ├────────────────┤ │
│ arestas  │ │   canvas (ocupa a altura restante) │  │ │ detalhe do nó  │ │
│ ação     │ │                    [+] [−] [⤢]     │  │ │ vizinhos-chips │ │
│          │ └────────────────────────────────────┘  │ └────────────────┘ │
│          │                                         │ ▸ Query executada  │
└──────────┴────────────────────────────────────────┴────────────────────┘
```

`body { overflow: hidden }` e `.app { height: 100dvh }`: a página nunca rola. Só o
trilho de controles e o corpo do inspetor rolam internamente, quando precisam.

A versão anterior empilhava grafo, busca, vetor e alertas numa coluna de 2.600 px
— o apresentador rolava para achar cada evidência e perdia o grafo de vista. As
três evidências viraram abas do inspetor, ao lado do grafo, que continua visível o
tempo todo.

## Interação com o grafo

Três padrões emprestados de ferramentas de investigação de grafo:

- **Realce de vizinhos.** Passar o mouse sobre um nó acende seus vizinhos diretos
  e atenua todo o resto para ~16% de opacidade. A cadeia que interessa fica
  sozinha na tela. O nó em foco **mantém a cor do seu tipo** e ganha borda azul —
  trocar o fundo apagaria a informação de "está numa rede".
- **Divulgação progressiva.** Nada de tooltip nativo: o detalhe do nó (saltos,
  grau, marcas de risco, `_id`) vive na aba **Nó** do inspetor, junto com os
  vizinhos diretos como atalhos clicáveis.
- **Reacomodação no arraste.** O layout usa `forceAtlas2Based` com
  `avoidOverlap: 0.85`. A física fica **congelada em repouso** — o design system
  proíbe animação ambiente contínua — e volta durante o arraste, então nós
  próximos abrem espaço em vez de se sobreporem. Congela de novo quando o motor
  estabiliza, com teto de 2,5 s.

O tamanho do nó escala pela raiz do grau dentro do subgrafo: quem concentra
vínculos salta aos olhos antes de qualquer rótulo ser lido. Nós de rede recebem um
halo vermelho discreto.

Controles de enquadramento (`+`, `−`, `⤢`) ficam sobre o canvas: depois de
arrastar ou dar zoom, o apresentador volta ao enquadramento sem recarregar.

## Estados

| Estado | O que a tela faz |
|---|---|
| Backend fora do ar | badge `✕ backend offline`; o canvas diz o que aconteceu |
| Índice de Search/Vector `BUILDING`/`MISSING` | badge no painel daquele recurso e um aviso explicando; o grafo continua funcionando |
| Sem `VOYAGE_API_KEY` | mesmo caminho, com status `NO_EMBEDDING_KEY` |
| Ponto de entrada sem vínculo | canvas vazio com texto, não spinner infinito |
| Resultado truncado | aviso apontando `LIMITATIONS.md §4`, com a contagem real de arestas encontradas |
| Nenhum alerta ainda | texto dizendo qual ação produz um, não um vazio mudo |

Nenhum estado é comunicado só por cor: nó de rede tem borda mais grossa e o
`ring_id` no tooltip, além do vermelho.

## Streaming

Alertas chegam por `EventSource` em `/api/alerts/stream`. O backend manda
heartbeat a cada 15 s para o navegador não considerar a conexão ociosa morta;
`EventSource` reconecta sozinho. A UI mantém os 12 alertas mais recentes.

## Roteiro da demo

Completo em [`docs/demo-script.md`](../demo-script.md), incluindo o checklist
pré-demo. Resumo do caminho pela tela:

1. escolher uma conta limpa no seletor — grafo pequeno, nada acontecendo;
2. trocar para a conta sob suspeita, profundidade 1;
3. subir para 2, depois 3 — a rede aparece inteira, os nós vermelhos se fecham;
4. subir para 4 e 5 — o traversal começa a vazar para a população limpa: é a hora
   de falar de fan-out e mostrar o toggle de poda;
5. Atlas Search com o nome grafado errado que o `$graphLookup` por igualdade nunca
   acharia;
6. Vector Search com um motivo escrito de outro jeito (opcional, cortar se apertar);
7. **Marcar N nós sob investigação** — transação ACID, com os números na tela;
8. **Injetar transação na rede** — o alerta aparece por change stream em tempo real;
9. fechar em arquitetura: um sistema a menos para operar, sincronizar e proteger.
