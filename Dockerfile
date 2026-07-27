# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/nablo-io/lerim

# Install curl (healthcheck) and ripgrep
RUN apt-get update && apt-get install -y --no-install-recommends curl ripgrep && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/

# Node runtime for transcript parsing. Lerim normalizes every agent transcript
# with @letta-ai/trajectory, which is a node package, so node >= 20 and npm are
# hard runtime requirements. Copied from the official image because Debian's
# `nodejs` package is still on 18.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    node --version && npm --version

WORKDIR /build

# Install third-party dependencies before copying source so source edits do not
# invalidate the expensive dependency layer.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-emit-project --no-hashes \
      --format requirements.txt --output-file /tmp/requirements.txt && \
    uv pip install --system --requirements /tmp/requirements.txt

# Install Lerim itself from the package surface only. Keep docs, private launch
# material, benchmarks, specs, tests, and local configs out of the image.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-deps .

# Bake the pinned trajectory normalizer into the image so a container can parse
# transcripts on first ingest without reaching npm. The version pin lives in
# lerim.adapters.trajectory_bridge, so it is not repeated here. `lerim up`
# bind-mounts the host data dir over this path; its own preflight installs the
# same pinned package there, so both entry paths start with a warm bridge.
RUN python -c "from lerim.adapters.trajectory_bridge import ensure_trajectory_installed; print(ensure_trajectory_installed())"

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

ENTRYPOINT ["lerim", "serve"]
