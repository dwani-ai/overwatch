# Overwatch — Agentic Video Analytics

Autonomous agents analyse video using **Google Gemma 4 E4B-it** (multimodal), with **FastAPI**, **Docker**, and (planned) **Google ADK** and **A2A**.

## Goals

- Main events observed  
- Security issues  
- Logistics / item tracking (**SAM 3.1 later**)  
- **Attendance: anonymous counts only** (no identity)  

All processed video runs are logged with **timestamp + frame index + event payload**.

## What is implemented (v0)

- **Overwatch API**: `GET /v1/health`, `GET/POST /v1/jobs`, **`POST /v1/jobs/upload`** (multipart video), **`GET /v1/jobs/{id}/summary`**, **`GET /v1/jobs/{id}/events`** (paginated; `legacy=true` for full list)  
- **Agents (v0):** Job-level text agents over the stored **summary** JSON (same vLLM as the pipeline):
  - **Synthesis** — cross-chunk narrative and recommended actions (`agent_synthesis` event).
  - **Risk review** — safety / security triage (`agent_risk_review` event).
  - **Async queue:** **`POST /v1/jobs/{id}/agent-runs`** with body `{"agent":"synthesis"|"risk_review","force":?}` returns **202** and a **`run_id`**; poll **`GET /v1/agent-runs/{run_id}`** until `status` is `completed` or `failed`. A background **agent worker** (started with the API) drains the queue and still appends **orchestrator** events for audit.
  - **Blocking synthesis** (optional): **`POST /v1/jobs/{id}/agents/synthesis?blocking=true`** waits for the LLM in-process (legacy / scripts).
  - **Latest by kind:** **`GET …/agents/synthesis`** and **`GET …/agents/risk-review`** return the newest stored event payload. The web UI runs agents via the async API and polls.  
- **Web UI** (Vite + React): upload a video and poll for job status + summary (`frontend/`; Docker **`overwatch-ui`** publishes the gateway on host port **80**)  
- **SQLite** job + event store under `DATA_DIR`; jobs include **`summary_json`** (aggregated structured analysis)  
- **Folder ingest**: poll `INGEST_DIR` for new video files; stable-write detection (`INGEST_STABLE_SEC`)  
- **Worker** — per chunk:
  1. **Observe** (multimodal): MP4 clip → strict JSON `scene_summary` + `observations[]` (with JSON repair retries)  
  2. **Specialists** (text-only, 3 calls): `main_events`, `security`+`logistics`, **count-only** `attendance`  
  3. **Merged** `ChunkAnalysisMerged` stored on **`chunk_analysis`** event  
  4. **Job summary** (`schema_version: "1"`) lists all chunk merges → **`GET …/summary`** and `JobRecord.summary`  
