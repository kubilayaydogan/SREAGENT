#!/usr/bin/env bash
# deploy/out/secrets.env → deploy/out/agent.env (SRE Agent container ortamı). Çıktı gitignored, chmod 600.
# Kullanım: scripts/gen-agent-env.sh [secrets.env] [çıktı]
set -euo pipefail
SRC="${1:-deploy/out/secrets.env}"; OUT="${2:-deploy/out/agent.env}"
set -a; . "$SRC"; set +a
: "${DT_ENV:?DT_ENV yok}" "${T_MCP:?T_MCP (MCP platform token) yok}"
umask 077
{
  echo "DT_ENV=${DT_ENV}"
  echo "DT_PLATFORM_TOKEN=${T_MCP}"
  echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}"
  echo "CLAUDE_MODEL=${CLAUDE_MODEL:-claude-sonnet-5-5}"
  echo "LANGSMITH_API_KEY=${LANGSMITH_API_KEY:-}"
  echo "LANGSMITH_TRACING=${LANGSMITH_TRACING:-true}"
  echo "LANGSMITH_PROJECT=${LANGSMITH_PROJECT:-airlab-sre-agent}"
} > "$OUT"
chmod 600 "$OUT"
echo "yazıldı: $OUT (değerler gösterilmez)"
