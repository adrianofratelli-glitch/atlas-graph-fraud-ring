import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DataSet } from 'vis-data'
import { Network } from 'vis-network'
import { t } from './theme'

// A cor da aresta diz de que tipo é a participação: pessoa física entrando no
// capital é uma leitura, holding controlando subsidiária é outra.
const EDGE_COLOR = {
  corporate: '#0498ec',
  individual: '#ffc010',
}

// Atenuação do que não é vizinho. Não é decoração: é o padrão de "revelar
// vizinhos" que ferramentas de investigação usam — o analista passa o mouse e a
// cadeia que importa fica sozinha na tela.
const DIM = 'rgba(136,147,151,0.16)'
const DIM_TEXT = 'rgba(136,147,151,0.35)'

function nodeStyle(n, { dimmed = false, emphasized = false, pinned = false } = {}) {
  // Empresa em revisão de crédito ganha borda tracejada: é o efeito da transação
  // ACID aparecendo no grafo. Sem isso, decidir não muda nada na tela.
  const dashes = n.credit_status === 'under_review' ? { borderDashes: [6, 4] } : { borderDashes: false }
  if (dimmed) {
    return {
      color: { background: 'rgba(28,45,56,0.45)', border: DIM },
      font: { color: DIM_TEXT, size: n.is_subject ? 15 : 12 },
      borderWidth: 1,
      shapeProperties: dashes,
    }
  }
  // Três papéis, três leituras: quem pediu o crédito, quem controla o grupo, e
  // quem só participa. Vencido pinta de vermelho — é o que o olho procura.
  const inadimplente = (n.vencido ?? 0) > 0
  const bg = n.is_subject
    ? t.accent
    : n.kind === 'person'
      ? 'rgba(255,192,16,.30)'
      : inadimplente
        ? 'rgba(255,105,96,.55)'
        : n.is_holding
          ? 'rgba(4,152,236,.35)'
          : t.bgCard
  const borda = pinned
    ? '#ffffff'
    : emphasized
      ? t.link
      : n.is_subject
        ? '#00c853'
        : inadimplente
          ? t.danger
          : n.is_holding
            ? t.link
            : t.borderSubtle
  return {
    color: {
      background: bg,
      border: borda,
      highlight: { background: bg, border: pinned ? '#ffffff' : t.link },
      hover: { background: bg, border: pinned ? '#ffffff' : t.link },
    },
    font: {
      // Empresa é `box`, e o vis desenha o rótulo DENTRO da caixa — então a cor
      // do texto precisa contrastar com o preenchimento, não com o fundo da
      // tela. O solicitante ficava com texto verde sobre caixa verde: o nó mais
      // importante da tela era o único ilegível.
      color: n.kind === 'person'
        ? t.textMuted
        : n.is_subject
          ? '#00171f'
          : inadimplente || n.is_holding
            ? t.textPri
            : t.textSec,
      size: n.is_subject ? 14 : 11.5,
      multi: false,
    },
    borderWidth: pinned ? 4 : emphasized ? 3 : n.is_subject ? 3 : inadimplente ? 2.5 : 1,
    shapeProperties: dashes,
  }
}

