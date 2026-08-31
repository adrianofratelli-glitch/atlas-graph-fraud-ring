import React from 'react'

const SENSITIVE = /(?:authorization|password|secret|token|uri|cpf|cnpj|email|keymaterial)/i
function redact(value, key = '') {
  if (SENSITIVE.test(key)) return '<mascarado>'
  if (Array.isArray(value)) return value.map((item) => redact(item))
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, redact(v, k)]))
  return value
}
export default function QueryDetails({ query, operation, namespace, explain, note, label = 'Ver query / chamada executada' }) {
  if (!query && !operation) return null
  const payload = typeof query === 'string' ? query : JSON.stringify(redact(query), null, 2)
  return <details style={{ marginTop: 8, borderTop: '1px solid var(--border-subtle, #3d4f58)', paddingTop: 7, minWidth: 0, maxWidth: '100%' }}>
    <summary style={{ cursor: 'pointer', color: 'var(--text-muted, #889397)', fontSize: 12 }}>⌘ {label}</summary>
    <div style={{ marginTop: 8 }}><div className="mono muted" style={{ fontSize: 11, marginBottom: 6 }}>{[operation, namespace].filter(Boolean).join(' · ')}{note ? ` · ${note}` : ' · valores sensíveis mascarados'}</div>
      <pre style={{ margin: 0, padding: 10, overflowX: 'auto', borderRadius: 8, background: '#001923', fontSize: 11.5 }}>{payload}</pre>
      {explain && <pre style={{ margin: '8px 0 0', padding: 10, overflowX: 'auto', borderRadius: 8, background: '#112733', fontSize: 11.5 }}>{JSON.stringify(explain, null, 2)}</pre>}
    </div>
  </details>
}
