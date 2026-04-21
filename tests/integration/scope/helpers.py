"""Helpers for multi-project scope integration cases."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from lerim.config.settings import Config
from lerim.context import ContextStore, ProjectIdentity, resolve_project_identity
from tests.conftest import SCOPE_EXPECTATIONS_DIR
from tests.integration.common_helpers import load_yaml_expectation
from tests.integration.common_helpers import seed_session


@dataclass
class ScopeCaseEnv:
    """Isolated multi-project test environment for scope integration cases."""

    config: Config
    store: ContextStore
    project_a_name: str
    project_b_name: str
    project_a_root: Path
    project_b_root: Path
    identity_a: ProjectIdentity
    identity_b: ProjectIdentity

    @property
    def all_project_ids(self) -> list[str]:
        """Return both registered project IDs in stable order."""
        return [self.identity_a.project_id, self.identity_b.project_id]


def load_scope_expectation(case_name: str) -> dict[str, object]:
    """Load one YAML expectation file for a scope case."""
    return load_yaml_expectation(SCOPE_EXPECTATIONS_DIR, case_name)


def build_scope_case_env(*, live_config: Config, tmp_path: Path) -> ScopeCaseEnv:
    """Create one isolated config and store with two registered project roots."""
    project_a_name = "alpha"
    project_b_name = "beta"
    project_a_root = tmp_path / project_a_name
    project_b_root = tmp_path / project_b_name
    project_a_root.mkdir(parents=True, exist_ok=True)
    project_b_root.mkdir(parents=True, exist_ok=True)

    config = replace(
        live_config,
        projects={
            project_a_name: str(project_a_root),
            project_b_name: str(project_b_root),
        },
    )
    store = ContextStore(config.context_db_path)
    store.initialize()

    identity_a = resolve_project_identity(project_a_root)
    identity_b = resolve_project_identity(project_b_root)
    store.register_project(identity_a)
    store.register_project(identity_b)

    return ScopeCaseEnv(
        config=config,
        store=store,
        project_a_name=project_a_name,
        project_b_name=project_b_name,
        project_a_root=project_a_root,
        project_b_root=project_b_root,
        identity_a=identity_a,
        identity_b=identity_b,
    )


def seed_scope_session(
    env: ScopeCaseEnv,
    *,
    project_identity: ProjectIdentity,
    session_id: str,
    repo_root: Path,
    agent_type: str = "integration-scope",
) -> None:
    """Insert one canonical session row before seeding or mutating records."""
    seed_session(
        env.store,
        project_id=project_identity.project_id,
        session_id=session_id,
        agent_type=agent_type,
        source_trace_ref="integration-scope",
        repo_root=repo_root,
        model_name="integration-scope",
    )


def seed_scope_record(
    env: ScopeCaseEnv,
    *,
    project_identity: ProjectIdentity,
    session_id: str,
    kind: str,
    title: str,
    body: str,
    decision: str | None = None,
    why: str | None = None,
    user_intent: str | None = None,
    what_happened: str | None = None,
    outcomes: str | None = None,
) -> dict[str, str]:
    """Create one seeded record inside the chosen project scope."""
    payload = env.store.create_record(
        project_id=project_identity.project_id,
        session_id=session_id,
        kind=kind,
        title=title,
        body=body,
        decision=decision,
        why=why,
        user_intent=user_intent,
        what_happened=what_happened,
        outcomes=outcomes,
    )
    return {
        "record_id": str(payload["record_id"]),
        "project_id": project_identity.project_id,
    }
