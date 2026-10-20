FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SCHEMAS_DIR=/app/schemas

WORKDIR /app

COPY app/pyproject.toml /app/pyproject.toml
COPY app/src /app/src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 ingest

COPY schemas/ /app/schemas/

USER ingest

ENTRYPOINT ["python", "-m", "ingest"]
CMD ["run"]
