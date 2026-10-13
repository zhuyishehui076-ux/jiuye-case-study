FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.lock .
RUN python -m pip install --require-hashes -r requirements.lock

COPY . .

FROM base AS test
RUN python -m pip install --require-hashes -r requirements-dev.lock
CMD ["python", "-m", "pytest", "--require-pg", "-v", "-ra", "--tb=short", "--basetemp=/tmp/jiuye-case-pytest", "--junitxml=/validation/backend-all.xml"]

FROM base AS runtime
EXPOSE 8000
ENTRYPOINT ["sh", "docker-entrypoint.sh"]
