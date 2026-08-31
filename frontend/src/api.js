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
      typeof detail === 'string' ? detail : detail?.error ?? 'resource unavailable'
    )
    err.status = res.status
    // O corpo estruturado segue junto: um 409 de revisão já aberta carrega o
    // `case_id`, e a tela usa isso para oferecer a saída em vez de só reclamar.
    err.detail = typeof detail === 'object' ? detail : null
    err.degraded = res.status === 503 && detail && typeof detail === 'object' ? detail : null
    // 429 é backpressure deliberado do bulkhead do backend, não indisponibilidade:
    // a consulta analítica tem poucas vagas e recusa cedo para não atrasar a
    // consulta interativa. A tela precisa distinguir as duas coisas — "tente de
    // novo em instantes" e "este recurso está fora" pedem reações diferentes.
    err.saturado = res.status === 429 && detail && typeof detail === 'object' ? detail : null
    throw err
  }
  return res.json()
}

export const api = {
  health: () => call('/health'),
  entryPoints: () => call('/api/entry-points'),
  // O caminho principal da demo: consulta pontual por chave de negócio.
  group: (cnpj, depth) => call(`/api/group/${encodeURIComponent(cnpj)}?depth=${depth}`),
  // `scopeOnly` é o padrão do backend: a busca da tela procura dentro do grafo em
  // uso. Abrir para a base inteira é ação deliberada de entity resolution.
  searchCompanies: (q, { companyIds, nodeIds, scopeOnly = true } = {}) =>
    call('/api/search/companies', {
      method: 'POST',
      body: JSON.stringify({
        q,
        company_ids: companyIds ?? null,
        node_ids: nodeIds ?? null,
        scope_only: scopeOnly,
      }),
    }),
  // Análise automática: a pergunta é feita pelo grafo, não digitada.
  concentration: (companyIds) =>
    call('/api/analysis/concentration', {
      method: 'POST',
      body: JSON.stringify({ company_ids: companyIds }),
    }),
  openReview: (companyIds, reason, groupExposure) =>
    call('/api/credit/review', {
      method: 'POST',
      body: JSON.stringify({
        company_ids: companyIds,
        reason,
        group_exposure: groupExposure ?? null,
      }),
    }),
  // Hierarquia comercial: o escopo é derivado da árvore no servidor. A tela
  // manda quem é o usuário, nunca a lista de contas que ele pode ver.
  roster: () => call('/api/hierarchy/roster'),
  portfolio: (advisorId) =>
    call(`/api/hierarchy/${encodeURIComponent(advisorId)}/portfolio`),
  canSee: (advisorId, cnpj) =>
    call(`/api/hierarchy/${encodeURIComponent(advisorId)}/can-see/${encodeURIComponent(cnpj)}`),
  caseDetail: (caseId) => call(`/api/credit/case/${encodeURIComponent(caseId)}`),
  closeReview: (caseId) =>
    call(`/api/credit/close/${encodeURIComponent(caseId)}`, { method: 'POST' }),
  reset: () => call('/api/demo/reset', { method: 'POST' }),
}