- **Docker Compose**: **`overwatch-api`** + **`overwatch-ui`** by default; local GPU vLLM is optional (`--profile local-vllm`).

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
export VLLM_BASE_URL=https://your-vllm-host.example/v1
export VLLM_MODEL=gemma4
# export VLLM_API_KEY=...   # if required
PYTHONPATH=src uvicorn overwatch.main:app --reload --port 8080
```

Drop files into `data/ingest/` (e.g. `.mp4`). After they are stable for `INGEST_STABLE_SEC`, a job is enqueued.

**Web UI (development):** in another terminal, from `frontend/` run `npm install` then `npm run dev`. The dev server proxies `/api/*` to `http://127.0.0.1:8080/v1/*`, so keep the API on port **8080** and open **http://localhost:5173**. You can upload a file there instead of copying into `INGEST_DIR`.

## Run with Docker Compose

Set **`VLLM_BASE_URL`** (and optional **`VLLM_API_KEY`**) in your environment or a **`.env`** file next to `compose.yml` (Compose loads it automatically). Example:

```bash
# .env (not committed) or export in shell:
# VLLM_BASE_URL=https://your-vllm-host.example/v1
# VLLM_API_KEY=...

export VLLM_MODEL=gemma4  # served model id on your vLLM
docker compose up --build
```

- **API + UI on port 80:** `http://localhost/v1/health`, `http://localhost/v1/jobs`, … — the **`overwatch-api`** container is **not** published on 8080; nginx proxies **`/v1/`**, **`/api/`**, **`/docs`**, **`/redoc`**, **`openapi.json`**, and **`/service`** (same JSON as the API’s `GET /` root).  
- **UI + gateway (Compose):** `http://localhost/` — React app at `/`; ensure nothing else on the host is bound to port 80. If your Docker setup cannot publish host ports below 1024 (some rootless setups), change `overwatch-ui` in `compose.yml` to e.g. `"8088:80"` and use `http://localhost:8088/` (same paths under that origin).  
- Remote vLLM: `https://some-vllm` (no local GPU in default compose)

**Creating a job from the host (Docker):** paths inside the container are **not** the same as on your laptop. The compose file mounts host `./data/ingest` at **`/data/ingest`** in the container. Use either:

```bash
# Easiest: basename only (resolved under INGEST_DIR in the container)
curl -s -X POST 'http://localhost/v1/jobs' \
  -H 'Content-Type: application/json' \
  -d '{"filename":"warehouse-1.mp4"}'

# Or the full path *as seen inside the container*
curl -s -X POST 'http://localhost/v1/jobs' \
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
| `VLLM_BASE_URL` | _(empty)_ | **Required** for vLLM calls: OpenAI prefix before `/chat/completions` (e.g. `https://your-host/v1`). Set in `.env` or the shell; do not hardcode in the app. |
| `VLLM_MODEL` | `gemma4` | Model id for chat completions |
| `VLLM_API_KEY` | _(unset)_ | `Authorization: Bearer …` if required |
| `VLLM_CHAT_TIMEOUT_SEC` | `120` | HTTP timeout for text chat completions |
| `VLLM_MULTIMODAL_ENABLED` | `true` | Send MP4 clips per chunk to the model |
| `VLLM_MAX_CHUNKS_PER_JOB` | `4` | Max chunks analysed per job (cost/latency cap) |
| `VLLM_CHUNK_TIMEOUT_SEC` | `600` | Timeout per multimodal request (large JSON body) |
| `VLLM_CHUNK_MAX_TOKENS` | `1024` | Max completion tokens for **observe** (multimodal JSON) |
| `VLLM_JSON_RETRY_MAX` | `2` | JSON parse repair rounds per LLM step |
| `VLLM_SPECIALIST_MAX_TOKENS` | `800` | Max tokens per **text specialist** call |
| `VLLM_AGENT_MAX_TOKENS` | `2048` | Max tokens for **synthesis** agent (job-level text pass) |
| `VLLM_AGENT_TIMEOUT_SEC` | `120` | HTTP timeout for synthesis agent chat completion |
| `VLLM_SEGMENT_MAX_BYTES` | `18000000` | Skip chunk if re-encoded MP4 exceeds this (~18 MB) |
| `VLLM_VIDEO_SCALE_WIDTH` | `480` | FFmpeg scale width before upload |
| `VLLM_SEGMENT_INCLUDE_AUDIO` | `true` | AAC in segment; retries video-only if ffmpeg fails |
| `WORKER_POLL_INTERVAL_SEC` | `1` | Worker idle sleep |
| `AGENT_WORKER_POLL_INTERVAL_SEC` | `0.4` | Sleep when the agent run queue is empty |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated browser origins for the API; use `*` to allow all (dev only). Needed when the UI origin differs from the API (e.g. Vite on 5173). The Compose gateway on port 80 uses same-origin `/api` and does not rely on CORS. |

Set `VLLM_BASE_URL=` empty to skip all vLLM calls (probe + chat).

**API quick reference**

- `GET /v1/jobs/{id}/events?limit=50&after_id=0` → `{ "items": [...], "next_after_id": <id|null> }` (use `next_after_id` as the next `after_id`)  
- `GET /v1/jobs/{id}/events?legacy=true` → full array (large jobs)  
- `GET /v1/jobs/{id}/summary` → aggregated `chunk_analyses` after the job completes  
- `GET /v1/jobs/{id}` includes `summary` when present  
- `POST /v1/jobs/{id}/agent-runs` → queue agent run (**202** + `run_id`); `GET /v1/agent-runs/{run_id}` → status and `result`  
- `GET /v1/jobs/{id}/agent-runs` → recent runs for that job  
- `POST /v1/jobs/{id}/agents/synthesis?blocking=true` → blocking synthesis; without `blocking`, **202** + async queue (same as `agent-runs` with `synthesis`)  
- `GET /v1/jobs/{id}/agents/synthesis` / **`…/agents/risk-review`** → latest stored event payload  

**Observe multimodal format:** [`chunk_video_user_messages`](src/overwatch/vllm_client.py) — `text` + `video_url` (`data:video/mp4;base64,...`). Structured parsing lives in [`analysis/chunk_pipeline.py`](src/overwatch/analysis/chunk_pipeline.py) and [`analysis/json_extract.py`](src/overwatch/analysis/json_extract.py).

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```
