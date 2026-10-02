# propbot on the NAS. The project folder is bind-mounted at /app and `python -m` runs from there,
# so config.yaml, rules, prompts and code edits apply on restart; the install below only brings deps.
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /root/.npm
WORKDIR /app
COPY pyproject.toml ./
COPY propbot ./propbot
RUN pip install --no-cache-dir . && rm -rf /app/*
# HOME for the claude CLI's own config; data/ is on the NAS and owned by James (uid 1000)
ENV PYTHONUNBUFFERED=1 PROPBOT_HOME=/app HOME=/app/data/home DISABLE_AUTOUPDATER=1
CMD ["python", "-m", "propbot.cli", "serve"]
