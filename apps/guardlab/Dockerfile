FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then PIP_CERT=/run/secrets/proxy_ca pip install --no-cache-dir -r requirements.txt; else pip install --no-cache-dir -r requirements.txt; fi
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app && mkdir /data && chown app:app /data
COPY --chown=app:app main.py database.py models.py ./
COPY --chown=app:app templates ./templates
COPY --chown=app:app static ./static
USER app
ENV APP_ENV=production DATABASE_URL=sqlite:////data/security.db PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/healthz', timeout=3)"
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
