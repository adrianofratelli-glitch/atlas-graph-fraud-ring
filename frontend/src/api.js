/**
 * Cliente HTTP. Toda chamada passa por aqui para que a degradação (503 por índice
 * ausente) tenha um formato único que a UI saiba interpretar.
 */
const base = ''

async function call(path, options = {}) {
  const res = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail
    try {
      detail = (await res.json()).detail
    } catch {
      detail = res.statusText
    }
    const err = new Error(typeof detail === 'string' ? detail : 'recurso indisponível')
    err.status = res.status
    // 503 com `feature` é degradação prevista (índice BUILDING/MISSING), não falha.
    err.degraded = res.status === 503 && detail && typeof detail === 'object' ? detail : null
    throw err
  }
  return res.json()
}

export const api = {
  health: () => call('/health'),
  entryPoints: () => call('/api/entry-points'),
  network: (personId, { depth, edgeTypes, pruneHubs }) => {
    const p = new URLSearchParams()
    if (depth != null) p.set('depth', depth)
    if (edgeTypes?.length) p.set('edge_types', edgeTypes.join(','))
    p.set('prune_hubs', pruneHubs ? 'true' : 'false')
    return call(`/api/network/${encodeURIComponent(personId)}?${p}`)
  },
  searchPeople: (q) => call(`/api/search/people?q=${encodeURIComponent(q)}`),
  similarReasons: (text) =>
    call('/api/search/similar-reasons', { method: 'POST', body: JSON.stringify({ text }) }),
  flag: (personIds, reason) =>
    call('/api/investigation/flag', {
      method: 'POST',
      body: JSON.stringify({ person_ids: personIds, reason }),
    }),
  simulate: () => call('/api/demo/simulate-transaction', { method: 'POST', body: '{}' }),
  reset: () => call('/api/demo/reset', { method: 'POST' }),
}
