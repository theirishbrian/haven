# Haven: local PHI/PII scrubbing proxy for AI prompts.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt \
 && python -m spacy download en_core_web_lg

# Run as an unprivileged user. /data holds the audit log.
RUN useradd --create-home --uid 10001 haven \
 && mkdir /data && chown haven:haven /data

COPY app ./app
COPY scripts ./scripts

USER haven

# Inside the container Haven must listen on all interfaces so Docker can reach
# it. docker-compose.yml publishes the port on 127.0.0.1 only, so it is still
# unreachable from other machines on your network.
ENV HAVEN_HOST=0.0.0.0 \
    HAVEN_PORT=8787 \
    HAVEN_AUDIT_DB_PATH=/data/haven_audit.db

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health', timeout=4)"

CMD ["python", "-m", "app"]