export default function GraphCanvas({ data, pinned, onSelect, onHover, foraDoEscopo, destaque }) {
  const holder = useRef(null)
  const net = useRef(null)
  const nodesDS = useRef(null)
  const edgesDS = useRef(null)
  const adjacency = useRef(new Map())
  // Handle do requestAnimationFrame da reacomodação pós-arraste.
  const animacao = useRef(0)
  // `pinned` vive num ref, não nas dependências do efeito: mudá-lo só repinta os
  // nós. Colocá-lo na dependência recriaria a Network a cada clique, jogando fora
  // o layout que a física acabou de estabilizar.
  const pinnedRef = useRef(pinned)
  // Mesmo motivo do `pinnedRef`: mudar o escopo repinta os nós, não recria a
  // Network — recriar jogaria fora o layout que acabou de ser desencostado.
  const escopoRef = useRef(foraDoEscopo)
  // Conjunto destacado por uma aba lateral (um resultado de busca, uma atividade,
  // a carteira de um assessor, um alerta). Vive num ref pelo mesmo motivo dos
  // outros: repinta, não recria a rede.
  const destaqueRef = useRef(destaque)
  const [settling, setSettling] = useState(false)
  const restoreRef = useRef(null)

  const unique = useMemo(() => {
    const seen = new Set()
    return (data?.edges ?? []).filter((e) => {
      const key = `${e.from}|${e.to}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }, [data])

  useEffect(() => {
    pinnedRef.current = pinned
    restoreRef.current?.()
  }, [pinned])

  useEffect(() => {
    escopoRef.current = foraDoEscopo
    restoreRef.current?.()
  }, [foraDoEscopo])

  useEffect(() => {
    destaqueRef.current = destaque
    restoreRef.current?.()
    // Enquadra o que foi destacado. Um destaque fora da área visível não destaca
    // nada — e num grupo de 25 empresas o nó procurado costuma estar justamente
    // na borda.
    const rede = net.current
    if (!rede || !destaque?.size) return
    const ids = [...destaque]
    // Enquadrar **um** nó levaria o zoom ao máximo e o contexto sumiria: o
    // apresentador ganharia uma caixa gigante no meio da tela sem saber onde ela
    // fica na cadeia. Centraliza no conjunto apontado e limita a escala.
    const pos = rede.getPositions(ids)
    const xs = ids.map((i) => pos[i].x)
    const ys = ids.map((i) => pos[i].y)
    const centro = {
      x: (Math.min(...xs) + Math.max(...xs)) / 2,
      y: (Math.min(...ys) + Math.max(...ys)) / 2,
    }
    const escalaAtual = rede.getScale()
    rede.moveTo({
      position: centro,
      scale: Math.min(Math.max(escalaAtual, 0.55), 0.9),
      animation: { duration: 500, easingFunction: 'easeInOutQuad' },
    })
  }, [destaque])

  const restore = useCallback(() => {
    if (!nodesDS.current) return
    nodesDS.current.update(
      (data?.nodes ?? []).map((n) => ({
        id: n.id,
        ...nodeStyle(n, {
          pinned: n.id === pinnedRef.current,
          // Destaque vindo de uma aba: o que **não** está no conjunto apaga, para
          // o olho ir direto ao que a lista lateral está falando. Sem isto, clicar
          // num resultado obriga o apresentador a procurar o nó no grafo com o
          // dedo na tela.
          emphasized: destaqueRef.current?.has(n.id) ?? false,
          dimmed:
            (escopoRef.current?.has(n.id) ?? false) ||
            (destaqueRef.current ? !destaqueRef.current.has(n.id) : false),
        }),
      }))
    )
    edgesDS.current.update(
      edgesDS.current.get().map((e) => ({
        id: e.id,
        color: { color: EDGE_COLOR[e.relType] ?? t.borderSubtle, opacity: 0.75 },
        width: e.relType === 'corporate' ? 3 : 2.4,
      }))
    )
  }, [data])

  restoreRef.current = restore

  const focus = useCallback(
    (nodeId) => {
      if (!nodesDS.current) return
      const neighbours = adjacency.current.get(nodeId) ?? new Set()
      nodesDS.current.update(
        (data?.nodes ?? []).map((n) => {
          const near = n.id === nodeId || neighbours.has(n.id)
          return {
            id: n.id,
            ...nodeStyle(n, {
              dimmed: !near,
              emphasized: n.id === nodeId,
              pinned: n.id === pinnedRef.current,
            }),
          }
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
            width: touches ? 4.5 : 1.6,
          }
        })
      )
    },
    [data]
  )

  useEffect(() => {
    if (!holder.current) return

    // Rótulo curto. "Farias Souza Empreendimentos EIRELI" ocupa quase o dobro da
    // largura de uma caixa e, com doze empresas no mesmo nível, os nomes se
    // sobrepõem e nenhum fica legível. O nome inteiro continua no painel de
    // detalhe do nó, que é onde ele é lido de verdade.
    const curto = (texto = '') => {
      const limpo = String(texto).replace(/\s+(S\.A\.|Ltda|EIRELI)$/i, '')
      return limpo.length > 22 ? `${limpo.slice(0, 21)}…` : limpo
    }

    // Raio aproximado de cada nó, em unidades do canvas. Serve para a
    // reacomodação depois do arraste saber o que é sobreposição. A caixa da
    // empresa é retangular; usar o semi-eixo maior é conservador e evita que dois
    // rótulos longos encostem.
    const raio = new Map()
    // Meia-largura, não largura. A caixa da empresa tem `widthConstraint` de no
    // máximo 120 px mais margem, então o raio horizontal é ~68 no pior caso —
    // usar a largura inteira dobrava o espaçamento exigido, alargava cada nível e
    // o `fit` respondia reduzindo o zoom até os rótulos ficarem ilegíveis.
    const raioDe = (n) => (n.kind === 'person' ? 18 : Math.min(68, 26 + curto(n.label).length * 1.7))

    const nodes = new DataSet(
      (data?.nodes ?? []).map((n) => ({
        id: n.id,
        label: curto(n.label),
        // Empresa é caixa, sócio pessoa física é círculo. A forma distingue os
        // dois tipos de nó antes de qualquer rótulo ser lido, e nenhum estado
        // aqui é comunicado só por cor.
        shape: n.kind === 'person' ? 'dot' : 'box',
        // Tamanho pelo limite de crédito: numa decisão de crédito, o que importa
        // é onde o dinheiro está, não quantas arestas o nó tem.
        size: n.kind === 'person' ? 10 : undefined,
        widthConstraint: n.kind === 'company' ? { minimum: 60, maximum: 120 } : undefined,
        shadow: (n.vencido ?? 0) > 0
          ? { enabled: true, color: 'rgba(255,105,96,.45)', size: 16, x: 0, y: 0 }
          : false,
        ...nodeStyle(n, {
          pinned: n.id === pinnedRef.current,
          emphasized: destaqueRef.current?.has(n.id) ?? false,
          dimmed:
            (escopoRef.current?.has(n.id) ?? false) ||
            (destaqueRef.current ? !destaqueRef.current.has(n.id) : false),
        }),
      }))
    )
    for (const n of data?.nodes ?? []) raio.set(n.id, raioDe(n))

    const edges = new DataSet(
      unique.map((e, i) => ({
        id: i,
        from: e.from,
        to: e.to,
        relType: e.type,
        // A seta importa: participação é dirigida, e é a direção que diz quem
        // controla quem.
        arrows: { to: { enabled: true, scaleFactor: 0.55 } },
        label: e.percentage != null ? `${e.percentage}%` : undefined,
        font: { size: 10, color: t.textMuted, strokeWidth: 0, align: 'middle' },
        color: { color: EDGE_COLOR[e.type] ?? t.borderSubtle, opacity: 0.75 },
        width: e.type === 'corporate' ? 3 : 2.4,
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
        // Hierárquico, e é a escolha certa aqui: cadeia societária tem topo e
        // base. Um layout de força esconderia justamente a informação que o
        // analista procura — quem está acima de quem.
        layout: {
          hierarchical: {
            enabled: true,
            direction: 'UD',
            sortMethod: 'directed',
            // Espaçamento estreito e níveis altos de propósito. O `fit` escala
            // pela **largura** da caixa: com 25 empresas e uma dúzia de sócios
            // pessoa física no topo, um `nodeSpacing` largo produzia uma faixa de
            // 900 px de largura por 200 de altura — o grafo aparecia espremido no
            // meio de um canvas vazio, que é o oposto do que a tela precisa
            // mostrar.
            levelSeparation: 190,
            nodeSpacing: 95,
            treeSpacing: 120,
            shakeTowards: 'roots',
          },
        },
        // Física **sempre** desligada.
        //
        // Ela existia para reacomodar vizinhos durante o arraste, e o preço era
        // o grafo inteiro entrar em movimento a cada nó puxado. A reacomodação
        // agora é local e determinística (`desencostar`, mais abaixo): empurra
        // só quem ficou sobreposto, e ninguém mais sai do lugar.
        //
        // Um grafo que treme sozinho também contraria o design system do
        // workspace, que proíbe animação ambiente contínua.
        physics: { enabled: false },
        interaction: {
          hover: true,
          hoverConnectedEdges: false,
          tooltipDelay: 2 ** 31,
          navigationButtons: false,
          zoomView: true,
          dragView: true,
          hideEdgesOnDrag: true,
        },
        nodes: { shadow: false, borderWidthSelected: 3, chosen: false, margin: 8 },
        edges: { smooth: { type: 'cubicBezier', forceDirection: 'vertical', roundness: 0.5 }, shadow: false, selectionWidth: 0 },
      }
    )

    network.once('afterDrawing', () => {
      network.fit({ animation: { duration: 420, easingFunction: 'easeInOutQuad' } })
      setSettling(false)
      // Segundo `fit` depois que a animação termina: o primeiro roda com a caixa
      // delimitadora ainda do layout anterior e deixa o grafo pequeno no centro
      // de um canvas vazio.
      window.setTimeout(() => {
        const sujeito = (data?.nodes ?? []).find((n) => n.is_subject)?.id
        desencostarNivel(sujeito)
        network.fit({ animation: false })
      }, 500)
    })
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

    // ## Arrastar move **só** o que foi arrastado
    //
    // A versão anterior ligava a física no `dragStart` e desligava quando o motor
    // estabilizava. Funcionava no papel e era ruim na mão: mexer num nó punha o
    // grafo inteiro em movimento, e o analista perdia a referência espacial que
    // tinha acabado de construir. Numa demo isso parece instabilidade.
    //
    // Aqui a física fica desligada o tempo todo. O nó arrastado fica exatamente
    // onde foi solto, e depois um passo local empurra **apenas** quem ficou
    // sobreposto, pelo mínimo necessário, em cascata curta. Quem não colidiu não
    // se move um pixel.
    const PADDING = 10        // folga entre bordas, para os rótulos respirarem
    const ITERACOES = 8
    const CASCATA_MAX = 2     // até onde um empurrão propaga
    // Teto de deslocamento por nó. Sem ele, uma cascata longa reposiciona meia
    // árvore e o resultado parece o layout se desfazendo — que é exatamente o
    // problema que esta função existe para resolver.
    const DESLOCAMENTO_MAX = 90

    const desencostar = (ancora) => {
      const pos = network.getPositions()
      const ids = Object.keys(pos)
      const alvo = new Map(ids.map((id) => [id, { ...pos[id] }]))
      // `fixos` nunca se movem: o nó que o usuário soltou é a âncora, e mover a
      // âncora desfaria justamente a ação dele.
      const fixos = new Set([ancora])
      const frente = new Map([[ancora, 0]])

      for (let it = 0; it < ITERACOES; it++) {
        let colidiu = false
        for (const [a, nivel] of [...frente]) {
          if (nivel >= CASCATA_MAX) continue
          const pa = alvo.get(a)
          const ra = raio.get(a) ?? 40
          for (const b of ids) {
            if (b === a || fixos.has(b)) continue
            const pb = alvo.get(b)
            const rb = raio.get(b) ?? 40
            let dx = pb.x - pa.x
            let dy = pb.y - pa.y
            let d = Math.hypot(dx, dy)
            const minimo = ra + rb + PADDING
            if (d >= minimo) continue
            // Dois nós exatamente sobrepostos não têm direção: escolhe uma
            // determinística em vez de dividir por zero.
            if (d < 0.001) {
              dx = 1
              dy = 0
              d = 1
            }
            const empurrao = (minimo - d) / d
            const nx = pb.x + dx * empurrao
            const ny = pb.y + dy * empurrao
            // Respeita o teto: um nó que já andou o bastante para de ser
            // empurrado, mesmo que ainda encoste. Encostar é menos ruim do que
            // o grafo inteiro se rearranjar.
            const p0 = pos[b]
            if (Math.hypot(nx - p0.x, ny - p0.y) > DESLOCAMENTO_MAX) continue
            pb.x = nx
            pb.y = ny
            colidiu = true
            if (!frente.has(b)) frente.set(b, nivel + 1)
          }
        }
        if (!colidiu) break
      }

      // Só quem realmente saiu do lugar entra na animação.
      const mexidos = ids.filter((id) => {
        const p0 = pos[id]
        const p1 = alvo.get(id)
        return Math.hypot(p1.x - p0.x, p1.y - p0.y) > 0.5
      })
      if (!mexidos.length) return

      const QUADROS = 14
      let q = 0
      const passo = () => {
        q += 1
        // easeOutCubic: sai rápido e chega devagar, que é como o olho espera que
        // uma peça reposicionada se acomode.
        const t = 1 - Math.pow(1 - q / QUADROS, 3)
        for (const id of mexidos) {
          const p0 = pos[id]
          const p1 = alvo.get(id)
          network.moveNode(id, p0.x + (p1.x - p0.x) * t, p0.y + (p1.y - p0.y) * t)
        }
        if (q < QUADROS) animacao.current = requestAnimationFrame(passo)
        else network.storePositions()
      }
      cancelAnimationFrame(animacao.current)
      animacao.current = requestAnimationFrame(passo)
    }

    /**
     * Desencosta o layout inicial, **só na horizontal**.
     *
     * O layout hierárquico distribui os nós por nível e não sabe o tamanho do
     * rótulo: com doze empresas num nível e razões sociais longas, as caixas
     * encostam e os nomes viram uma mancha. Empurrar na vertical resolveria a
     * sobreposição e destruiria a informação — a altura **é** o nível societário,
     * e é ela que diz quem controla quem.
     *
     * Então o passo global move apenas no eixo X, dentro de cada nível, e mantém
     * o sujeito da consulta parado para a tela não "pular" depois de carregar.
     */
    const desencostarNivel = (fixo) => {
      const pos = network.getPositions()
      const ids = Object.keys(pos)
      const alvo = new Map(ids.map((id) => [id, { ...pos[id] }]))

      // Nós na mesma faixa de altura pertencem ao mesmo nível. A tolerância
      // existe porque o vis já aplica pequenos ajustes verticais.
      const niveis = new Map()
      for (const id of ids) {
        const chave = Math.round(alvo.get(id).y / 40)
        if (!niveis.has(chave)) niveis.set(chave, [])
        niveis.get(chave).push(id)
      }

      for (const grupo of niveis.values()) {
        if (grupo.length < 2) continue
        grupo.sort((a, b) => alvo.get(a).x - alvo.get(b).x)
        for (let it = 0; it < 30; it++) {
          let colidiu = false
          for (let i = 0; i < grupo.length - 1; i++) {
            const a = grupo[i]
            const b = grupo[i + 1]
            const pa = alvo.get(a)
            const pb = alvo.get(b)
            const minimo = (raio.get(a) ?? 40) + (raio.get(b) ?? 40) + PADDING
            const d = pb.x - pa.x
            if (d >= minimo) continue
            const falta = (minimo - d) / 2
            // Divide o empurrão entre os dois vizinhos, para o nível crescer
            // simetricamente em vez de escorrer todo para a direita.
            if (a !== fixo) pa.x -= falta
            if (b !== fixo) pb.x += falta
            if (a === fixo) pb.x += falta
            if (b === fixo) pa.x -= falta
            colidiu = true
          }
          if (!colidiu) break
        }
      }

      for (const id of ids) {
        const p = alvo.get(id)
        network.moveNode(id, p.x, p.y)
      }
      network.storePositions()
    }

    network.on('dragEnd', (p) => {
      const solto = p.nodes?.[0]
      if (solto) desencostar(solto)
    })

    // O vis nem sempre emite `blurNode` quando o ponteiro sai do canvas de uma
    // vez — e o grafo ficava atenuado para sempre. O DOM sabe disso melhor.
    const leave = () => {
      restore()
      onHover?.(null)
    }
    holder.current.addEventListener('mouseleave', leave)
    const holderEl = holder.current

    // Gancho de teste. A rede vive dentro do componente e o canvas não expõe
    // nós no DOM, então sem isto não há como um teste automatizado afirmar
    // "só o nó arrastado saiu do lugar" — que é justamente a garantia desta
    // tela. Só leitura de posição; não muda o comportamento da aplicação.
    window.__grafo = {
      posicoes: () => network.getPositions(),
      paraTela: (p) => network.canvasToDOM(p),
      arrastar: (id, dx, dy) => {
        const p = network.getPositions([id])[id]
        network.moveNode(id, p.x + dx, p.y + dy)
        desencostar(id)
      },
    }

    net.current = network
    return () => {
      cancelAnimationFrame(animacao.current)
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
      <div className="canvas-surface" ref={holder} aria-label="Grafo da cadeia societária" role="img" />
      <div className="canvas-controls">
        <button type="button" onClick={() => zoom(1.25)} aria-label="Aproximar">+</button>
        <button type="button" onClick={() => zoom(0.8)} aria-label="Afastar">−</button>
        <button type="button" onClick={fit} aria-label="Enquadrar o grafo inteiro">⤢</button>
      </div>
      {settling && <div className="canvas-settling" role="status">organizando a cadeia…</div>}
    </div>
  )
}
