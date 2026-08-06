FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY libs/infusion-models/ /app/libs/infusion-models/
COPY libs/infusion-streams/ /app/libs/infusion-streams/
COPY libs/infusion-common/ /app/libs/infusion-common/

RUN pip install --no-cache-dir \
    /app/libs/infusion-models \
    /app/libs/infusion-streams \
    /app/libs/infusion-common

COPY services/scanner/ /app/services/scanner/
RUN pip install --no-cache-dir /app/services/scanner/

RUN useradd --create-home appuser
USER appuser

CMD ["python", "-m", "scanner.main"]
