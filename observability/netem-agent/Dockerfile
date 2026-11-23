FROM python:3.13-slim
# iproute2 (tc) imaj build sırasında kurulur; çalışma anında paket kurulumu yok.
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY netem_agent.py .
ENV PYTHONUNBUFFERED=1 TZ=UTC
# tc, CAP_NET_ADMIN ister; compose'da cap_drop: [ALL] + cap_add: [NET_ADMIN] ile yalnız bu yetki verilir.
ENTRYPOINT ["python", "-I", "/app/netem_agent.py"]
