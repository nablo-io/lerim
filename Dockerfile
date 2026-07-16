# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/nablo-io/lerim

# Install curl (healthcheck) and ripgrep
RUN apt-get update && apt-get install -y --no-install-recommends curl ripgrep && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /uvx /bin/

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

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

ENTRYPOINT ["lerim", "serve"]
