FROM python:3.12-slim AS base
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

# CPU-only torch, per docs/EBIE-IMPLEMENTATION-ANSWERS.md Q2.2 ("Do not
# add GPU infrastructure initially"). Installed from PyTorch's own CPU
# wheel index BEFORE the package's own dependency resolution runs, so
# pip never reaches for the much larger default CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY libs/infusion-models/ /app/libs/infusion-models/
COPY libs/infusion-streams/ /app/libs/infusion-streams/
COPY libs/infusion-common/ /app/libs/infusion-common/
RUN pip install --no-cache-dir /app/libs/infusion-models /app/libs/infusion-streams /app/libs/infusion-common
COPY services/sentiment-engine/ /app/services/sentiment-engine/
RUN pip install --no-cache-dir /app/services/sentiment-engine/
RUN useradd --create-home appuser
# Pre-create the HF cache dir owned by appuser -- Docker initializes a
# named volume's contents/ownership from whatever already exists at
# that path in the image at first mount, so this avoids a real
# PermissionError (found and fixed via live verification) where the
# volume would otherwise mount in root-owned and the non-root appuser
# process couldn't write the downloaded model into it.
RUN mkdir -p /home/appuser/.cache/huggingface && chown -R appuser:appuser /home/appuser/.cache
USER appuser
CMD ["python", "-m", "sentiment_engine.main"]
