FROM python:3.12-slim

LABEL org.opencontainers.image.source=https://github.com/lerim-dev/lerim-cli

# Install curl (healthcheck) and ripgrep
RUN apt-get update && apt-get install -y --no-install-recommends curl ripgrep && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Install lerim from local source
COPY . /build
RUN pip install --no-cache-dir --retries 10 --default-timeout 120 /build && rm -rf /build


EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8765/api/health || exit 1

ENTRYPOINT ["lerim", "serve"]
