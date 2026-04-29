#!/usr/bin/env bash
# Lerim test runner — auto-activates venv and runs test groups.
set -euo pipefail

usage() {
  cat <<'USAGE'
Lerim test runner

Usage:
  tests/run_tests.sh [lint|unit|smoke|integration|e2e|quality|all] [options]

Groups:
  lint          Run ruff linter
  unit          Unit tests (no LLM calls)
  smoke         Smoke tests (quick LLM round-trips)
  integration   Integration tests (real LLM pipelines)
  e2e           End-to-end tests (full sync/maintain flows)
  quality       Compile check + pip check
  all           Run all groups in order

Options:
  --llm-provider PROVIDER
  --llm-model MODEL
  --llm-base-url URL
  --agent-provider PROVIDER
  --agent-model MODEL


Environment overrides are also supported (e.g. LERIM_LLM_PROVIDER).
USAGE
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- Auto-activate venv if not already active ---
VENV_DIR="$ROOT_DIR/.venv"
if [[ -z "${VIRTUAL_ENV:-}" && -f "$VENV_DIR/bin/activate" ]]; then
  echo "Activating venv at $VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
elif [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "Warning: no .venv found at $VENV_DIR and no venv active."
  echo "Run: uv venv && source .venv/bin/activate && uv pip install -e ."
fi

# --- Load .env if present ---
ENV_FILE="$ROOT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

GROUP=${1:-unit}
if [[ "$GROUP" == "-h" || "$GROUP" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

LLM_PROVIDER=${LLM_PROVIDER:-openrouter}
LLM_MODEL=${LLM_MODEL:-openai/gpt-5-nano}
LLM_BASE_URL=${LLM_BASE_URL:-}

AGENT_PROVIDER=${AGENT_PROVIDER:-minimax}
AGENT_MODEL=${AGENT_MODEL:-MiniMax-M2.5}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --llm-provider) LLM_PROVIDER="$2"; shift 2 ;;
    --llm-provider=*) LLM_PROVIDER="${1#*=}"; shift ;;
    --llm-model) LLM_MODEL="$2"; shift 2 ;;
    --llm-model=*) LLM_MODEL="${1#*=}"; shift ;;
    --llm-base-url) LLM_BASE_URL="$2"; shift 2 ;;
    --llm-base-url=*) LLM_BASE_URL="${1#*=}"; shift ;;
    --agent-provider) AGENT_PROVIDER="$2"; shift 2 ;;
    --agent-provider=*) AGENT_PROVIDER="${1#*=}"; shift ;;
    --agent-model) AGENT_MODEL="$2"; shift 2 ;;
    --agent-model=*) AGENT_MODEL="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

print_section() {
  printf "\n== %s ==\n" "$1"
}

print_kv() {
  printf "  - %-24s %s\n" "$1" "$2"
}

print_section "Lerim test runner"
print_kv "Group" "$GROUP"
print_kv "Python" "$(command -v python || echo 'not found')"
print_kv "Venv" "${VIRTUAL_ENV:-not active}"
print_kv "LLM" "provider=$LLM_PROVIDER model=$LLM_MODEL"
print_kv "Agent" "provider=$AGENT_PROVIDER model=$AGENT_MODEL"

key_status() {
  local key="$1"
  if [[ -n "${!key:-}" ]]; then
    echo "set"
  else
    echo "missing"
  fi
}
print_section "Key status"
print_kv "ZAI_API_KEY" "$(key_status ZAI_API_KEY)"
print_kv "ZAI_CODING_API_KEY" "$(key_status ZAI_CODING_API_KEY)"
print_kv "OPENAI_API_KEY" "$(key_status OPENAI_API_KEY)"
print_kv "OPENROUTER_API_KEY" "$(key_status OPENROUTER_API_KEY)"
print_kv "ANTHROPIC_API_KEY" "$(key_status ANTHROPIC_API_KEY)"
print_kv "MINIMAX_API_KEY" "$(key_status MINIMAX_API_KEY)"
print_kv "OPENCODE_API_KEY" "$(key_status OPENCODE_API_KEY)"

# Config comes from TOML layers now (src/lerim/config/default.toml -> ~/.lerim/config.toml -> project).
# Only API keys are read from env (ANTHROPIC_API_KEY, OPENROUTER_API_KEY, ZAI_API_KEY).
# Tests use LERIM_CONFIG env var to point at tests/test_config.toml (auto-applied by conftest.py).

# --- Build pytest command ---
PYTEST_CMD=()
if [[ -n "${VIRTUAL_ENV:-}" ]] && command -v python >/dev/null 2>&1; then
  PYTEST_CMD=(python -m pytest)
elif command -v uv >/dev/null 2>&1; then
  PYTEST_CMD=(uv run pytest)
elif command -v python >/dev/null 2>&1; then
  PYTEST_CMD=(python -m pytest)
elif command -v python3 >/dev/null 2>&1; then
  PYTEST_CMD=(python3 -m pytest)
else
  echo "ERROR: no pytest runner found. Activate a venv or install uv."
  exit 1
fi

# --- Run from project root so pytest can find tests module ---
cd "$ROOT_DIR"

run_unit() {
  print_section "Unit tests"
  "${PYTEST_CMD[@]}" tests/unit/ -x -q
}

run_integration() {
  print_section "Integration tests"
  export LERIM_INTEGRATION=1
  export LERIM_LLM_INTEGRATION=1
  export LERIM_EMBEDDINGS_INTEGRATION=1
  "${PYTEST_CMD[@]}" tests/integration/ -q -n 1
}

run_e2e() {
  print_section "End-to-end tests"
  export LERIM_E2E=1
  "${PYTEST_CMD[@]}" tests/e2e/ -q -n 1
}

run_smoke() {
  print_section "Smoke tests"
  export LERIM_SMOKE=1
  "${PYTEST_CMD[@]}" tests/smoke/ -q -n 1
}

run_lint() {
  print_section "Lint"
  if ! command -v ruff >/dev/null 2>&1; then
    echo "Ruff not found; install with: uv pip install -e \".[lint]\""
    return 1
  fi
  ruff check .
}

run_quality() {
  print_section "Quality checks"
  python -m compileall -q src/lerim
  if python -m pip --version >/dev/null 2>&1; then
    python -m pip check
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    uv pip check
    return
  fi
  echo "pip check unavailable; skipping"
}

case "$GROUP" in
  unit)
    run_unit
    ;;
  integration)
    run_integration
    ;;
  e2e)
    run_e2e
    ;;
  smoke)
    run_smoke
    ;;
  lint)
    run_lint
    ;;
  quality)
    run_quality
    ;;
  all)
    run_lint
    run_unit
    run_smoke
    run_integration
    run_e2e
    run_quality
    ;;
  *)
    echo "Unknown group: $GROUP"
    usage
    exit 1
    ;;
esac
