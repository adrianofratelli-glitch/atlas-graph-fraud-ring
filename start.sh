#!/bin/bash
# Sobe backend (8350) e frontend (5350). Porta estrita: se já houver alguém
# escutando, o processo existente é preservado e este script falha — a política
# do workspace é nunca matar o que não é seu.
set -u

BASE="$(cd "$(dirname "$0")" && pwd)"
BACKEND_LOG="${TMPDIR:-/tmp}/atlas-graph-grupo-economico-backend.log"
FRONTEND_LOG="${TMPDIR:-/tmp}/atlas-graph-grupo-economico-frontend.log"
BACKEND_PORT=8350
FRONTEND_PORT=5350

fail() { echo "❌ $1" >&2; exit 1; }
cleanup() { kill "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true; }

wait_for_url() {
  local url="$1" attempts="${2:-40}"
  for ((i = 1; i <= attempts; i++)); do
    curl --fail --silent --max-time 2 "$url" >/dev/null && return 0
    sleep 1
  done
  return 1
}

command -v curl >/dev/null || fail "curl não encontrado."
command -v npm >/dev/null || fail "npm não encontrado."
[[ -f "$BASE/.env" ]] || fail "Falta .env. Copie .env.example e preencha MONGODB_URI."
[[ -x "$BASE/backend/venv/bin/uvicorn" ]] || fail "Virtualenv do backend ausente. Veja a seção Setup do README."
[[ -d "$BASE/frontend/node_modules" ]] || fail "Dependências do frontend ausentes. Rode npm install em frontend/."

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "Porta $port já está ocupada; o processo existente foi preservado."
  fi
done

echo "🕸  Grupo econômico & risco de crédito · MongoDB Atlas"
echo "=================================="

echo "▶ backend (porta $BACKEND_PORT)..."
cd "$BASE/backend"
UVICORN_ARGS=(main:app --host 127.0.0.1 --port "$BACKEND_PORT")
[[ "${DEV:-0}" == "1" ]] && UVICORN_ARGS+=(--reload)
venv/bin/uvicorn "${UVICORN_ARGS[@]}" > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

if ! wait_for_url "http://127.0.0.1:$BACKEND_PORT/health/live" 40; then
  echo "❌ Backend não ficou pronto. Últimas linhas do log:" >&2
  tail -n 25 "$BACKEND_LOG" >&2
  cleanup; exit 1
fi

echo "▶ frontend (porta $FRONTEND_PORT)..."
cd "$BASE/frontend"
if [[ "${DEV:-0}" == "1" ]]; then
  npm run dev > "$FRONTEND_LOG" 2>&1 &
else
  # Reconstrói só quando a fonte mudou: o bundle pronto evita manter um watcher
  # de arquivos por PoV aberta.
  if [[ ! -d dist ]] || [[ -n "$(find src index.html vite.config.js package-lock.json -newer dist -print -quit 2>/dev/null)" ]]; then
    npm run build > "$FRONTEND_LOG" 2>&1 || { echo "❌ build do frontend falhou"; tail -n 20 "$FRONTEND_LOG" >&2; cleanup; exit 1; }
  fi
  npm run preview >> "$FRONTEND_LOG" 2>&1 &
fi
FRONTEND_PID=$!

if ! wait_for_url "http://127.0.0.1:$FRONTEND_PORT/" 40; then
  echo "❌ Frontend não ficou pronto." >&2
  tail -n 25 "$FRONTEND_LOG" >&2
  cleanup; exit 1
fi

echo
echo "✅ pronto"
echo "   UI      http://127.0.0.1:$FRONTEND_PORT"
echo "   API     http://127.0.0.1:$BACKEND_PORT/docs"
echo "   health  http://127.0.0.1:$BACKEND_PORT/health"
echo
echo "Antes de apresentar, rode o checklist em docs/demo-script.md."
echo "Ctrl+C encerra os dois."

trap cleanup EXIT INT TERM
wait
