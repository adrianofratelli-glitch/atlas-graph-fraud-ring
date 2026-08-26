# Interface

React com Vite, e `vis-network` para desenhar o grafo.

```bash
npm install
npm run dev      # porta 5350, com proxy para a API na 8350
npm run build
npm run preview  # porta 5350, servindo o bundle pronto — é o que ./start.sh usa
```

| Arquivo | O que faz |
|---|---|
| `src/App.jsx` | a tela inteira: controles, métricas, painéis de busca e alertas |
| `src/GraphCanvas.jsx` | desenha o resultado do traversal e cuida das interações |
| `src/api.js` | cliente HTTP; normaliza a indisponibilidade de um recurso |
| `src/theme.js` | as cores, espelhando o stylesheet |

O visual usa fundo escuro, verde para ação e sucesso, azul para informação,
amarelo para alerta e vermelho para risco. Nenhum estado é comunicado só por cor:
sempre há um ícone, um rótulo ou uma diferença de forma junto.
