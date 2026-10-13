# Spashta API and page. Works as a Hugging Face Docker Space (port 7860) or on any container host.
# No secrets are baked in: model keys and the admin token come from the host's secret settings at run time.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 app && mkdir -p /data && chown app /data
WORKDIR /home/app/spashta

COPY --chown=app pyproject.toml README.md ./
COPY --chown=app spashta ./spashta
RUN pip install --no-cache-dir ".[api,extract,nepali]"

USER app
ENV SPASHTA_DB=/tmp/spashta.db \
    SPASHTA_TRUST_PROXY=1 \
    PORT=7860
EXPOSE 7860

# Seed the outlet list (idempotent), then serve.
# --no-access-log: uvicorn's access log records client addresses; Spashta logs requests without them.
CMD spashta --db "$SPASHTA_DB" sources seed && \
    uvicorn spashta.api:app --host 0.0.0.0 --port "$PORT" --proxy-headers --no-access-log --timeout-keep-alive 5
