import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DataSet } from 'vis-data'
import { Network } from 'vis-network'
import { t } from './theme'

const EDGE_COLOR = {
  shares_device: '#0498ec',
  shares_address: '#ffc010',
  shares_pix_key: '#00ed64',
}

// Atenuação do que não é vizinho. Não é decoração: é o padrão de "revelar
// vizinhos" que ferramentas de investigação usam — o analista passa o mouse e a
// cadeia de conexões que importa fica sozinha na tela.
const DIM = 'rgba(136,147,151,0.16)'
const DIM_TEXT = 'rgba(136,147,151,0.35)'

function nodeStyle(n, { dimmed = false, emphasized = false } = {}) {
  const isRing = Boolean(n.ring_id)
  if (dimmed) {
    return {
      color: { background: 'rgba(28,45,56,0.45)', border: DIM },
      font: { color: DIM_TEXT, size: n.is_root ? 15 : 12 },
      borderWidth: 1,
    }
  }
  // O nó em foco mantém a cor do seu tipo e ganha borda azul: trocar o fundo por
  // azul apagaria a informação de "está numa rede", que é o que o analista está
  // lendo. O realce muda a moldura, não a identidade.
  const bg = n.is_root ? t.accent : isRing ? 'rgba(255,105,96,.55)' : t.bgCard
  return {
    color: {
      background: bg,
      border: emphasized ? t.link : n.is_root ? '#00c853' : isRing ? t.danger : t.borderSubtle,
      highlight: { background: bg, border: t.link },
      hover: { background: bg, border: t.link },
    },
    font: {
      // O rótulo fica FORA do nó, sobre o fundo escuro — a cor de contraste do
      // preenchimento verde deixaria o nome ilegível.
      color: n.is_root ? t.accent : isRing ? t.textPri : t.textMuted,
      size: n.is_root ? 15 : 12,
    },
    borderWidth: emphasized ? 3 : n.is_root ? 3 : isRing ? 2.5 : 1,
  }
}

