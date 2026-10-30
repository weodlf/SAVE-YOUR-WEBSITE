FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8080
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home esoc && mkdir /data && chown esoc:esoc /data
COPY --chown=esoc:esoc app.py engine.py models.py ./
COPY --chown=esoc:esoc templates ./templates
COPY --chown=esoc:esoc static ./static
ENV DATABASE_URL=sqlite:////data/esoc.db COOKIE_SECURE=1 FIREWALL_ENABLED=0 APP_ENV=production
USER esoc
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz', timeout=3)"
CMD ["python", "app.py"]
