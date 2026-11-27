FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPORTSRANK_DB=/app/data/sportsrank.sqlite3 \
    SPORTSRANK_ASSETS=/app/assets \
    SPORTSRANK_DB_IMMUTABLE=1 \
    PORT=8080

WORKDIR /app

# Only a tiny WSGI server is needed at runtime; web/ uses Python stdlib only.
RUN pip install --no-cache-dir gunicorn==23.0.0

COPY web/ /app/web/
COPY website/cfb/assets/ /app/assets/cfb/assets/
COPY website/cbb/assets/ /app/assets/cbb/assets/
COPY .sportsrank/web/sportsrank.sqlite3 /app/data/sportsrank.sqlite3

EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --threads 4 --timeout 30 'web.app:from_env()'"]
