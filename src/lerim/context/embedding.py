"""Local ONNX embedding provider for Lerim semantic retrieval.

Lerim uses one small local embedding model for semantic record search. The
provider owns model download, tokenizer setup, ONNX session loading, and vector
normalization so the rest of the codebase only deals with query/document
embedding calls.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from huggingface_hub import snapshot_download
from tokenizers import Tokenizer

from lerim.config.settings import get_config

DEFAULT_EMBEDDING_MODEL_NAME = "mixedbread-ai/mxbai-embed-xsmall-v1"
DEFAULT_EMBEDDING_DIMS = 384
DEFAULT_ONNX_FILE = "onnx/model_quantized.onnx"
MODEL_FILE_PATTERNS = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    DEFAULT_ONNX_FILE,
)


def _safe_model_dir_name(model_id: str) -> str:
    """Return a filesystem-safe cache directory name for one model id."""
    return str(model_id).replace("/", "--").replace(":", "--")


def _normalize(vector: np.ndarray) -> np.ndarray:
    """Return one L2-normalized float32 vector."""
    array = np.asarray(vector, dtype=np.float32)
    norm = np.linalg.norm(array)
    if norm <= 0:
        return array.astype(np.float32, copy=False)
    return (array / norm).astype(np.float32, copy=False)


class EmbeddingProvider:
    """Small local embedding provider backed by ONNX Runtime."""

    def __init__(self, *, model_id: str, cache_dir: Path, onnx_file: str = DEFAULT_ONNX_FILE) -> None:
        self.model_id = str(model_id).strip() or DEFAULT_EMBEDDING_MODEL_NAME
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.onnx_file = onnx_file
        self.model_dir = self.cache_dir / _safe_model_dir_name(self.model_id)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self._tokenizer: Tokenizer | None = None
        self._session: ort.InferenceSession | None = None
        self._embedding_dims: int | None = None
        self._tokenizer_config: dict[str, Any] | None = None

    @property
    def embedding_dims(self) -> int:
        """Return model embedding size from local config or downloaded config."""
        if self._embedding_dims is not None:
            return self._embedding_dims

        config_path = self._download_model_files(allow_patterns=("config.json",)) / "config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"failed_to_read_embedding_config:{self.model_id}:{config_path}"
            ) from exc
        hidden_size = int(config.get("hidden_size") or 0)
        if hidden_size <= 0:
            raise RuntimeError(f"invalid_embedding_dims:{self.model_id}")
        self._embedding_dims = hidden_size
        return hidden_size

    def embed_query(self, query_text: str) -> list[float]:
        """Embed one search query into a normalized vector."""
        return self._embed_texts([str(query_text or "")])[0]

    def embed_document(self, document_text: str) -> list[float]:
        """Embed one record document into a normalized vector."""
        return self._embed_texts([str(document_text or "")])[0]

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed many texts with ONNX Runtime and return normalized vectors."""
        if not texts:
            return []
        tokenizer = self._ensure_tokenizer()
        session = self._ensure_session()
        encodings = tokenizer.encode_batch(texts)
        input_ids = np.asarray([encoding.ids for encoding in encodings], dtype=np.int64)
        attention_mask = np.asarray([encoding.attention_mask for encoding in encodings], dtype=np.int64)
        outputs = session.run(
            ["sentence_embedding"],
            {"input_ids": input_ids, "attention_mask": attention_mask},
        )[0]
        rows = np.asarray(outputs, dtype=np.float32)
        return [_normalize(row).tolist() for row in rows]

    def _ensure_tokenizer(self) -> Tokenizer:
        """Load and configure the tokenizer once."""
        if self._tokenizer is not None:
            return self._tokenizer

        model_dir = self._download_model_files(
            allow_patterns=(
                "tokenizer.json",
                "tokenizer_config.json",
                "special_tokens_map.json",
            )
        )
        tokenizer_path = model_dir / "tokenizer.json"
        if not tokenizer_path.exists():
            raise RuntimeError(f"missing_embedding_tokenizer:{self.model_id}")
        try:
            tokenizer = Tokenizer.from_file(str(tokenizer_path))
        except Exception as exc:  # pragma: no cover - defensive parse guard
            raise RuntimeError(f"failed_to_load_embedding_tokenizer:{self.model_id}") from exc

        config = self._load_tokenizer_config(model_dir)
        pad_token = str(config.get("pad_token") or "[PAD]")
        pad_token_id = int(config.get("pad_token_id") or 0)
        max_length = int(config.get("model_max_length") or config.get("max_length") or 512)
        if max_length <= 0:
            max_length = 512
        tokenizer.enable_truncation(max_length=max_length)
        tokenizer.enable_padding(pad_id=pad_token_id, pad_token=pad_token)
        self._tokenizer = tokenizer
        return tokenizer

    def _ensure_session(self) -> ort.InferenceSession:
        """Load the ONNX embedding session once."""
        if self._session is not None:
            return self._session

        model_dir = self._download_model_files(allow_patterns=MODEL_FILE_PATTERNS)
        model_path = model_dir / self.onnx_file
        if not model_path.exists():
            raise RuntimeError(f"missing_embedding_onnx:{self.model_id}:{self.onnx_file}")

        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            session = ort.InferenceSession(
                str(model_path),
                sess_options=session_options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:  # pragma: no cover - provider failure path
            raise RuntimeError(f"failed_to_load_embedding_model:{self.model_id}") from exc

        output_names = {output.name for output in session.get_outputs()}
        if "sentence_embedding" not in output_names:
            raise RuntimeError(f"embedding_model_missing_sentence_embedding:{self.model_id}")
        self._session = session
        return session

    def _load_tokenizer_config(self, model_dir: Path) -> dict[str, Any]:
        """Read tokenizer config once and cache it."""
        if self._tokenizer_config is not None:
            return self._tokenizer_config
        config_path = model_dir / "tokenizer_config.json"
        if not config_path.exists():
            self._tokenizer_config = {}
            return self._tokenizer_config
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        self._tokenizer_config = payload if isinstance(payload, dict) else {}
        return self._tokenizer_config

    def _download_model_files(self, *, allow_patterns: tuple[str, ...]) -> Path:
        """Ensure the required model files are present in the local cache."""
        try:
            snapshot_download(
                repo_id=self.model_id,
                allow_patterns=list(allow_patterns),
                local_dir=self.model_dir,
            )
        except Exception as exc:  # pragma: no cover - network/cache failure path
            raise RuntimeError(f"failed_to_download_embedding_model:{self.model_id}") from exc
        return self.model_dir


@lru_cache(maxsize=8)
def build_embedding_provider(model_id: str, cache_dir: str) -> EmbeddingProvider:
    """Build and cache an embedding provider for one model/cache pair."""
    return EmbeddingProvider(model_id=model_id, cache_dir=Path(cache_dir))


def get_embedding_provider() -> EmbeddingProvider:
    """Return the configured embedding provider."""
    config = get_config()
    return build_embedding_provider(config.embedding_model_id, str(config.embedding_cache_dir))


def clear_embedding_provider_cache() -> None:
    """Clear the embedding provider cache for tests or config resets."""
    build_embedding_provider.cache_clear()


EMBEDDING_MODEL_NAME = DEFAULT_EMBEDDING_MODEL_NAME
EMBEDDING_DIMS = DEFAULT_EMBEDDING_DIMS


if __name__ == "__main__":
    """Run a small real-provider smoke check."""
    provider = EmbeddingProvider(
        model_id=DEFAULT_EMBEDDING_MODEL_NAME,
        cache_dir=Path("/tmp/lerim-embedding-smoke"),
    )
    a = provider.embed_query("replace generic write api")
    b = provider.embed_document("title: explicit write tools\nbody: replace generic write api with explicit tools")
    c = provider.embed_document("title: tomato planting\nbody: how to plant tomatoes in spring")
    assert len(a) == DEFAULT_EMBEDDING_DIMS
    assert len(b) == DEFAULT_EMBEDDING_DIMS
    assert float(np.dot(np.asarray(a), np.asarray(b))) > float(np.dot(np.asarray(a), np.asarray(c)))
    print("embedding provider: self-test passed")
