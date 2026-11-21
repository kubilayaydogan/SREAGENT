# airlab SRE Agent — salt-okur; tek canlı veri kaynağı Dynatrace Remote MCP. DB sürücüsü/istemcisi içermez.
FROM python:3.13-slim
RUN useradd --system --create-home --home /home/agent agent && mkdir -p /data && chown agent /data
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
COPY tests ./tests
COPY eval ./eval
USER agent
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 TZ=UTC AGENT_DATA_DIR=/data
VOLUME ["/data"]
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s \
  CMD python -c "import sqlite3,os,sys; sys.exit(0 if os.path.exists('/data/state.sqlite') else 1)"
ENTRYPOINT ["sre-agent"]
CMD ["watch"]
