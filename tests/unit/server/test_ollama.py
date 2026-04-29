"""Unit tests for the Ollama model load/unload lifecycle manager.

Tests verify that the context manager correctly loads models on enter,
unloads on exit, handles unreachable servers gracefully, respects
auto_unload config, and deduplicates models across roles.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lerim.config.settings import Config, RoleConfig
from lerim.server.api import (
    _ollama_models,
    ollama_lifecycle,
)
from tests.helpers import make_config


def _make_ollama_config(
    tmp_path: Path,
    *,
    agent_provider: str = "ollama",
    agent_model: str = "qwen3.5:4b-q8_0",
    auto_unload: bool = True,
) -> Config:
    """Build a Config with Ollama agent role for testing."""
    base = make_config(tmp_path)
    return Config(
        global_data_dir=base.global_data_dir,
        sessions_db_path=base.sessions_db_path,
        context_db_path=base.context_db_path,
        platforms_path=base.platforms_path,
        embedding_model_id=base.embedding_model_id,
        embedding_cache_dir=base.embedding_cache_dir,
        semantic_shortlist_size=base.semantic_shortlist_size,
        lexical_shortlist_size=base.lexical_shortlist_size,
        server_host=base.server_host,
        server_port=base.server_port,
        sync_interval_minutes=base.sync_interval_minutes,
        maintain_interval_minutes=base.maintain_interval_minutes,
        agent_role=RoleConfig(
            provider=agent_provider,
            model=agent_model,
        ),
        sync_window_days=7,
        sync_max_sessions=50,
        mlflow_enabled=False,
        anthropic_api_key=None,
        openai_api_key=None,
        zai_api_key=None,
        openrouter_api_key=None,
        minimax_api_key=None,
        opencode_api_key=None,
        provider_api_bases={
            "ollama": "http://127.0.0.1:11434",
        },
        auto_unload=auto_unload,
        agents={},
        projects={},
        cloud_endpoint="https://api.lerim.dev",
        cloud_token=None,
    )


class TestOllamaModels:
    """Tests for _ollama_models() model collection."""

    def test_no_ollama_roles(self, tmp_path: Path) -> None:
        """No-op when no roles use ollama provider."""
        config = make_config(tmp_path)
        assert _ollama_models(config) == []

    def test_ollama_roles_deduped(self, tmp_path: Path) -> None:
        """Same model used by multiple roles appears once."""
        config = _make_ollama_config(tmp_path)
        models = _ollama_models(config)
        assert len(models) == 1
        assert models[0] == ("http://127.0.0.1:11434", "qwen3.5:4b-q8_0")

    def test_single_agent_model(self, tmp_path: Path) -> None:
        """Agent ollama model appears once."""
        config = _make_ollama_config(
            tmp_path,
            agent_model="qwen3.5:9b-q8_0",
        )
        models = _ollama_models(config)
        assert len(models) == 1
        assert models[0] == ("http://127.0.0.1:11434", "qwen3.5:9b-q8_0")

    def test_non_ollama_agent_returns_empty(self, tmp_path: Path) -> None:
        """Non-ollama agent role produces no ollama models."""
        config = _make_ollama_config(
            tmp_path,
            agent_provider="minimax",
            agent_model="MiniMax-M2.5",
        )
        models = _ollama_models(config)
        assert len(models) == 0


class TestOllamaLifecycle:
    """Tests for the ollama_lifecycle context manager."""

    def test_noop_no_ollama(self, tmp_path: Path) -> None:
        """Context manager is a no-op when no ollama roles configured."""
        config = make_config(tmp_path)
        with ollama_lifecycle(config):
            pass  # Should not make any HTTP calls

    @patch("lerim.server.api._unload_model")
    @patch("lerim.server.api._load_model")
    @patch("lerim.server.api._is_ollama_reachable", return_value=True)
    def test_load_and_unload(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Model is loaded on enter and unloaded on exit."""
        config = _make_ollama_config(tmp_path)
        with ollama_lifecycle(config):
            mock_load.assert_called_once_with(
                "http://127.0.0.1:11434", "qwen3.5:4b-q8_0"
            )
            mock_unload.assert_not_called()
        mock_unload.assert_called_once_with("http://127.0.0.1:11434", "qwen3.5:4b-q8_0")

    @patch("lerim.server.api._unload_model")
    @patch("lerim.server.api._load_model")
    @patch("lerim.server.api._is_ollama_reachable", return_value=True)
    def test_auto_unload_false_skips_unload(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When auto_unload=False, model is loaded but not unloaded."""
        config = _make_ollama_config(tmp_path, auto_unload=False)
        with ollama_lifecycle(config):
            pass
        mock_load.assert_called_once()
        mock_unload.assert_not_called()

    @patch("lerim.server.api._unload_model")
    @patch("lerim.server.api._load_model")
    @patch("lerim.server.api._is_ollama_reachable", return_value=False)
    def test_unreachable_skips_gracefully(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Unreachable Ollama is handled gracefully — no crash."""
        config = _make_ollama_config(tmp_path)
        with ollama_lifecycle(config):
            pass
        mock_load.assert_not_called()
        mock_unload.assert_not_called()

    @patch("lerim.server.api._unload_model")
    @patch("lerim.server.api._load_model")
    @patch("lerim.server.api._is_ollama_reachable", return_value=True)
    def test_unload_on_exception(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Model is unloaded even when the inner block raises."""
        config = _make_ollama_config(tmp_path)
        with pytest.raises(ValueError, match="test error"):
            with ollama_lifecycle(config):
                raise ValueError("test error")
        mock_unload.assert_called_once()

    @patch("lerim.server.api._unload_model")
    @patch("lerim.server.api._load_model")
    @patch("lerim.server.api._is_ollama_reachable", return_value=True)
    def test_single_agent_model_lifecycle(
        self,
        mock_reachable: MagicMock,
        mock_load: MagicMock,
        mock_unload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Agent ollama model is loaded and unloaded exactly once."""
        config = _make_ollama_config(
            tmp_path,
            agent_model="qwen3.5:9b-q8_0",
        )
        with ollama_lifecycle(config):
            assert mock_load.call_count == 1
        assert mock_unload.call_count == 1
