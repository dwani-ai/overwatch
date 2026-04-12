# Overwatch — Agentic Video Analytics

Autonomous agents analyse video using **Google Gemma 4 E4B-it** (multimodal), with **FastAPI**, **Docker**, and (planned) **Google ADK** and **A2A**.

## Goals

- Main events observed  
- Security issues  
- Logistics / item tracking (**SAM 3.1 later**)  
- **Attendance: anonymous counts only** (no identity)  

All processed video runs are logged with **timestamp + frame index + event payload**.

## What is implemented (v0)

- **Overwatch API** (`/v1/health`, `/v1/jobs`, `/v1/jobs/{id}/events`, `POST /v1/jobs`)  
- **SQLite** job + event store under `DATA_DIR`  
- **Folder ingest**: poll `INGEST_DIR` for new video files; stable-write detection (`INGEST_STABLE_SEC`)  
- **Worker**: `ffprobe` metadata, chunk plan (~60s windows), then **remote vLLM**:
  - `GET …/models` (optional probe)
  - **`POST …/chat/completions`** — phase 1 uses a **text-only** summary of probe + chunk plan (multimodal clips next)  
- **Docker Compose**: **`overwatch-api` only** by default, calling **`https://vllm-video-api.dwani.ai/ai/v1`**. Local GPU vLLM is optional (`--profile local-vllm`).

## Remote vLLM (phase 1)

`VLLM_BASE_URL` must be the **OpenAI-compatible prefix** immediately before `/chat/completions`.

Default: **`https://vllm-video-api.dwani.ai/ai/v1`** → requests go to  
`https://vllm-video-api.dwani.ai/ai/v1/chat/completions` and `…/models`.

If your gateway uses a different layout, override `VLLM_BASE_URL` accordingly.

Set **`VLLM_API_KEY`** if the hosted API requires a bearer token.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATA_DIR=./data/overwatch INGEST_DIR=./data/ingest
export VLLM_BASE_URL=https://vllm-video-api.dwani.ai/ai/v1
export VLLM_MODEL=gemma4
# export VLLM_API_KEY=...   # if required
PYTHONPATH=src uvicorn overwatch.main:app --reload --port 8080
```

Drop files into `data/ingest/` (e.g. `.mp4`). After they are stable for `INGEST_STABLE_SEC`, a job is enqueued.

## Run with Docker Compose

```bash
export VLLM_API_KEY=...   # if required by the hosted API
export VLLM_MODEL=gemma4  # served model name on the remote vLLM
docker compose up --build
```

- Overwatch: `http://localhost:8080/v1/health`  
- Remote vLLM: `https://vllm-video-api.dwani.ai/ai/v1` (no local GPU in default compose)

**Local GPU vLLM** (optional):

```bash
export HF_TOKEN=...
docker compose --profile local-vllm up --build
```

Point Overwatch at the local server with `VLLM_BASE_URL=http://vllm-server:10802/v1` (vLLM OpenAI path is usually under `/v1`).

Mount points: `./data/ingest` → `/data/ingest`, `./data/overwatch` → `/data/overwatch`.

## Tech stack

- Python / FastAPI  
- Docker / Compose  
- Hosted vLLM OpenAI API (chat completions) — Gemma 4 E4B-it on the remote service  
- Google Agent Development Kit (planned)  
- A2A — Agent-to-Agent protocol (planned)  

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `./data/overwatch` | SQLite DB and state |
| `INGEST_DIR` | `./data/ingest` | Watched folder for new videos |
| `INGEST_POLL_INTERVAL_SEC` | `5` | Folder scan interval |
| `INGEST_STABLE_SEC` | `2` | File size+mtime must be unchanged this long |
| `INGEST_EXTENSIONS` | `.mp4,.mkv,...` | Allowed suffixes |
| `VLLM_BASE_URL` | `https://vllm-video-api.dwani.ai/ai/v1` | Prefix before `/chat/completions` |
| `VLLM_MODEL` | `gemma4` | Model id for chat completions |
| `VLLM_API_KEY` | _(unset)_ | `Authorization: Bearer …` if required |
| `VLLM_CHAT_TIMEOUT_SEC` | `120` | HTTP timeout for chat completions |
| `WORKER_POLL_INTERVAL_SEC` | `1` | Worker idle sleep |

Set `VLLM_BASE_URL=` empty to skip all vLLM calls (probe + chat).

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```
