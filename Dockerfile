FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only PyTorch first (~200 MB vs 530 MB for the CUDA wheel).
# sentence-transformers and transformers will reuse this install.
# To use a GPU build instead, remove this RUN step and let pip resolve automatically.
RUN pip install --no-cache-dir \
    torch \
    --extra-index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
ENV PYTHONPATH=/app/src

EXPOSE 8080
CMD ["uvicorn", "overwatch.main:app", "--host", "0.0.0.0", "--port", "8080"]
