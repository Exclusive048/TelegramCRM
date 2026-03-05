FROM python:3.12-slim

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# healthcheck нужен ТОЛЬКО для api-сервиса, но пусть будет в образе — compose использует его на api
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# CMD в compose задаётся отдельно для api/crm-bot/master-bot
CMD ["python", "-c", "print('Use docker-compose command to start a specific entrypoint')"]