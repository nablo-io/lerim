"""Extended tests for embedding provider — helper functions, caching, and init."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from lerim.context.embedding import (
    DEFAULT_EMBEDDING_MODEL_NAME,
    EmbeddingProvider,
    _normalize,
    _safe_model_dir_name,
    build_embedding_provider,
    clear_embedding_provider_cache,
    get_embedding_provider,
)


class TestSafeModelDirName:
    """Tests for _safe_model_dir_name."""

    def test_replaces_slash(self):
        assert _safe_model_dir_name("org/model") == "org--model"

    def test_replaces_colon(self):
        assert _safe_model_dir_name("host:port/model") == "host--port--model"

    def test_replaces_both(self):
        assert _safe_model_dir_name("a/b:c") == "a--b--c"

    def test_no_special_chars(self):
        assert _safe_model_dir_name("simple-name") == "simple-name"

    def test_empty_string(self):
        assert _safe_model_dir_name("") == ""


class TestNormalize:
    """Tests for _normalize."""

    def test_l2_normalization(self):
        vec = np.array([3.0, 4.0])
        result = _normalize(vec)
        expected = np.array([0.6, 0.8], dtype=np.float32)
        np.testing.assert_allclose(result, expected, atol=1e-6)

    def test_zero_vector(self):
        vec = np.zeros(5)
        result = _normalize(vec)
        np.testing.assert_array_equal(result, np.zeros(5, dtype=np.float32))

    def test_already_normalized(self):
        vec = np.array([1.0, 0.0, 0.0])
        result = _normalize(vec)
        expected = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        np.testing.assert_allclose(result, expected, atol=1e-6)

    def test_returns_float32(self):
        vec = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        result = _normalize(vec)
        assert result.dtype == np.float32

    def test_unit_length(self):
        vec = np.array([1.0, 2.0, 3.0, 4.0])
        result = _normalize(vec)
        norm = np.linalg.norm(result)
        np.testing.assert_allclose(norm, 1.0, atol=1e-6)


class TestEmbeddingProviderInit:
    """Tests for EmbeddingProvider.__init__."""

    def test_stores_model_id(self, tmp_path):
        provider = EmbeddingProvider(model_id="test/model", cache_dir=tmp_path)
        assert provider.model_id == "test/model"

    def test_stores_cache_dir(self, tmp_path):
        provider = EmbeddingProvider(model_id="test/model", cache_dir=tmp_path)
        assert provider.cache_dir == tmp_path.resolve()

    def test_default_model_id_on_empty(self, tmp_path):
        provider = EmbeddingProvider(model_id="", cache_dir=tmp_path)
        assert provider.model_id == DEFAULT_EMBEDDING_MODEL_NAME

    def test_default_model_id_on_none(self, tmp_path):
        provider = EmbeddingProvider(model_id=None, cache_dir=tmp_path)
        assert provider.model_id == DEFAULT_EMBEDDING_MODEL_NAME

    def test_creates_model_dir(self, tmp_path):
        provider = EmbeddingProvider(model_id="org/model", cache_dir=tmp_path)
        expected = tmp_path / "org--model"
        assert provider.model_dir == expected
        assert expected.exists()

    def test_permission_error_is_wrapped(self, tmp_path, monkeypatch):
        def fail_mkdir(self, *_args, **_kwargs):
            if self == tmp_path / "cache":
                raise PermissionError("denied")
            return None

        monkeypatch.setattr(Path, "mkdir", fail_mkdir)
        with pytest.raises(RuntimeError, match="embedding_cache_dir_not_writable"):
            EmbeddingProvider(model_id="test/model", cache_dir=tmp_path / "cache")

    def test_close_releases_cached_runtime_objects(self, tmp_path):
        provider = EmbeddingProvider(model_id="test/model", cache_dir=tmp_path)
        provider._tokenizer = MagicMock()
        provider._session = MagicMock()
        provider.close()
        assert provider._tokenizer is None
        assert provider._session is None

    def test_context_manager_closes_provider(self, tmp_path):
        with EmbeddingProvider(model_id="test/model", cache_dir=tmp_path) as provider:
            provider._tokenizer = MagicMock()
            provider._session = MagicMock()
        assert provider._tokenizer is None
        assert provider._session is None


class TestEmbeddingProviderDimsProperty:
    """Tests for EmbeddingProvider.embedding_dims."""

    def test_reads_from_config(self, tmp_path, monkeypatch):
        provider = EmbeddingProvider(model_id="test/model", cache_dir=tmp_path)
        config_dir = tmp_path / "test--model"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.json").write_text(
            json.dumps({"hidden_size": 256}), encoding="utf-8"
        )
        monkeypatch.setattr(provider, "_download_model_files", lambda **_kw: config_dir)
        assert provider.embedding_dims == 256

    def test_caches_dims(self, tmp_path, monkeypatch):
        provider = EmbeddingProvider(model_id="test/model", cache_dir=tmp_path)
        config_dir = tmp_path / "test--model"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.json").write_text(
            json.dumps({"hidden_size": 128}), encoding="utf-8"
        )
        monkeypatch.setattr(provider, "_download_model_files", lambda **_kw: config_dir)
        _ = provider.embedding_dims
        provider._embedding_dims = 999
        assert provider.embedding_dims == 999


class TestBuildEmbeddingProviderCaching:
    """Tests for build_embedding_provider LRU cache."""

    def setup_method(self):
        clear_embedding_provider_cache()

    def teardown_method(self):
        clear_embedding_provider_cache()

    def test_returns_same_instance(self, tmp_path):
        p1 = build_embedding_provider("test/model", str(tmp_path / "cache"))
        p2 = build_embedding_provider("test/model", str(tmp_path / "cache"))
        assert p1 is p2

    def test_different_args_different_instance(self, tmp_path):
        p1 = build_embedding_provider("model/a", str(tmp_path / "a"))
        p2 = build_embedding_provider("model/b", str(tmp_path / "b"))
        assert p1 is not p2

    def test_cache_clear(self, tmp_path):
        p1 = build_embedding_provider("test/model", str(tmp_path / "cache"))
        clear_embedding_provider_cache()
        p2 = build_embedding_provider("test/model", str(tmp_path / "cache"))
        assert p1 is not p2


class TestGetEmbeddingProvider:
    """Tests for get_embedding_provider."""

    def setup_method(self):
        clear_embedding_provider_cache()

    def teardown_method(self):
        clear_embedding_provider_cache()

    def test_returns_provider(self, tmp_path, monkeypatch):
        mock_config = MagicMock()
        mock_config.embedding_model_id = "test/model"
        mock_config.embedding_cache_dir = tmp_path / "cache"
        monkeypatch.setattr("lerim.context.embedding.get_config", lambda: mock_config)
        provider = get_embedding_provider()
        assert isinstance(provider, EmbeddingProvider)
        assert provider.model_id == "test/model"
