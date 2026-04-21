# llama.cpp setup (Gemma 4 GGUF)

## Model files

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install huggingface_hub

mkdir -p factorio-models/gemma4-e2b
cd factorio-models/gemma4-e2b

# Download model + mmproj (vision projector - REQUIRED)
hf download unsloth/gemma-4-E2B-it-GGUF   "gemma-4-E2B-it-Q4_K_M.gguf" "mmproj-BF16.gguf" "config.json" --local-dir .

docker pull ghcr.io/ggml-org/llama.cpp:server-cuda
```

## Docker + Overwatch API (`VLLM_BASE_URL`)

The **`overwatch-api`** container must reach the llama.cpp HTTP server **by a hostname that resolves inside that container**. These are wrong:

- **`http://localhost:9000/...`** — `localhost` is the API container itself, not your machine. You will see connection refused to `127.0.0.1:9000` in logs.
- **`http://localhost:9000/`** without **`/v1`** — Overwatch expects OpenAI-compatible roots ending in `/v1` (same as for vLLM; see `chat_completions_url` in `vllm_client.py`).

Use one of these:

### 1. Same Compose project (recommended)

Start both stacks together so `overwatch-api` and `overwatch-vlm` share a Docker network:

```bash
docker compose -f compose.yml -f llama-cpp.yml up -d
```

Set:

```bash
export VLLM_BASE_URL=http://overwatch-vlm:9000/v1
```

`overwatch-vlm` is the service name from [`llama-cpp.yml`](../llama-cpp.yml).

### 2. LLM on the host, API in Docker

If llama.cpp is bound to the host on port `9000` (`ports: "9000:9000"`) and Overwatch runs in Compose, use the host gateway (supported by [`compose.yml`](../compose.yml) via `extra_hosts`):

```bash
export VLLM_BASE_URL=http://host.docker.internal:9000/v1
```

### 3. API and LLM both on the host (no Docker for API)

Then `http://localhost:9000/v1` is correct.

## LiteLLM / ADK job agents

Job agents use LiteLLM with `openai/<VLLM_MODEL>`; they reuse **`VLLM_BASE_URL`**. If the URL is wrong for Docker, the same connection errors appear for synthesis and other orchestrator agents.
