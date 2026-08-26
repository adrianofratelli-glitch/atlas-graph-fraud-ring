# ADR 0001 — Topologia do dado sintético

**Status:** aceito · **Data:** 2026-08-26

## Contexto

A demo depende de o grafo crescer visivelmente entre a profundidade 1 e a 4. Isso
não é uma propriedade do `$graphLookup`: é uma propriedade do dado. Três
topologias foram testadas e medidas antes de o backend existir.

## Alternativas medidas

| Topologia | Resultado medido | Por que foi descartada |
|---|---|---|
| Endereço único para toda a rede | rede de 20 completa na profundidade 1 (422 arestas, 20 nós) | vira um clique; não sobra nada para o passo 3 do roteiro mostrar |
| Cadeia (cada membro ligado ao seguinte) | precisaria de profundidade 19 para revelar uma rede de 20 | acima do cap do backend, e o crescimento é linear demais para ser legível |
| Atributo compartilhado sorteado na população limpa | grau médio 8.6; profundidade 2 alcançava 1096 de 2000 nós | a rede de fraude se perde no ruído; o grafo fica ilegível |

## Decisão

Árvore de fator de ramificação **2**, com os atributos compartilhados seguindo a
mesma árvore e nunca o anel inteiro:

- **device:** cada par pai/filho divide um dispositivo — é a aresta que cria a árvore;
- **endereço:** dividido dentro de um ramo (pai + filhos diretos);
- **chave PIX:** só entre os coletores (líder e filhos diretos).

Na população limpa, o compartilhamento é **estruturado, não sorteado**: domicílios
de 2 a 3 pessoas (18% da população) e 4% das transações operadas do dispositivo de
uma conta vizinha. Pessoas aleatórias dividindo endereço criariam um componente
conexo artificial.

## Consequência medida

Rede de 30 membros, entrando pelo líder, no dataset completo (150 mil pessoas):

| Profundidade | Nós | Membros da rede alcançados |
|---|---|---|
| 1 | 7 | 7/30 |
| 2 | 15 | 15/30 |
| 3 | 30 | **30/30** |
| 4 | 30 | 30/30 |
| 5 | 30 | 30/30 |
| 6 | 30 | 30/30 |

A revelação é gradual e legível — que é o que o passo 3 do roteiro precisa. A
partir da profundidade 3 o traversal satura: a rede é um componente fechado, e não
há vazamento para a população limpa.

**Isso é uma consequência, não um objetivo, e mudou o roteiro.** Uma versão
anterior deste ADR previa que a profundidade 4 começaria a alcançar gente
legítima, o que se confirmou no dataset reduzido de teste (onde os hubs ficam
abaixo do limiar de poda) mas **não** no dataset completo, onde eles são
descartados na materialização. A degradação por fan-out existe e é mensurável —
só não é aqui. Ela está medida em `LIMITATIONS.md §4`, a partir de um hub, e o
roteiro foi corrigido para mostrá-la lá.

Documentar a previsão que não se confirmou é deliberado: é o tipo de detalhe que
um arquiteto do lado do cliente vai testar.
