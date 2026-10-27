FROM python:3.14.4-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# CI uses the same reviewed, hash-locked files and wheel-only installation.
COPY requirements-bootstrap.txt requirements.txt ./
RUN python -m pip install --require-hashes --only-binary=:all: -r requirements-bootstrap.txt \
    && python -m pip install --require-hashes --only-binary=:all: -r requirements.txt \
    && python -m pip check

RUN groupadd --system app && useradd --system --gid app --home-dir /app app
COPY --chown=app:app app/ ./app/
COPY --chown=app:app alembic/ ./alembic/
COPY --chown=app:app alembic.ini ./alembic.ini

USER app
EXPOSE 8000

# Migrations are a separate, explicitly guarded operation.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
