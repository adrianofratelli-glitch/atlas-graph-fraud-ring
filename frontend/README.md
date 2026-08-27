# Interface

React with Vite, and `vis-network` to draw the graph.

```bash
npm install
npm run dev      # port 5350, proxying to the API on 8350
npm run build
npm run preview  # port 5350, serving the built bundle — this is what ./start.sh uses
```

| File | What it does |
|---|---|
| `src/App.jsx` | the whole screen: controls, metrics, search and alert panels |
| `src/GraphCanvas.jsx` | draws the traversal result and handles the interactions |
| `src/api.js` | HTTP client; normalises the unavailability of a feature |
| `src/theme.js` | the colours, mirroring the stylesheet |

The design uses a dark background, green for action and success, blue for
information, amber for warning and red for risk. No state is communicated by
colour alone: there is always an icon, a label or a difference in shape alongside.

The page never scrolls. Only the control rail and the inspector body scroll
internally, and only when the presenter expands something on purpose.
