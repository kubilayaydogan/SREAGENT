# SRE Agent Management Platform backend (FastAPI API + sre-worker aynı imajdan). Build bağlamı: repo kökü
# (mevcut ajan çekirdeği sre-agent/ DEĞİŞTİRİLMEDEN paket olarak kurulur).
#   docker build -f sre-platform/backend/Dockerfile -t airlab/sre-platform-backend .
FROM python:3.13-slim
RUN useradd --system --create-home --home /home/sre sre && mkdir -p /data && chown sre /data
WORKDIR /app
COPY sre-agent/requirements.lock /app/agent-requirements.lock
COPY sre-platform/backend/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r agent-requirements.lock -r requirements.lock
COPY sre-agent/pyproject.toml sre-agent/README.md /src/sre-agent/
COPY sre-agent/src /src/sre-agent/src
COPY sre-platform/backend/pyproject.toml /src/sre-platform/
COPY sre-platform/backend/sre_platform /src/sre-platform/sre_platform
RUN pip install --no-cache-dir --no-deps /src/sre-agent /src/sre-platform && rm -rf /src
USER sre
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 TZ=UTC AGENT_DATA_DIR=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["sre-platform"]
CMD ["serve"]
