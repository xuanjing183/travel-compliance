# Travel Compliance Desk — Hugging Face Spaces (Docker SDK)
# Public demo deployment. Secrets are injected by HF Space Secrets at runtime
# (HC-07: the app only reads the process environment) — never baked into the image.
FROM python:3.11-slim

# HF Spaces runs containers without root privileges; use a regular user.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user
ENV PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

# Project metadata + source. Editable install keeps the package at ./src so the
# app's REPO_ROOT-relative defaults (tests/fixtures policies, var/, artifacts/) resolve.
COPY --chown=user pyproject.toml README.md ./
COPY --chown=user src ./src
RUN pip install --user --no-cache-dir -e .

# Demo policy documents / provider fixtures / recorded LLM fixtures.
COPY --chown=user tests/fixtures ./tests/fixtures

# Preloaded demo database (trips, approvals, workflow checkpoints).
COPY --chown=user var/ ./var/

# Writable runtime dirs: SQLite DB, uploads, checkpoints, guard/usage logs.
RUN mkdir -p var/uploads artifacts

# Non-secret runtime config. Space variables/secrets (runtime env) override these.
ENV TRAVEL_DESK_HOST=0.0.0.0
ENV TRAVEL_DESK_PORT=7860
ENV TRAVEL_DESK_LLM_MODE=real
ENV TRAVEL_DESK_PROVIDER_MODE=demo
ENV TRAVEL_DESK_RETRIEVAL=vector

EXPOSE 7860

CMD ["travel-desk"]
