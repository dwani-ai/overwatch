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
  - `GET …/models`
  - **`POST …/chat/completions`** — short **text** handshake, then **one request per chunk** with an **MP4 data-URI** (`video_url`) + analysis prompt (events, security, logistics, **count-only** attendance)  
- **Docker Compose**: **`overwatch-api` only** by default, calling **`https://some-vllm`**. Local GPU vLLM is optional (`--profile local-vllm`).

## Remote vLLM (phase 1)

`VLLM_BASE_URL` must be the **OpenAI-compatible prefix** immediately before `/chat/completions`.

If your gateway uses a different layout, override `VLLM_BASE_URL` accordingly.

Set **`VLLM_API_KEY`** if the hosted API requires a bearer token.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATA_DIR=./data/overwatch INGEST_DIR=./data/ingest
export VLLM_BASE_URL=some_url
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
- Remote vLLM: `https://some-vllm` (no local GPU in default compose)

**Creating a job from the host (Docker):** paths inside the container are **not** the same as on your laptop. The compose file mounts host `./data/ingest` at **`/data/ingest`** in the container. Use either:

```bash
# Easiest: basename only (resolved under INGEST_DIR in the container)
curl -s -X POST 'http://localhost:8080/v1/jobs' \
  -H 'Content-Type: application/json' \
  -d '{"filename":"warehouse-1.mp4"}'

# Or the full path *as seen inside the container*
curl -s -X POST 'http://localhost:8080/v1/jobs' \
  -H 'Content-Type: application/json' \
  -d '{"source_path":"/data/ingest/warehouse-1.mp4"}'
```

Do **not** pass host paths like `/home/you/.../data/ingest/foo.mp4` — the API will reject them.

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
| `VLLM_BASE_URL` | `https://some-vllm` | Prefix before `/chat/completions` |
| `VLLM_MODEL` | `gemma4` | Model id for chat completions |
| `VLLM_API_KEY` | _(unset)_ | `Authorization: Bearer …` if required |
| `VLLM_CHAT_TIMEOUT_SEC` | `120` | HTTP timeout for text chat completions |
| `VLLM_MULTIMODAL_ENABLED` | `true` | Send MP4 clips per chunk to the model |
| `VLLM_MAX_CHUNKS_PER_JOB` | `4` | Max chunks analysed per job (cost/latency cap) |
| `VLLM_CHUNK_TIMEOUT_SEC` | `600` | Timeout per multimodal request (large JSON body) |
| `VLLM_CHUNK_MAX_TOKENS` | `1024` | Max completion tokens per chunk |
| `VLLM_SEGMENT_MAX_BYTES` | `18000000` | Skip chunk if re-encoded MP4 exceeds this (~18 MB) |
| `VLLM_VIDEO_SCALE_WIDTH` | `480` | FFmpeg scale width before upload |
| `VLLM_SEGMENT_INCLUDE_AUDIO` | `true` | AAC in segment; retries video-only if ffmpeg fails |
| `WORKER_POLL_INTERVAL_SEC` | `1` | Worker idle sleep |

Set `VLLM_BASE_URL=` empty to skip all vLLM calls (probe + chat).

**Multimodal format:** each chunk is sent as OpenAI-style message `content`: `text` + `video_url` with `data:video/mp4;base64,...`. If your gateway expects a different schema, change [`chunk_video_user_messages`](src/overwatch/vllm_client.py). Events **`vllm_chunk_video`** store `assistant_preview`, `segment_bytes`, and the usual vLLM `data` payload.

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```
