import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import GraphCanvas from './GraphCanvas'
import { api } from './api'

const DEPTHS = [1, 2, 3, 4, 5, 6]
const EDGE_TYPES = [
  ['shares_device', 'Dispositivo', '#0498ec'],
  ['shares_address', 'Endereço', '#ffc010'],
  ['shares_pix_key', 'Chave PIX', '#00ed64'],
]
const TABS = [
  ['no', 'Nó'],
  ['search', 'Busca'],
  ['vector', 'Semântica'],
  ['alerts', 'Alertas'],
]

const fmt = (n) => (n == null ? '—' : n.toLocaleString('pt-BR'))

export default function App() {
  const [health, setHealth] = useState(null)
  const [entry, setEntry] = useState(null)
  const [subject, setSubject] = useState('')
  const [depth, setDepth] = useState(1)
  const [types, setTypes] = useState(EDGE_TYPES.map(([k]) => k))
  const [prune, setPrune] = useState(true)
  const [net, setNet] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [caseInfo, setCaseInfo] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [search, setSearch] = useState({ q: '', out: null, degraded: null })
  const [vector, setVector] = useState({ text: '', out: null, degraded: null })
  const [tab, setTab] = useState('no')
  const [picked, setPicked] = useState(null)
  const [hovered, setHovered] = useState(null)

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: 'offline' }))
    api.entryPoints().then(setEntry).catch(() => {})
  }, [])

  useEffect(() => {
    if (entry?.suspects?.length && !subject) setSubject(entry.suspects[0].person_id)
  }, [entry, subject])

  // Campos de busca já preenchidos com o caso do dataset: no palco, o apresentador
  // só clica em Buscar. Digitar um nome com erro de digitação ao vivo é a forma
  // mais fácil de errar a letra e a busca não achar nada.
  useEffect(() => {
    const caso = entry?.entity_resolution_case
    if (caso) setSearch((p) => (p.q ? p : { ...p, q: caso.typo_name }))
    // Paráfrase que NÃO existe no corpus: se a frase de consulta estivesse lá
    // literalmente, o primeiro resultado seria ela mesma e a demo não provaria nada.
    setVector((p) => (p.text ? p : { ...p, text: 'paguei o valor mensal da casa em que moro de aluguel' }))
  }, [entry])

  useEffect(() => {
    const es = new EventSource('/api/alerts/stream')
    es.onmessage = (ev) => {
      setAlerts((prev) => [JSON.parse(ev.data), ...prev].slice(0, 20))
      setTab('alerts')
    }
    return () => es.close()
  }, [])

  const expand = useCallback(
    async (d, s) => {
      if (!s) return
      setBusy(true)
      setError(null)
      try {
        setNet(await api.network(s, { depth: d, edgeTypes: types, pruneHubs: prune }))
      } catch (e) {
        setError(e.message)
        setNet(null)
      } finally {
        setBusy(false)
      }
    },
    [types, prune]
  )

  useEffect(() => {
    if (subject) expand(depth, subject)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subject, depth, types, prune])

  const ringNodes = useMemo(() => (net?.nodes ?? []).filter((n) => n.ring_id).map((n) => n.id), [net])
  const byId = useMemo(() => new Map((net?.nodes ?? []).map((n) => [n.id, n])), [net])
  const neighbours = useMemo(() => {
    const m = new Map()
    const seen = new Set()
    for (const e of net?.edges ?? []) {
      const key = [e.from, e.to].sort().join('|') + e.type
      if (seen.has(key)) continue
      seen.add(key)
      if (!m.has(e.from)) m.set(e.from, new Set())
      if (!m.has(e.to)) m.set(e.to, new Set())
      m.get(e.from).add(e.to)
      m.get(e.to).add(e.from)
    }
    return m
  }, [net])

  const detail = byId.get(hovered ?? picked)

  const doFlag = async () => {
    if (!ringNodes.length) return
    setBusy(true)
    try {
      setCaseInfo(await api.flag(ringNodes, `rede alcançada em ${depth} saltos a partir de ${subject}`))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const doSearch = async (e) => {
    e.preventDefault()
    try {
      const out = await api.searchPeople(search.q)
      setSearch((p) => ({ ...p, out, degraded: null }))
    } catch (err) {
      setSearch((p) => ({ ...p, out: null, degraded: err.degraded ?? { status: err.message } }))
    }
  }

  const doVector = async (e) => {
    e.preventDefault()
    try {
      const out = await api.similarReasons(vector.text)
      setVector((p) => ({ ...p, out, degraded: null }))
    } catch (err) {
      setVector((p) => ({ ...p, out: null, degraded: err.degraded ?? { status: err.message } }))
    }
  }

  const idx = health?.checks?.search_indexes ?? {}
  const searchReady = Object.values(idx)[0] === 'READY'
  const vectorReady = Object.values(idx)[1] === 'READY'
  const stats = net?.stats

  return (
    <div className="app" data-pov-shell>
      <a className="pov-skip-link" href="#conteudo-principal">Pular para o conteúdo</a>

      <header className="topbar">
        <span className="brand">Grafo de fraude</span>
        <span className="thesis">
          Uma transação isolada parece legítima. <em>A rede em volta dela, não.</em>
        </span>
        <span className="spacer" />
        <span className="hint">$graphLookup · Atlas Search · Vector Search · Change Streams</span>
        <HealthBadge health={health} />
      </header>

      <main className="body" id="conteudo-principal">
        <aside className="rail" aria-label="Controles">
          <section className="block">
            <h2>Ponto de entrada</h2>
            <select value={subject} onChange={(e) => setSubject(e.target.value)} aria-label="Pessoa investigada">
              <optgroup label="Contas sob suspeita">
                {(entry?.suspects ?? []).map((s) => (
                  <option key={s.person_id} value={s.person_id}>
                    {s.person_name} · {s.ring_id}
                  </option>
                ))}
              </optgroup>
              <optgroup label="População limpa (controle)">
                {(entry?.clean ?? []).map((c) => (
                  <option key={c._id} value={c._id}>{c.name}</option>
                ))}
              </optgroup>
            </select>
          </section>

          <section className="block">
            <h2>Profundidade</h2>
            <div className="depth-row" role="group" aria-label="Profundidade do traversal">
              {DEPTHS.map((d) => (
                <button key={d} className="depth-btn" aria-pressed={depth === d} onClick={() => setDepth(d)}>
                  {d}
                </button>
              ))}
            </div>
          </section>

          <section className="block">
            <h2>Arestas</h2>
            {EDGE_TYPES.map(([key, label, color]) => (
              <label className="check" key={key}>
                <input
                  type="checkbox"
                  checked={types.includes(key)}
                  onChange={(e) =>
                    setTypes((prev) => (e.target.checked ? [...prev, key] : prev.filter((x) => x !== key)))
                  }
                />
                <span className="dot" style={{ background: color }} />
                {label}
              </label>
            ))}
            <label className="check">
              <input type="checkbox" checked={prune} onChange={(e) => setPrune(e.target.checked)} />
              <span className="dot" style={{ background: 'transparent', border: '1px solid var(--text-muted)' }} />
              Podar hubs
            </label>
          </section>

          <section className="block block-action">
            <button className="btn btn-primary btn-block" onClick={doFlag} disabled={busy || !ringNodes.length}>
              Marcar {ringNodes.length || 0} nós sob investigação
            </button>
            <button
              className="btn btn-ghost btn-block"
              disabled={!caseInfo}
              onClick={() => api.simulate().catch((e) => setError(e.message))}
            >
              Injetar transação na rede
            </button>
            {caseInfo && (
              <p className="case-line">
                <code>{caseInfo.case_id}</code> · {fmt(caseInfo.accounts_flagged)} contas ·{' '}
                {caseInfo.elapsed_ms} ms · ACID
              </p>
            )}
          </section>
        </aside>

        <section className="stage">
          <div className="metrics" role="group" aria-label="Métricas da expansão">
            <Metric k="Nós" v={fmt(stats?.nodes)} />
            <Metric k="Arestas" v={fmt(stats?.edges_unique)} />
            <Metric k="Em rede" v={fmt(stats?.ring_nodes)} tone={stats?.ring_nodes ? 'bad' : undefined} />
            <Metric k="Tempo" v={stats ? `${stats.elapsed_ms} ms` : '—'} tone={metricTone(stats?.elapsed_ms)} />
            <Metric k="Salto" v={net?.depth ?? '—'} />
            <span className="spacer" />
            <span className="legend">
              <span><i className="dot" style={{ background: '#00ed64' }} /> entrada</span>
              <span><i className="dot" style={{ background: '#ff6960' }} /> rede</span>
              <span><i className="dot dot-plain" /> limpa</span>
            </span>
          </div>

          <div className="canvas-wrap">
            {net?.nodes?.length ? (
              <GraphCanvas data={net} onSelect={setPicked} onHover={setHovered} />
            ) : (
              <div className="canvas empty">
                {busy ? 'expandindo…' : error ? error : 'Nenhum vínculo neste ponto de entrada.'}
              </div>
            )}
            {stats?.truncated && (
              <p className="canvas-note">
                truncado em {fmt(stats.max_nodes)} nós de {fmt(stats.edges_before_truncation)} arestas — fan-out alto,
                comportamento descrito em <code>LIMITATIONS.md §4</code>
              </p>
            )}
          </div>
        </section>

        <aside className="inspector" aria-label="Detalhe e evidências">
          <div className="tabs" role="tablist">
            {TABS.map(([key, label]) => (
              <button
                key={key}
                role="tab"
                className="tab"
                aria-selected={tab === key}
                onClick={() => setTab(key)}
              >
                {label}
                {key === 'alerts' && alerts.length > 0 && <span className="tab-count">{alerts.length}</span>}
              </button>
            ))}
          </div>

          <div className="tab-body">
            {tab === 'no' && (
              <NodePanel
                node={detail}
                neighbours={neighbours}
                byId={byId}
                hovering={Boolean(hovered)}
                depth={depth}
                onPick={setPicked}
              />
            )}

            {tab === 'search' && (
              <>
                <StatusLine ready={searchReady} label="Atlas Search" status={Object.values(idx)[0]} />
                {entry?.entity_resolution_case && (
                  <p className="muted small">
                    O dataset contém <code>{entry.entity_resolution_case.typo_name}</code>, grafado quase igual a{' '}
                    <code>{entry.entity_resolution_case.real_name}</code>. Nenhuma aresta liga os dois — só o{' '}
                    <code>fuzzy</code> alcança.
                  </p>
                )}
                <form onSubmit={doSearch} className="inline-form">
                  <input
                    type="text"
                    value={search.q}
                    onChange={(e) => setSearch((p) => ({ ...p, q: e.target.value }))}
                    aria-label="Nome a resolver"
                  />
                  <button className="btn btn-ghost" disabled={!searchReady || !search.q.trim()}>Buscar</button>
                </form>
                {search.degraded && <Degraded d={search.degraded} feature="Atlas Search" />}
                {search.out && (
                  <div className="rows">
                    {search.out.results.map((r) => (
                      <div className={`row ${r.ring_id ? 'hit' : ''}`} key={r._id}>
                        <span className="grow">
                          {r.name}
                          <em>{r.ring_id ? `rede ${r.ring_id}` : 'sem rede'} · {r.city}</em>
                        </span>
                        <span className="score">{r.score.toFixed(2)}</span>
                      </div>
                    ))}
                    <p className="muted small">{search.out.elapsed_ms} ms · {search.out.index}</p>
                  </div>
                )}
              </>
            )}

            {tab === 'vector' && (
              <>
                <StatusLine ready={vectorReady} label="Vector Search" status={Object.values(idx)[1]} />
                <form onSubmit={doVector} className="inline-form">
                  <input
                    type="text"
                    value={vector.text}
                    onChange={(e) => setVector((p) => ({ ...p, text: e.target.value }))}
                    aria-label="Texto de motivo"
                  />
                  <button className="btn btn-ghost" disabled={!vectorReady || !vector.text.trim()}>Buscar</button>
                </form>
                {vector.degraded && <Degraded d={vector.degraded} feature="Vector Search" />}
                {vector.out && (
                  <div className="rows">
                    {vector.out.results.map((r) => (
                      <div className={`row ${r.ring_id ? 'hit' : ''}`} key={r.reason_text}>
                        <span className="grow">
                          {r.reason_text}
                          <em>{fmt(r.ocorrencias_no_bloco)} ocorrências · ticket R$ {fmt(Math.round(r.amount))}</em>
                        </span>
                        <span className="score">{r.score.toFixed(3)}</span>
                      </div>
                    ))}
                    <p className="muted small">
                      {vector.out.elapsed_ms} ms · {vector.out.model} · {vector.out.dimensions}d
                    </p>
                  </div>
                )}
              </>
            )}

            {tab === 'alerts' && (
              <>
                <StatusLine
                  ready={health?.checks?.change_stream?.running}
                  label="Change Stream"
                  status={health?.checks?.change_stream?.running ? 'READY' : 'parado'}
                />
                {alerts.length === 0 ? (
                  <p className="muted small">
                    Nenhum alerta. Marque a rede e injete uma transação para acordar o listener.
                  </p>
                ) : (
                  alerts.map((a) => (
                    <div className="alert" key={a.transaction_id}>
                      <strong>R$ {fmt(Math.round(a.amount))}</strong> tocou a rede {a.ring_id ?? a.case_id}
                      <em>
                        <code>{a.transaction_id}</code> · verificação em {a.lookup_ms} ms ·{' '}
                        {new Date(a.at).toLocaleTimeString('pt-BR')}
                      </em>
                    </div>
                  ))
                )}
              </>
            )}
          </div>

          <details className="query-drawer">
            <summary>Query executada</summary>
            <pre>{JSON.stringify(pipelinePreview(subject, net), null, 2)}</pre>
          </details>
        </aside>
      </main>
    </div>
  )
}

function NodePanel({ node, neighbours, byId, hovering, depth, onPick }) {
  if (!node) {
    return (
      <p className="muted small">
        Passe o mouse sobre um nó para isolar seus vizinhos, ou clique para fixar o detalhe aqui.
        A expansão atual vai até {depth} salto{depth > 1 ? 's' : ''}.
      </p>
    )
  }
  const vizinhos = [...(neighbours.get(node.id) ?? [])]
  return (
    <div className="node-detail">
      <h3>{node.label}</h3>
      <span className={`badge ${node.ring_id ? 'badge-bad' : 'badge-ok'}`}>
        {node.ring_id ? `rede ${node.ring_id}` : 'sem rede conhecida'}
      </span>
      <dl>
        <div><dt>Saltos da entrada</dt><dd>{node.hops}</dd></div>
        <div><dt>Grau no subgrafo</dt><dd>{vizinhos.length}</dd></div>
        <div><dt>Papel</dt><dd>{node.is_root ? 'ponto de entrada' : 'alcançado pelo traversal'}</dd></div>
        {node.risk_flags?.length > 0 && (
          <div><dt>Marcas de risco</dt><dd>{node.risk_flags.join(', ')}</dd></div>
        )}
      </dl>

      {vizinhos.length > 0 && (
        <>
          <h4>Vizinhos diretos</h4>
          <div className="chips">
            {vizinhos.map((id) => {
              const v = byId.get(id)
              return (
                <button
                  key={id}
                  className={`chip ${v?.ring_id ? 'chip-ring' : ''}`}
                  onClick={() => onPick(id)}
                  title={v?.ring_id ? `rede ${v.ring_id}` : 'sem rede conhecida'}
                >
                  {v?.label ?? id.slice(0, 12)}
                </button>
              )
            })}
          </div>
        </>
      )}

      <code className="id">{node.id}</code>
      {hovering && <p className="muted small">Vizinhos em destaque; o resto do grafo está atenuado.</p>}
    </div>
  )
}

function Metric({ k, v, tone }) {
  return (
    <div className={`metric ${tone ?? ''}`}>
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  )
}

function metricTone(ms) {
  if (ms == null) return undefined
  if (ms < 500) return 'good'
  if (ms < 2000) return 'warn'
  return 'bad'
}

function HealthBadge({ health }) {
  if (!health) return <span className="badge badge-warn">verificando…</span>
  if (health.status === 'ok') return <span className="badge badge-ok">✓ tudo pronto</span>
  if (health.status === 'offline') return <span className="badge badge-bad">✕ backend offline</span>
  return <span className="badge badge-warn">⚠ degradado</span>
}

function StatusLine({ ready, label, status }) {
  return (
    <p className="status-line">
      <span className={`badge ${ready ? 'badge-ok' : 'badge-warn'}`}>{label} {status ?? '—'}</span>
    </p>
  )
}

function Degraded({ d, feature }) {
  return (
    <p className="notice notice-warn">
      {feature} indisponível (<code>{d.index ?? '—'}</code>: {d.status}). O traversal continua funcionando — a
      degradação é por recurso, não por tela.
    </p>
  )
}

function pipelinePreview(personId, net) {
  const q = net?.query
  const stage = {
    from: 'connections',
    startWith: '$_id',
    connectFromField: 'to',
    connectToField: 'from',
    as: 'network',
    maxDepth: net?.depth ?? 1,
    depthField: 'hops',
  }
  if (q?.restrict_search_with_match) stage.restrictSearchWithMatch = q.restrict_search_with_match
  return [{ $match: { _id: personId } }, { $graphLookup: stage }]
}
