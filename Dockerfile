# Container image for the NL-Calculator Slack bot (HTTP Events mode) plus its
# 2-minute health monitor. Cloudflare Containers needs linux/amd64.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app

# Dependencies first so this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code only. No secrets: the Worker passes them in at start-up.
COPY . .
RUN chmod 0755 /app/docker-entrypoint.sh \
    && useradd --system --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

EXPOSE 8080
ENTRYPOINT ["/app/docker-entrypoint.sh"]