export default function GraphCanvas({ data, onSelect, onHover }) {
  const holder = useRef(null)
  const net = useRef(null)
  const nodesDS = useRef(null)
  const edgesDS = useRef(null)
  const adjacency = useRef(new Map())
  const freezeTimer = useRef(null)
  const [settling, setSettling] = useState(false)

  // A aresta é bidirecional na coleção; desenhar as duas vias polui o canvas.
  const unique = useMemo(() => {
    const seen = new Set()
    return (data?.edges ?? []).filter((e) => {
      const key = [e.from, e.to].sort().join('|') + e.type
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [data])

  const restore = useCallback(() => {
    if (!nodesDS.current) return
    nodesDS.current.update((data?.nodes ?? []).map((n) => ({ id: n.id, ...nodeStyle(n) })))
    edgesDS.current.update(
      edgesDS.current.get().map((e) => ({
        id: e.id,
        color: { color: EDGE_COLOR[e.relType] ?? t.borderSubtle, opacity: 0.55 },
        width: e.relType === 'shares_device' ? 1.6 : 1,
      }))
    )
  }, [data])

  const focus = useCallback(
    (nodeId) => {
      if (!nodesDS.current) return
      const neighbours = adjacency.current.get(nodeId) ?? new Set()
      nodesDS.current.update(
        (data?.nodes ?? []).map((n) => {
          const near = n.id === nodeId || neighbours.has(n.id)
          return { id: n.id, ...nodeStyle(n, { dimmed: !near, emphasized: n.id === nodeId }) }
        })
      )
      edgesDS.current.update(
        edgesDS.current.get().map((e) => {
          const touches = e.from === nodeId || e.to === nodeId
          return {
            id: e.id,
            color: touches
              ? { color: EDGE_COLOR[e.relType] ?? t.borderSubtle, opacity: 1 }
              : { color: DIM, opacity: 0.25 },
            width: touches ? 2.4 : 1,
          }
        })
      )
    },
    [data]
  )

  useEffect(() => {
    if (!holder.current) return

    // Grau dentro do subgrafo: um nó que concentra vínculos precisa saltar aos
    // olhos antes de o analista ler qualquer rótulo. Raiz e nós de rede mantêm um
    // piso próprio para que o tipo continue legível mesmo com grau baixo.
    const deg = new Map()
    for (const e of unique) {
      deg.set(e.from, (deg.get(e.from) ?? 0) + 1)
      deg.set(e.to, (deg.get(e.to) ?? 0) + 1)
    }
    const maxDeg = Math.max(1, ...deg.values())

    const nodes = new DataSet(
      (data?.nodes ?? []).map((n) => {
        const base = n.is_root ? 20 : n.ring_id ? 12 : 8
        const boost = 14 * Math.sqrt((deg.get(n.id) ?? 0) / maxDeg)
        return {
          id: n.id,
          label: n.label,
          shape: 'dot',
          size: Math.round(base + boost),
          shadow: n.ring_id
            ? { enabled: true, color: 'rgba(255,105,96,.45)', size: 14, x: 0, y: 0 }
            : false,
          ...nodeStyle(n),
        }
      })
    )
    const edges = new DataSet(
      unique.map((e, i) => ({
        id: i,
        from: e.from,
        to: e.to,
        relType: e.type,
        color: { color: EDGE_COLOR[e.type] ?? t.borderSubtle, opacity: 0.55 },
        width: e.type === 'shares_device' ? 1.6 : 1,
      }))
    )

    const adj = new Map()
    for (const e of unique) {
      if (!adj.has(e.from)) adj.set(e.from, new Set())
      if (!adj.has(e.to)) adj.set(e.to, new Set())
      adj.get(e.from).add(e.to)
      adj.get(e.to).add(e.from)
    }
    adjacency.current = adj
    nodesDS.current = nodes
    edgesDS.current = edges

    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

    setSettling(!reduceMotion)
    const network = new Network(
      holder.current,
      { nodes, edges },
      {
        physics: {
          enabled: !reduceMotion,
          // forceAtlas2Based com `avoidOverlap` é o que faz dois nós arrastados
          // para perto se empurrarem em vez de ficarem um em cima do outro.
          solver: 'forceAtlas2Based',
          forceAtlas2Based: {
            gravitationalConstant: -70,
            centralGravity: 0.006,
            springLength: 130,
            springConstant: 0.08,
            damping: 0.45,
            avoidOverlap: 0.85,
          },
          stabilization: { iterations: 300, updateInterval: 25, fit: true },
          minVelocity: 0.6,
        },
        layout: { improvedLayout: (data?.nodes?.length ?? 0) < 400 },
        interaction: {
          hover: true,
          hoverConnectedEdges: false,
          tooltipDelay: 2 ** 31, // tooltip nativo desligado: o detalhe vive no inspetor
          navigationButtons: false,
          zoomView: true,
          dragView: true,
          hideEdgesOnDrag: true,
        },
        nodes: { shadow: false, borderWidthSelected: 3, chosen: false },
        edges: { smooth: { type: 'continuous', roundness: 0.35 }, shadow: false, selectionWidth: 0 },
      }
    )

    // Congela a física assim que estabiliza. O design system do workspace proíbe
    // animação ambiente contínua, e um grafo que nunca para de tremer cansa a
    // vista numa apresentação de 15 minutos.
    network.once('stabilizationIterationsDone', () => {
      network.setOptions({ physics: { enabled: false } })
      network.fit({ animation: { duration: 420, easingFunction: 'easeInOutQuad' } })
      setSettling(false)
    })
    if (reduceMotion) {
      network.fit()
      setSettling(false)
    }

    network.on('hoverNode', (p) => {
      focus(p.node)
      onHover?.(p.node)
    })
    network.on('blurNode', () => {
      restore()
      onHover?.(null)
    })
    network.on('selectNode', (p) => onSelect?.(p.nodes[0]))
    network.on('deselectNode', () => onSelect?.(null))
    // Arrastar precisa reacomodar a vizinhança. A física fica congelada em
    // repouso (nada de tremor ambiente), mas volta durante o arraste: com
    // `avoidOverlap`, os nós próximos abrem espaço em vez de se sobreporem.
    // Congela de novo quando o motor se estabiliza, com um teto de tempo para o
    // caso de um grafo denso que nunca chega ao repouso perfeito.
    const freeze = () => {
      clearTimeout(freezeTimer.current)
      network.setOptions({ physics: { enabled: false } })
      network.storePositions()
    }
    network.on('dragStart', () => {
      clearTimeout(freezeTimer.current)
      network.setOptions({ physics: { enabled: true } })
    })
    network.on('dragEnd', () => {
      clearTimeout(freezeTimer.current)
      freezeTimer.current = setTimeout(freeze, 2500)
      network.once('stabilized', freeze)
    })

    // O vis nem sempre emite `blurNode` quando o ponteiro sai do canvas de uma
    // vez — e o grafo ficava atenuado para sempre. O DOM sabe disso melhor.
    const leave = () => {
      restore()
      onHover?.(null)
    }
    holder.current.addEventListener('mouseleave', leave)
    const holderEl = holder.current

    net.current = network
    return () => {
      clearTimeout(freezeTimer.current)
      holderEl?.removeEventListener('mouseleave', leave)
      network.destroy()
      net.current = null
    }
  }, [data, unique, focus, restore, onHover, onSelect])

  const fit = () => net.current?.fit({ animation: { duration: 420, easingFunction: 'easeInOutQuad' } })
  const zoom = (factor) => {
    const n = net.current
    if (!n) return
    n.moveTo({ scale: n.getScale() * factor, animation: { duration: 220, easingFunction: 'easeInOutQuad' } })
  }

  return (
    <div className="canvas">
      <div className="canvas-surface" ref={holder} aria-label="Grafo da rede investigada" role="img" />

      {/* Depois de arrastar ou dar zoom, o apresentador precisa de um jeito de
          voltar ao enquadramento sem recarregar a tela. */}
      <div className="canvas-controls">
        <button type="button" onClick={() => zoom(1.25)} aria-label="Aproximar">+</button>
        <button type="button" onClick={() => zoom(0.8)} aria-label="Afastar">−</button>
        <button type="button" onClick={fit} aria-label="Enquadrar o grafo inteiro">⤢</button>
      </div>

      {settling && (
        <div className="canvas-settling" role="status">
          organizando o layout…
        </div>
      )}
    </div>
  )
}
