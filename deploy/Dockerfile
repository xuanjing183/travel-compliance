# Travel Compliance Desk — CentOS Docker Deployment
# Based on the HF Spaces Dockerfile, adapted for production CentOS deployment.
FROM python:3.11-slim

# Create a non-root user for security
RUN useradd -m -u 1000 appuser
ENV HOME=/home/appuser
ENV PATH=/home/appuser/.local/bin:$PATH

WORKDIR /home/appuser/app

# 1. Install build tools for jieba (requires C compiler)
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

# 2. Install dependencies (leverage Docker layer cache)
# Using Tsinghua mirror for China network (HC-06)
COPY --chown=appuser pyproject.toml README.md ./
COPY --chown=appuser src ./src
RUN pip install --user --no-cache-dir -e . -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. Copy policy documents / provider fixtures / recorded LLM fixtures
COPY --chown=appuser tests/fixtures ./tests/fixtures

# 3. Copy preloaded demo database
COPY --chown=appuser var/ ./var/

# 4. Create writable runtime directories and initialize cost ledger (owned by appuser)
RUN mkdir -p var/uploads artifacts && \
    echo '<!-- manual_total_cny: 0.00 -->' > artifacts/cost-ledger.md && \
    chown -R appuser:appuser var artifacts

# 5. Switch to non-root user
USER appuser

# Runtime config defaults (overridden by docker-compose env_file)
ENV TRAVEL_DESK_HOST=0.0.0.0
ENV TRAVEL_DESK_PORT=8000
ENV TRAVEL_DESK_LLM_MODE=real
ENV TRAVEL_DESK_PROVIDER_MODE=live
ENV TRAVEL_DESK_RETRIEVAL=vector

EXPOSE 8000

# Health check: verify the app responds (using Python stdlib to avoid dependency issues)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["travel-desk"]
