"""Command-line entrypoint for the BAML plus LangGraph extraction experiment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from baml_extract_agent.graph import (
    BAML_PROVIDER,
    MODEL_NAME,
    OLLAMA_BASE_URL,
    run_baml_extraction,
)


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and run the BAML extraction graph."""
    parser = argparse.ArgumentParser(
        description="Run the minimal BAML plus LangGraph Lerim extraction experiment."
    )
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument(
        "--context-db",
        default=Path(".tmp/baml_agents/context.sqlite3"),
        type=Path,
    )
    parser.add_argument("--project-root", default=Path.cwd(), type=Path)
    parser.add_argument("--session-id", default="baml-extract-session")
    parser.add_argument("--session-started-at", default=None)
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument(
        "--baml-provider",
        default=BAML_PROVIDER,
        choices=("ollama", "minimax", "openai-generic"),
    )
    parser.add_argument("--api-base-url", default=None)
    parser.add_argument("--api-key-env", default=None)
    parser.add_argument("--ollama-base-url", default=OLLAMA_BASE_URL)
    parser.add_argument("--temperature", default=0.0, type=float)
    parser.add_argument("--max-llm-calls", default=None, type=int)
    args = parser.parse_args(argv)

    api_key_env = args.api_key_env
    if api_key_env is None and args.baml_provider == "minimax":
        api_key_env = "MINIMAX_API_KEY"

    result = run_baml_extraction(
        trace_path=args.trace,
        context_db_path=args.context_db,
        project_root=args.project_root,
        session_id=args.session_id,
        session_started_at=args.session_started_at,
        model_name=args.model,
        baml_provider=args.baml_provider,
        api_base_url=args.api_base_url,
        api_key=os.environ.get(api_key_env) if api_key_env else None,
        temperature=args.temperature,
        ollama_base_url=args.ollama_base_url,
        max_llm_calls=args.max_llm_calls,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
