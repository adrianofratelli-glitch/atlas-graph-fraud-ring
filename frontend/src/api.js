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
    const err = new Error(
      typeof detail === 'string' ? detail : detail?.error ?? 'recurso indisponível'
    )
    err.status = res.status
    // O corpo estruturado segue junto: um 409 de caso já aberto carrega o
    // `case_id`, e a tela usa isso para oferecer a saída em vez de só reclamar.
    err.detail = typeof detail === 'object' ? detail : null
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
    // Sempre enviado, mesmo vazio: omitir o parâmetro significa "todos os tipos"
    // no backend, então desmarcar as três caixas mostrava o grafo inteiro.
    p.set('edge_types', (edgeTypes ?? []).join(','))
    p.set('prune_hubs', pruneHubs ? 'true' : 'false')
    return call(`/api/network/${encodeURIComponent(personId)}?${p}`)
  },
  // As duas buscas recebem a rede que está na tela: uma para escopar/anotar os
  // nomes, outra para restringir os motivos ao anel investigado.
  searchPeople: (q, { scope = 'base', personIds, ringIds } = {}) =>
    call('/api/search/people', {
      method: 'POST',
      body: JSON.stringify({ q, scope, person_ids: personIds ?? null, ring_ids: ringIds ?? null }),
    }),
  similarReasons: (text, { scope = 'rede', ringIds } = {}) =>
    call('/api/search/similar-reasons', {
      method: 'POST',
      body: JSON.stringify({ text, scope, ring_ids: ringIds ?? null }),
    }),
  flag: (personIds, reason) =>
    call('/api/investigation/flag', {
      method: 'POST',
      body: JSON.stringify({ person_ids: personIds, reason }),
    }),
  // `person_ids` deixa a injeção acontecer também SEM nada marcado: é a metade
  // "não alerta" do A/B que prova que o change stream consulta o estado real.
  simulate: (personIds) =>
    call('/api/demo/simulate-transaction', {
      method: 'POST',
      body: JSON.stringify({ person_ids: personIds ?? null }),
    }),
  closeCase: (caseId) =>
    call(`/api/investigation/close/${encodeURIComponent(caseId)}`, { method: 'POST' }),
  caseDetail: (caseId) => call(`/api/investigation/case/${encodeURIComponent(caseId)}`),
  coaf: (caseId) => call(`/api/investigation/coaf/${encodeURIComponent(caseId)}`),
  reset: () => call('/api/demo/reset', { method: 'POST' }),
}
