FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    SITE_DATA_PATH=/app/data/site.json \
    DATABASE_PATH=/app/runtime/dlut_cpc.sqlite3

COPY app.py /app/app.py
COPY database.py /app/database.py
COPY official_imports.py /app/official_imports.py
COPY schools.py /app/schools.py
COPY admin_auth.py /app/admin_auth.py
COPY tools /app/tools
COPY web /app/web
COPY data /app/data

RUN mkdir -p /app/runtime

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).read()"]

CMD ["python", "app.py"]
