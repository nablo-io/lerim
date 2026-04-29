#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Download a Hugging Face GGUF, import it into Ollama, then remove the temporary file.

Usage:
  hf-gguf-to-ollama.sh <repo[:selector-or-file.gguf]> [options]

Examples:
  ./hf-gguf-to-ollama.sh unsloth/Qwen3.6-27B-GGUF:UD-Q2_K_XL --name qwen36-27b-q2
  ./hf-gguf-to-ollama.sh unsloth/Qwen3.6-27B-GGUF:Qwen3.6-27B-UD-Q2_K_XL.gguf

Options:
  --name NAME              Ollama model name. Default: derived from repo + selector.
  --ctx TOKENS             Ollama num_ctx parameter. Default: 8192.
  --temperature VALUE      Default: 0.6.
  --top-p VALUE            Default: 0.95.
  --top-k VALUE            Default: 20.
  --repeat-penalty VALUE   Default: 1.0.
  --keep-download          Keep the temporary download directory for debugging.
  --force                  Overwrite an existing Ollama model with the same name.
  -h, --help               Show this help.

Requirements:
  - ollama
  - hf CLI from huggingface_hub
  - python3 with huggingface_hub installed

Install HF tooling:
  python3 -m pip install -U huggingface_hub
EOF
}

log() {
  printf '[hf-gguf-to-ollama] %s\n' "$*"
}

die() {
  printf '[hf-gguf-to-ollama] error: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

slugify() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

model_ref=""
name=""
ctx="8192"
temperature="0.6"
top_p="0.95"
top_k="20"
repeat_penalty="1.0"
keep_download="false"
force="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --name)
      [[ $# -ge 2 ]] || die "--name requires a value"
      name="$2"
      shift 2
      ;;
    --ctx)
      [[ $# -ge 2 ]] || die "--ctx requires a value"
      ctx="$2"
      shift 2
      ;;
    --temperature)
      [[ $# -ge 2 ]] || die "--temperature requires a value"
      temperature="$2"
      shift 2
      ;;
    --top-p)
      [[ $# -ge 2 ]] || die "--top-p requires a value"
      top_p="$2"
      shift 2
      ;;
    --top-k)
      [[ $# -ge 2 ]] || die "--top-k requires a value"
      top_k="$2"
      shift 2
      ;;
    --repeat-penalty)
      [[ $# -ge 2 ]] || die "--repeat-penalty requires a value"
      repeat_penalty="$2"
      shift 2
      ;;
    --keep-download)
      keep_download="true"
      shift
      ;;
    --force)
      force="true"
      shift
      ;;
    --*)
      die "unknown option: $1"
      ;;
    *)
      [[ -z "$model_ref" ]] || die "only one model ref is supported"
      model_ref="$1"
      shift
      ;;
  esac
done

[[ -n "$model_ref" ]] || {
  usage
  exit 1
}

require_cmd ollama
require_cmd hf
require_cmd python3

python3 - <<'PY' >/dev/null 2>&1 || die "python package huggingface_hub is missing. Run: python3 -m pip install -U huggingface_hub"
import huggingface_hub
PY

repo="$model_ref"
selector=""
if [[ "$model_ref" == *:* ]]; then
  repo="${model_ref%%:*}"
  selector="${model_ref#*:}"
fi

[[ "$repo" == */* ]] || die "expected a Hugging Face model repo like owner/repo or owner/repo:quant"

filename="$(
  HF_REPO="$repo" HF_SELECTOR="$selector" python3 - <<'PY'
import os
import sys
from pathlib import PurePosixPath
from huggingface_hub import HfApi

repo = os.environ["HF_REPO"]
selector = os.environ["HF_SELECTOR"].strip()

try:
    files = HfApi().list_repo_files(repo_id=repo, repo_type="model")
except Exception as exc:
    print(f"failed to list files for {repo}: {exc}", file=sys.stderr)
    sys.exit(2)

ggufs = [
    path for path in files
    if path.lower().endswith(".gguf")
    and "mmproj" not in PurePosixPath(path).name.lower()
]

if not ggufs:
    print(f"no non-mmproj .gguf files found in {repo}", file=sys.stderr)
    sys.exit(3)

def basename(path: str) -> str:
    return PurePosixPath(path).name

matches = []
if selector:
    selector_lower = selector.lower()
    for path in ggufs:
        base_lower = basename(path).lower()
        path_lower = path.lower()
        if selector_lower == path_lower or selector_lower == base_lower:
            matches = [path]
            break
    if not matches:
        matches = [
            path for path in ggufs
            if selector_lower in basename(path).lower()
        ]
else:
    matches = ggufs

if len(matches) != 1:
    if selector:
        print(f"selector {selector!r} matched {len(matches)} files in {repo}.", file=sys.stderr)
    else:
        print(f"{repo} contains {len(matches)} GGUF files; pass :selector or :file.gguf.", file=sys.stderr)
    for candidate in sorted(matches or ggufs)[:30]:
        print(f"  - {candidate}", file=sys.stderr)
    if len(matches or ggufs) > 30:
        print("  ...", file=sys.stderr)
    sys.exit(4)

print(matches[0])
PY
)" || exit $?

if [[ -z "$name" ]]; then
  model_name_seed="${repo##*/}"
  if [[ -n "$selector" ]]; then
    model_name_seed="${model_name_seed}-${selector%.gguf}"
  else
    model_name_seed="${model_name_seed}-${filename%.gguf}"
  fi
  name="$(slugify "$model_name_seed")"
fi

if [[ "$force" != "true" ]] && ollama show "$name" >/dev/null 2>&1; then
  die "Ollama model '$name' already exists. Use --force to recreate it, or pass --name."
fi

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/hf-gguf-to-ollama.XXXXXX")"
cleanup() {
  if [[ "$keep_download" == "true" ]]; then
    log "kept temporary directory: $tmpdir"
  else
    rm -rf "$tmpdir"
  fi
}
trap cleanup EXIT

log "repo: $repo"
log "file: $filename"
log "ollama model: $name"
log "temporary directory: $tmpdir"

log "downloading GGUF..."
HF_HUB_CACHE="$tmpdir/.hf-cache" HF_XET_CACHE="$tmpdir/.xet-cache" \
  hf download "$repo" "$filename" --local-dir "$tmpdir"

gguf_path="$tmpdir/$filename"
[[ -f "$gguf_path" ]] || die "download completed, but expected file is missing: $gguf_path"

modelfile="$tmpdir/Modelfile"
cat > "$modelfile" <<EOF
FROM $gguf_path

PARAMETER num_ctx $ctx
PARAMETER temperature $temperature
PARAMETER top_p $top_p
PARAMETER top_k $top_k
PARAMETER repeat_penalty $repeat_penalty
EOF

if [[ "$force" == "true" ]] && ollama show "$name" >/dev/null 2>&1; then
  log "removing existing Ollama model: $name"
  ollama rm "$name" >/dev/null
fi

log "creating Ollama model..."
ollama create "$name" -f "$modelfile"

log "verifying model exists..."
ollama show "$name" >/dev/null

log "done. Run it with:"
printf '  ollama run %s\n' "$name"
