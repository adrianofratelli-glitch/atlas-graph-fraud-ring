import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import GraphCanvas from './GraphCanvas'
import { api } from './api'

const DEPTHS = [1, 2, 3, 4, 5, 6]
const EDGE_TYPES = [
  ['shares_device', 'Device', '#0498ec'],
  ['shares_address', 'Address', '#ffc010'],
  ['same_pix_counterparty', 'Destination PIX key', '#00ed64'],
]
const TABS = [
  ['node', 'Node'],
  ['search', 'Search'],
  ['vector', 'Semantic'],
  ['alerts', 'Alerts'],
]

const fmt = (n) => (n == null ? '—' : n.toLocaleString('en-US'))

// Paráfrases que NÃO existem no corpus. Se a frase da consulta estivesse lá
// literalmente, o primeiro resultado seria ela mesma e a busca não provaria nada.
// Uma por escopo, porque a rede e a base falam de coisas diferentes: o anel
// justifica repasses informais, a população legítima paga aluguel.
const SUGESTAO_VETOR = {
  rede: 'informal settlement of amounts between people, no signed contract',
  base: 'I paid the monthly amount for the house I rent',
}

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
  const [search, setSearch] = useState({ q: '', out: null, degraded: null, scope: 'base' })
  const [vector, setVector] = useState({ text: '', out: null, degraded: null, scope: 'rede' })
  const [tab, setTab] = useState('node')
  const [picked, setPicked] = useState(null)
  const [hovered, setHovered] = useState(null)
  const [caseDetail, setCaseDetail] = useState(null)
  const [coaf, setCoaf] = useState(null)
  const [lastSim, setLastSim] = useState(null)

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
    setVector((p) => (p.text ? p : { ...p, text: SUGESTAO_VETOR[p.scope] }))
  }, [entry])

  useEffect(() => {
    const es = new EventSource('/api/alerts/stream')
    es.onmessage = (ev) => {
      const evento = JSON.parse(ev.data)
      setAlerts((prev) => [evento, ...prev].slice(0, 20))
      // Só alerta de verdade troca a aba. Eventos de manutenção e de verificação
      // sem alerta ficam disponíveis, mas não tiram o apresentador da tela em que
      // ele está — uma rajada deles no meio de uma busca sequestrava a interface.
      if (evento.type === 'ring_touch') setTab('alerts')
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

  // Recupera um caso já aberto no banco quando a tela recarrega.
  //
  // O `case_id` viaja junto com o nó, então a tela consegue se reconciliar com o
  // servidor sozinha. Sem isso, dar F5 no meio da apresentação deixava as contas
  // bloqueadas no banco e o botão "Close case" desabilitado — sem saída pela
  // interface a não ser resetar a demo inteira.
  useEffect(() => {
    if (caseInfo) return
    const aberto = (net?.nodes ?? []).find((n) => n.flagged && n.case_id)
    if (!aberto) return
    let vivo = true
    api
      .caseDetail(aberto.case_id)
      .then((d) => {
        if (!vivo) return
        setCaseDetail(d)
        setCaseInfo({
          case_id: aberto.case_id,
          accounts_flagged: d.accounts.length,
          people_flagged: d.case.people_flagged ?? d.people.length,
          elapsed_ms: d.case.elapsed_ms ?? '—',
          read_concern: 'snapshot',
          write_concern: 'majority',
        })
      })
      .catch(() => {})
    return () => {
      vivo = false
    }
  }, [net, caseInfo])

  // Os anéis presentes na expansão atual. `ring_id` é campo de filtro nos dois
  // índices de busca, então é por ele que o escopo desce até o motor.
  const ringIds = useMemo(
    () => [...new Set((net?.nodes ?? []).map((n) => n.ring_id).filter(Boolean))],
    [net]
  )
  const nodeIds = useMemo(() => (net?.nodes ?? []).map((n) => n.id), [net])

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

  // O nó **fixado** manda no painel; o hover só preenche quando nada está fixado.
  //
  // Era o contrário, e por isso clicar num nó era inútil: bastava mover o mouse
  // em direção ao painel para o hover trocar o conteúdo, e o analista nunca
  // conseguia ler o nó que tinha escolhido. Clicar precisa parar o painel.
  const detail = byId.get(picked ?? hovered)
  const pinned = Boolean(picked && byId.has(picked))


  const refreshNet = () => expand(depth, subject)

  const doFlag = async () => {
    if (!ringNodes.length) return
    setBusy(true)
    setError(null)
    try {
      const info = await api.flag(ringNodes, `rede alcançada em ${depth} saltos a partir de ${subject}`)
      setCaseInfo(info)
      setCoaf(null)
      setLastSim(null)
      // O detalhe traz o antes/depois das contas: é o que torna o efeito da
      // transação ACID visível em vez de uma linha de texto com o case_id.
      setCaseDetail(await api.caseDetail(info.case_id).catch(() => null))
      await refreshNet()
    } catch (e) {
      // 409 com `already_open`: o backend recusou porque já há caso aberto sobre
      // estes nós. Em vez de mostrar erro, a tela traz aquele caso para a frente
      // — o botão de encerrar fica ao lado, que é a saída que o analista quer.
      if (e.detail?.already_open && e.detail.case_id) {
        const d = await api.caseDetail(e.detail.case_id).catch(() => null)
        setCaseDetail(d)
        setCaseInfo({
          case_id: e.detail.case_id,
          accounts_flagged: d?.accounts.length,
          people_flagged: d?.people.length,
          elapsed_ms: '—',
          read_concern: 'snapshot',
          write_concern: 'majority',
        })
        setError('A case is already open on this network. Close it before opening another.')
      } else {
        setError(e.message)
      }
    } finally {
      setBusy(false)
    }
  }

  const doClose = async () => {
    if (!caseInfo?.case_id) return
    setBusy(true)
    try {
      await api.closeCase(caseInfo.case_id)
      setCaseInfo(null)
      setCaseDetail(null)
      setCoaf(null)
      setLastSim(null)
      await refreshNet()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const doReset = async () => {
    setBusy(true)
    try {
      await api.reset()
      setCaseInfo(null)
      setCaseDetail(null)
      setCoaf(null)
      setLastSim(null)
      setAlerts([])
      await refreshNet()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const doCoaf = async () => {
    if (!caseInfo?.case_id) return
    try {
      setCoaf(await api.coaf(caseInfo.case_id))
    } catch (e) {
      setError(e.message)
    }
  }

  // Injeta com OU sem o caso aberto. Sem caso, `expect_alert` volta falso e a
  // tela diz que o silêncio é o resultado certo — é essa metade que prova que o
  // alerta lê o estado em vez de disparar sozinho.
  const doSimulate = async () => {
    try {
      const out = await api.simulate((net?.nodes ?? []).map((n) => n.id))
      setLastSim(out)
      if (!out.expect_alert) setTab('alerts')
    } catch (e) {
      setError(e.message)
    }
  }

  const doSearch = async (e, scope = search.scope) => {
    e?.preventDefault()
    try {
      const out = await api.searchPeople(search.q, { scope, personIds: nodeIds, ringIds })
      setSearch((p) => ({ ...p, out, degraded: null, scope }))
    } catch (err) {
      setSearch((p) => ({ ...p, out: null, degraded: err.degraded ?? { status: err.message } }))
    }
  }

  const doVector = async (e, scope = vector.scope) => {
    e?.preventDefault()
    try {
      const out = await api.similarReasons(vector.text, { scope, ringIds })
      setVector((p) => ({ ...p, out, degraded: null, scope }))
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
      <a className="pov-skip-link" href="#main-content">Skip to content</a>

      <header className="topbar">
        <span className="brand">Fraud ring graph</span>
        <span className="thesis">
          One transaction on its own looks legitimate. <em>The network around it does not.</em>
        </span>
        <span className="spacer" />
        <span className="hint">$graphLookup · Atlas Search · Vector Search · Change Streams</span>
        <HealthBadge health={health} />
      </header>

      <main className="body" id="main-content">
        <aside className="rail" aria-label="Controls">
          <section className="block">
            <h2>Entry point</h2>
            <select value={subject} onChange={(e) => setSubject(e.target.value)} aria-label="Person under investigation">
              <optgroup label="Accounts under suspicion">
                {(entry?.suspects ?? []).map((s) => (
                  <option key={s.person_id} value={s.person_id}>
                    {s.person_name} · {s.ring_id}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Clean population (control)">
                {(entry?.clean ?? []).map((c) => (
                  <option key={c._id} value={c._id}>{c.name}</option>
                ))}
              </optgroup>
            </select>
          </section>

          <section className="block">
            <h2>Depth</h2>
            <div className="depth-row" role="group" aria-label="Traversal depth">
              {DEPTHS.map((d) => (
                <button key={d} className="depth-btn" aria-pressed={depth === d} onClick={() => setDepth(d)}>
                  {d}
                </button>
              ))}
            </div>
          </section>

          <section className="block">
            <h2>Edges</h2>
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
              Prune hubs
            </label>
          </section>

          <section className="block block-action">
            <h2>Analyst action</h2>
            <button className="btn btn-primary btn-block" onClick={doFlag} disabled={busy || !ringNodes.length}>
              Flag {ringNodes.length || 0} nodes for investigation
            </button>
            {!caseInfo && ringNodes.length > 0 && depth < 3 && (
              <p className="muted small">
                You are at hop {depth}. The ring usually closes at 3 — flagging now blocks only
                the {ringNodes.length} nodes in this expansion, not the whole ring.
              </p>
            )}

            {caseInfo && <CaseCard info={caseInfo} detail={caseDetail} coaf={coaf} onCoaf={doCoaf} />}

            <button className="btn btn-ghost btn-block" disabled={busy || !net?.nodes?.length} onClick={doSimulate}>
              Inject transaction into the network
            </button>
            {lastSim && (
              <p className={`notice ${lastSim.expect_alert ? 'notice-bad' : 'notice-ok'}`}>
                {lastSim.expect_alert
                  ? 'Transaction injected into a blocked account — the alert should show up in the Alerts tab.'
                  : 'Transaction injected while the network is FREE. No alert is the correct outcome: the listener read the state and had nothing to report.'}
              </p>
            )}

            <div className="btn-row">
              <button className="btn btn-ghost" disabled={busy || !caseInfo} onClick={doClose}>
                Close case
              </button>
              <button className="btn btn-ghost" disabled={busy} onClick={doReset}>
                Reset demo
              </button>
            </div>
            <p className="muted small">
              Closing the case unblocks the accounts and lets you inject the same transaction
              against a free network — the A/B that shows the change stream reading state rather
              than firing on its own.
            </p>
          </section>
        </aside>

        <section className="stage">
          <div className="metrics" role="group" aria-label="Expansion metrics">
            <Metric k="Nodes" v={fmt(stats?.nodes)} />
            <Metric k="Edges" v={fmt(stats?.edges_unique)} />
            <Metric k="In ring" v={fmt(stats?.ring_nodes)} tone={stats?.ring_nodes ? 'bad' : undefined} />
            {stats?.flagged_nodes > 0 && (
              <Metric k="Blocked" v={fmt(stats.flagged_nodes)} tone="warn" />
            )}
            <Metric k="Time" v={stats ? `${stats.elapsed_ms} ms` : '—'} tone={metricTone(stats?.elapsed_ms)} />
            <Metric k="Hop" v={net?.depth ?? '—'} />
            <span className="spacer" />
            <span className="legend">
              <span><i className="dot" style={{ background: '#00ed64' }} /> entry</span>
              <span><i className="dot" style={{ background: '#ff6960' }} /> ring</span>
              <span><i className="dot dot-plain" /> clean</span>
              <span><i className="dot dot-flagged" /> blocked</span>
            </span>
          </div>

          <div className="canvas-wrap">
            {net?.nodes?.length ? (
              <GraphCanvas
                data={net}
                pinned={picked}
                onSelect={setPicked}
                onHover={setHovered}
              />
            ) : (
              <div className="canvas empty">
                {busy ? 'expanding…' : error ? error : 'No links from this entry point.'}
              </div>
            )}
            {stats?.truncated && (
              <p className="canvas-note">
                truncated at {fmt(stats.max_nodes)} nodes out of {fmt(stats.edges_before_truncation)} edges — high
                fan-out, behaviour described in <code>LIMITATIONS.md §4</code>
              </p>
            )}
          </div>
        </section>

        <aside className="inspector" aria-label="Detail and evidence">
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
                {key === 'alerts' && alerts.length > 0 && (
                  <span className="tab-count">{alerts.length}</span>
                )}
              </button>
            ))}
          </div>

          <div className="tab-body">
            {tab === 'node' && (
              <NodePanel
                node={detail}
                neighbours={neighbours}
                byId={byId}
                hovering={Boolean(hovered)}
                pinned={pinned}
                depth={depth}
                onPick={setPicked}
                onUnpin={() => setPicked(null)}
              />
            )}

            {tab === 'search' && (
              <>
                <StatusLine ready={searchReady} label="Atlas Search" status={Object.values(idx)[0]} />
                {entry?.entity_resolution_case && (
                  <p className="muted small">
                    The dataset contains <code>{entry.entity_resolution_case.typo_name}</code>, spelled almost
                    exactly like <code>{entry.entity_resolution_case.real_name}</code>. No edge connects the two —
                    only <code>fuzzy</code> reaches it.
                  </p>
                )}
                <ScopePicker
                  value={search.scope}
                  disabled={!nodeIds.length}
                  onChange={(sc) => {
                    setSearch((p) => ({ ...p, scope: sc }))
                    if (search.q.trim()) doSearch(null, sc)
                  }}
                  labels={[
                    ['base', `Whole database`],
                    ['rede', `This network only (${nodeIds.length})`],
                  ]}
                />
                <form onSubmit={doSearch} className="inline-form">
                  <input
                    type="text"
                    value={search.q}
                    onChange={(e) => setSearch((p) => ({ ...p, q: e.target.value }))}
                    aria-label="Name to resolve"
                  />
                  <button className="btn btn-ghost" disabled={!searchReady || !search.q.trim()}>Search</button>
                </form>
                {search.degraded && <Degraded d={search.degraded} feature="Atlas Search" />}
                {search.out && (
                  <div className="rows">
                    {search.out.scope === 'base' && (
                      <p className="muted small">
                        {search.out.na_rede > 0
                          ? `${search.out.na_rede} in the network on screen, listed first; the rest is the whole database.`
                          : 'No result is in the network on screen — all of them come from the database.'}
                      </p>
                    )}
                    {search.out.results.map((r) => (
                      <div className={`row ${r.na_rede ? 'hit' : ''}`} key={r._id}>
                        <span className="grow">
                          {r.name}
                          <em>
                            {/* A relação com o grafo é a informação que faltava:
                                sem ela, "Diego" devolvia dez Diegos e o analista
                                não sabia qual deles estava sob investigação. */}
                            <span className={r.na_rede ? 'tag tag-in' : 'tag'}>
                              {r.na_rede ? 'in network' : 'outside'}
                            </span>
                            {r.ring_id ? ` ring ${r.ring_id}` : ' no ring'}
                            {r.city ? ` · ${r.city}` : ''}
                          </em>
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
                {/* A pergunta é o que dá sentido ao painel. Solto na base, ele
                    só mostra que o motor entende sinônimo; escopado na rede, ele
                    responde "que desculpa essas contas usam para mover dinheiro?" */}
                <p className="muted small">
                  {vector.scope === 'rede'
                    ? 'What reasons do the accounts in this network give? The same excuse rewritten several ways is not a coincidence — and keyword search does not group it.'
                    : 'Meaning-based search over the whole database: phrases that say the same thing without sharing a single word.'}
                </p>
                <ScopePicker
                  value={vector.scope}
                  disabled={!ringIds.length}
                  onChange={(sc) => {
                    setVector((p) => ({ ...p, text: SUGESTAO_VETOR[sc], scope: sc }))
                    doVector(null, sc)
                  }}
                  labels={[
                    ['rede', ringIds.length ? `This network only (${ringIds.join(', ')})` : 'This network only'],
                    ['base', 'Whole database'],
                  ]}
                />
                <form onSubmit={doVector} className="inline-form">
                  <input
                    type="text"
                    value={vector.text}
                    onChange={(e) => setVector((p) => ({ ...p, text: e.target.value }))}
                    aria-label="Reason text"
                  />
                  <button className="btn btn-ghost" disabled={!vectorReady || !vector.text.trim()}>Search</button>
                </form>
                {vector.degraded && <Degraded d={vector.degraded} feature="Vector Search" />}
                {vector.out && (
                  <div className="rows">
                    <p className={vector.out.formas_distintas > 2 ? 'notice notice-warn' : 'muted small'}>
                      {vector.out.scope === 'rede'
                        ? `${vector.out.formas_distintas} different ways of saying the same thing inside this network.`
                        : `${vector.out.formas_distintas} different ways across the whole database.`}
                    </p>
                    {vector.out.results.map((r) => (
                      <div className={`row ${r.ring_id ? 'hit' : ''}`} key={r.reason_text}>
                        <span className="grow">
                          {r.reason_text}
                          <em>{fmt(r.ocorrencias_no_bloco)} occurrences · avg R$ {fmt(Math.round(r.amount))}</em>
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
                    Nothing yet. Inject a transaction: with the network flagged you get an alert,
                    without it you get the check — and both show up here.
                  </p>
                ) : (
                  alerts.map((a) =>
                    a.type === 'checked' ? (
                      /* O silêncio precisa ter forma. Sem este evento, injetar numa
                         rede livre não mostrava nada e parecia demo travada — quando
                         é justamente a metade que prova que o alerta lê o estado. */
                      <div className="alert alert-check" key={a.transaction_id}>
                        <strong>R$ {fmt(Math.round(a.amount))}</strong> checked — no account under
                        investigation, no alert
                        <em>
                          <code>{a.transaction_id}</code> · {a.checked_accounts?.length ?? 0} accounts
                          checked in {a.lookup_ms} ms · {new Date(a.at).toLocaleTimeString('en-US')}
                        </em>
                      </div>
                    ) : a.type === 'edge_materialized' ? (
                      <div className="alert alert-check" key={a.transaction_id ?? a.at}>
                        <strong>edge created live</strong> by the change stream
                        <em>{new Date(a.at).toLocaleTimeString('en-US')}</em>
                      </div>
                    ) : (
                      <div className="alert" key={a.transaction_id}>
                        <strong>R$ {fmt(Math.round(a.amount))}</strong> touched ring {a.ring_id ?? a.case_id}
                        <em>
                          <code>{a.transaction_id}</code> · checked in {a.lookup_ms} ms ·{' '}
                          {new Date(a.at).toLocaleTimeString('en-US')}
                        </em>
                      </div>
                    )
                  )
                )}
              </>
            )}
          </div>

          <details className="query-drawer">
            <summary>Query executed</summary>
            <pre>{JSON.stringify(pipelinePreview(subject, net), null, 2)}</pre>
          </details>
        </aside>
      </main>
    </div>
  )
}

/**
 * O caso, na coluna onde a ação foi disparada — de propósito sem aba nova.
 *
 * Antes, tudo o que a transação ACID fazia aparecia como uma linha com o
 * `case_id`. O trabalho real (contas saindo de `active` para
 * `under_investigation`, junto com o registro de auditoria, num commit só) ficava
 * invisível, e a parte mais forte da demo passava despercebida.
 *
 * O relatório de compliance fica num `<details>` fechado: ele é a resposta para
 * "e o que o banco faz com isso?", que nem toda plateia pergunta.
 */
function CaseCard({ info, detail, coaf, onCoaf }) {
  const contas = detail?.accounts ?? []
  const bloqueadas = contas.filter((c) => c.status === 'under_investigation').length
  return (
    <div className="case-card">
      <p className="case-line">
        <span className="badge badge-bad">case open</span> <code>{info.case_id}</code>
      </p>
      <dl className="case-grid">
        <div><dt>Accounts blocked</dt><dd>{fmt(bloqueadas || info.accounts_flagged)}</dd></div>
        <div><dt>People flagged</dt><dd>{fmt(info.people_flagged)}</dd></div>
        <div><dt>Commit</dt><dd>{info.elapsed_ms} ms</dd></div>
        <div><dt>Guarantee</dt><dd>{info.read_concern} / {info.write_concern}</dd></div>
      </dl>
      <p className="muted small">
        All three writes — <code>accounts.status</code>, <code>people.risk_flags</code> and the
        audit document — committed together. Half the network blocked with no coherent audit
        record is worse than not having acted at all.
      </p>

      {contas.length > 0 && (
        <div className="case-diff">
          <span className="case-diff-h">before → after</span>
          {contas.slice(0, 4).map((c) => (
            // Uma pessoa pode ter mais de uma conta, e as linhas são por CONTA.
            // Sem o tipo, o mesmo nome aparecia duas vezes e parecia duplicata.
            <p key={c._id} className="case-diff-row">
              <span className="grow">
                {c.person_name ?? c._id.slice(0, 14)}
                {c.account_type && <em>{c.account_type}</em>}
              </span>
              <s>active</s> <b>blocked</b>
            </p>
          ))}
          {contas.length > 4 && <p className="muted small">and {fmt(contas.length - 4)} more accounts</p>}
        </div>
      )}

      <details className="case-compliance" onToggle={(e) => e.target.open && !coaf && onCoaf()}>
        <summary>What the bank is now required to do</summary>
        {!coaf ? (
          <p className="muted small">loading…</p>
        ) : (
          <div className="rows">
            <ComplianceRow
              titulo="COAF — report within 24h"
              norma={coaf.comunicacao.fundamento}
              linhas={[
                `deadline: ${new Date(coaf.comunicacao.prazo_limite).toLocaleString('en-US')}`,
                `${fmt(coaf.comunicacao.operacoes_analisadas)} operations · R$ ${fmt(Math.round(coaf.comunicacao.volume_analisado))}`,
                coaf.comunicacao.tipologia,
              ]}
            />
            <ComplianceRow
              titulo="MED — precautionary block"
              norma={coaf.med.fundamento}
              linhas={[
                `review by ${new Date(coaf.med.prazo_analise).toLocaleString('en-US')}`,
                coaf.med.observacao,
              ]}
            />
            <ComplianceRow
              titulo="LGPD — record of processing"
              norma={coaf.lgpd.fundamento}
              linhas={[coaf.lgpd.registro, coaf.lgpd.observacao]}
            />
            <p className="muted small">
              None of this is wired to an external system — and that is the point. The document
              comes entirely from the audit record that committed alongside the block.
            </p>
          </div>
        )}
      </details>
    </div>
  )
}

function ComplianceRow({ titulo, norma, linhas }) {
  return (
    <div className="row compliance">
      <span className="grow">
        <strong>{titulo}</strong>
        <em>{norma}</em>
        {linhas.filter(Boolean).map((l, i) => (
          <em key={i}>{l}</em>
        ))}
      </span>
    </div>
  )
}

function NodePanel({ node, neighbours, byId, hovering, pinned, depth, onPick, onUnpin }) {
  if (!node) {
    return (
      <p className="muted small">
        Hover a node to isolate its neighbours, or click to pin the detail here.
        The current expansion reaches {depth} hop{depth > 1 ? 's' : ''}.
      </p>
    )
  }
  const vizinhos = [...(neighbours.get(node.id) ?? [])]
  const abertura = node.first_account_opened_at ? new Date(node.first_account_opened_at) : null
  const ultima = node.last_account_opened_at ? new Date(node.last_account_opened_at) : null
  return (
    <div className="node-detail">
      <div className="node-head">
        <h3>{node.label}</h3>
        {/* Sem esta marca, o painel muda de conteúdo com o mouse e o analista não
            sabe de onde veio o que está lendo. Fixar precisa ser visível. */}
        {pinned && (
          <button className="pin-chip" onClick={onUnpin} title="Unpin the node">
            pinned ✕
          </button>
        )}
      </div>
      <span className={`badge ${node.ring_id ? 'badge-bad' : 'badge-ok'}`}>
        {node.ring_id ? `ring ${node.ring_id}` : 'no known ring'}
      </span>
      {node.flagged && <span className="badge badge-warn">under investigation · {node.case_id}</span>}
      <dl>
        <div><dt>Hops from entry</dt><dd>{node.hops}</dd></div>
        <div><dt>Degree in subgraph</dt><dd>{vizinhos.length}</dd></div>
        <div><dt>Role</dt><dd>{node.is_root ? 'entry point' : 'reached by traversal'}</dd></div>
        {node.city && <div><dt>City</dt><dd>{node.city}</dd></div>}
        <div><dt>Accounts</dt><dd>{fmt(node.accounts)}{node.account_types?.length ? ` · ${node.account_types.join(', ')}` : ''}</dd></div>
        {/* Data de abertura é evidência, não enfeite: contas de CPFs diferentes
            abertas na mesma janela, operando do mesmo aparelho, é fazenda de
            mulas — e o analista lê isso sem o apresentador narrar. */}
        {abertura && (
          <div>
            <dt>Oldest account</dt>
            <dd>{abertura.toLocaleDateString('en-US')}</dd>
          </div>
        )}
        {ultima && ultima.getTime() !== abertura?.getTime() && (
          <div>
            <dt>Newest account</dt>
            <dd>{ultima.toLocaleDateString('en-US')}</dd>
          </div>
        )}
        <div>
          <dt>Account status</dt>
          <dd>{node.flagged ? 'blocked' : 'active'}</dd>
        </div>
        {node.risk_flags?.length > 0 && (
          <div><dt>Risk flags</dt><dd>{node.risk_flags.join(', ')}</dd></div>
        )}
      </dl>

      {vizinhos.length > 0 && (
        <>
          <h4>Direct neighbours</h4>
          <div className="chips">
            {vizinhos.map((id) => {
              const v = byId.get(id)
              return (
                <button
                  key={id}
                  className={`chip ${v?.ring_id ? 'chip-ring' : ''}`}
                  onClick={() => onPick(id)}
                  title={v?.ring_id ? `ring ${v.ring_id}` : 'no known ring'}
                >
                  {v?.label ?? id.slice(0, 12)}
                </button>
              )
            })}
          </div>
        </>
      )}

      <code className="id">{node.id}</code>
      {hovering && !pinned && (
        <p className="muted small">Neighbours highlighted; the rest of the graph is dimmed.</p>
      )}
      {pinned && (
        <p className="muted small">
          Node pinned: hovering the graph still isolates neighbours, but no longer swaps this panel.
        </p>
      )}
    </div>
  )
}

/** Segmentado de escopo. Mesma linguagem visual dos botões de profundidade. */
function ScopePicker({ value, onChange, labels, disabled }) {
  return (
    <div className="scope-row" role="group" aria-label="Search scope">
      {labels.map(([key, label]) => (
        <button
          key={key}
          type="button"
          className="scope-btn"
          aria-pressed={value === key}
          disabled={disabled && key === 'rede'}
          onClick={() => value !== key && onChange(key)}
        >
          {label}
        </button>
      ))}
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
  if (!health) return <span className="badge badge-warn">checking…</span>
  if (health.status === 'ok') return <span className="badge badge-ok">✓ all ready</span>
  if (health.status === 'offline') return <span className="badge badge-bad">✕ backend offline</span>
  return <span className="badge badge-warn">⚠ degraded</span>
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
      {feature} unavailable (<code>{d.index ?? '—'}</code>: {d.status}). The traversal keeps working —
      degradation is per feature, not per screen.
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
