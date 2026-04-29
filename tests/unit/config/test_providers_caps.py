"""Tests for the provider capability registry and validation."""

from __future__ import annotations


import pytest

from lerim.config.providers import (
	PROVIDER_CAPABILITIES,
	normalize_model_name,
	validate_provider_for_role,
)


class TestValidateProviderForRole:
	"""Tests for validate_provider_for_role."""

	def test_valid_provider_and_role_passes(self):
		"""Known provider + supported role should not raise."""
		validate_provider_for_role("minimax", "agent")

	def test_unknown_provider_raises_with_supported_list(self):
		"""Unknown provider should raise RuntimeError listing all supported providers."""
		with pytest.raises(RuntimeError, match="Unknown provider 'bogus'") as exc_info:
			validate_provider_for_role("bogus", "agent")
		# The error message must list at least some known providers.
		for name in ("minimax", "openai", "ollama"):
			assert name in str(exc_info.value)

	def test_unsupported_role_raises_with_supported_roles(self):
		"""Provider that exists but doesn't support the role should list its supported roles."""
		with pytest.raises(RuntimeError, match="does not support role 'extract'"):
			validate_provider_for_role("mlx", "extract")


class TestNormalizeModelName:
	"""Tests for normalize_model_name auto-correction."""

	def test_minimax_lowercase_corrected(self):
		"""lowercase minimax-m2.5 with minimax provider -> PascalCase."""
		assert normalize_model_name("minimax", "minimax-m2.5") == "MiniMax-M2.5"

	def test_minimax_correct_casing_unchanged(self):
		"""Already correct PascalCase passes through."""
		assert normalize_model_name("minimax", "MiniMax-M2.5") == "MiniMax-M2.5"

	def test_opencode_go_lowercase_unchanged(self):
		"""opencode_go expects lowercase and gets it back."""
		assert normalize_model_name("opencode_go", "minimax-m2.5") == "minimax-m2.5"

	def test_openrouter_passthrough(self):
		"""Provider without a models list passes through any name."""
		assert normalize_model_name("openrouter", "anything/here") == "anything/here"

	def test_unknown_model_passthrough(self):
		"""Unknown model for a known provider passes through unchanged."""
		assert normalize_model_name("minimax", "custom-model-v3") == "custom-model-v3"

	def test_unknown_provider_passthrough(self):
		"""Completely unknown provider passes through."""
		assert normalize_model_name("bogus", "some-model") == "some-model"

	def test_zai_models_normalized(self):
		"""zai provider models normalize correctly."""
		assert normalize_model_name("zai", "GLM-4.7") == "glm-4.7"
		assert normalize_model_name("zai", "glm-4.5-air") == "glm-4.5-air"


class TestAllProvidersHaveAgentRole:
	"""Every registered provider must support the 'agent' role."""

	@pytest.mark.parametrize("provider", list(PROVIDER_CAPABILITIES.keys()))
	def test_agent_in_roles(self, provider):
		caps = PROVIDER_CAPABILITIES[provider]
		assert "agent" in caps["roles"], f"Provider '{provider}' is missing 'agent' role"
