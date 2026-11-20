FROM python:3.13-slim
RUN useradd --system --home /app collector && mkdir -p /data && chown collector /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py .
USER collector
ENV PYTHONUNBUFFERED=1 TZ=UTC
ENTRYPOINT ["python", "-I", "/app/collector.py"]
