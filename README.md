# Overwatch — Agentic Video Analytics

Autonomous pipeline that analyses surveillance and operations video using a **multimodal LLM** (Gemma 4 / any OpenAI-compatible model), a **hybrid RAG search engine**, and **SigLIP-ViT visual intelligence** — all in a single Docker Compose stack.

> **Technical deep-dive:** [docs/technical_report.md](docs/technical_report.md) — architecture, data model, API surface, search system, SigLIP features, configuration, deployment, and known limitations (~18 pages).

---

## Major features

### 1. Multimodal chunk analysis

Every video is cut into time-bounded chunks and put through four LLM passes using the OpenAI-compatible chat completions API:

| Pass | Type | Output |
|------|------|--------|
| **Observe** | Multimodal (MP4 → LLM) | `scene_summary`, `observations[]` (what / where / when) |
| **Main events** | Text specialist | `main_events[]` with confidence |
| **Security + Logistics** | Text specialist | `security[]` with severity, `logistics[]` with action |
| **Attendance** | Text specialist | `approx_people_visible`, `entries`, `exits` — **counts only, no identity** |

Results merge into a `ChunkAnalysisMerged` event and roll up into a searchable `job_summary`.

---

### 2. Seven job-level agents

Text-only agents run over the stored job summary after analysis completes. All are async-queued, pollable, and result-cached.

| Agent | Purpose |
|-------|---------|
| `synthesis` | Executive summary, key observations, security/logistics highlights, attendance summary, recommended actions |
| `risk_review` | Overall risk level (low/medium/high), risk factors, operator notes, suggested mitigations |
| `incident_brief` | Incident narrative, key moments, situational factors, follow-up checks |
| `compliance_brief` | SOP alignment, observed practices, gaps, verifications needed |
| `loss_prevention` | LP narrative, behavioural observations, risk level, suggested actions |
| `perimeter_chain` | Ordered boundary/access storyline, zone labels, follow-up checks |
| `privacy_review` | Identity inference risks, sensitive descriptors, safe output guidance |

Run a single agent, a custom ordered sequence, or one of **11 pre-built industry pipelines**:

```
general · retail_qsr · logistics_warehouse · manufacturing · commercial_real_estate
transportation_hubs · critical_infrastructure · banking_atm · hospitality_venues
education_campus · healthcare_facilities
```

---

### 3. Hybrid RAG search

Query across all videos and all agents with a single search call — no video metadata pre-knowledge required.

- **ChromaDB vector search** — `bge-small-en-v1.5` (384-dim) over all chunk observations, security items, logistics items, and agent outputs
- **BM25 keyword search** — in-memory, rebuilt from ChromaDB on startup
- **RRF fusion** — Reciprocal Rank Fusion merges all three rankings (vector + BM25 + SigLIP frames) into a single ranked list
- **Filters** — by job, agent type, severity
- **AI answer** — optional LLM answer synthesis from top results (toggle in UI)

```
POST /v1/search
{
  "query": "forklift near pedestrian loading dock",
  "limit": 10,
  "job_ids": ["abc123"],
  "severity": "high",
  "synthesize_answer": true,
  "include_frames": true
}
```

---

### 4. SigLIP-ViT frame intelligence

Every completed job's keyframes are embedded with **`google/siglip-base-patch16-224`** (768-dim, ~400 MB). Six analysis passes run over the same frame embeddings — no pixel data is ever persisted.

| Feature | What it does | Config |
|---------|-------------|--------|
| **Zero-shot visual alerting** | Score every frame against configurable text prompts; emit `visual_alert` events for matches | `VISUAL_ALERT_PROMPTS`, `VISUAL_ALERT_THRESHOLD` |
| **Scene change detection** | Flag frames where consecutive embedding distance jumps above threshold | `SCENE_CHANGE_THRESHOLD` |
| **Occupancy density scoring** | Score each frame on an empty↔crowded axis using probe embeddings | `OCCUPANCY_SCORING_ENABLED` |
| **Visual diversity keyframes** | Greedy farthest-point sampling selects representative storyboard frames | `FRAME_KEYFRAME_COUNT` |
| **Baseline anomaly detection** | Flag frames far from the job's visual centroid | `ANOMALY_THRESHOLD` |
| **Image-to-frame search** | Upload a reference image, find visually similar frames across all videos | `POST /v1/search/by-image` |

Default alert prompts (configurable via `VISUAL_ALERT_PROMPTS`):
```
person lying on the ground · fire or smoke visible · person climbing over a fence
crowd blocking an emergency exit · forklift operating near a pedestrian
unattended bag or package near a doorway
```

---

### 5. Cross-modal search

Search video frames using **natural language** or an **uploaded image** — no labels or training required.

**Text → frame** (cross-modal via SigLIP text encoder):
```
POST /v1/search  { "query": "empty warehouse aisle", "include_frames": true }
```

**Image → frame** (via SigLIP image encoder):
```
POST /v1/search/by-image   (multipart image file)
```

Results from both text analysis and frame embeddings are fused via RRF into a single ranked list.

---

### 6. Web UI

React + Vite frontend with:

- **Upload** or path-based job submission; auto-polling job list
- **Job detail** — probe info, chunk event timeline, per-agent result panels
- **One-click orchestration** — industry pack dropdown, custom step builder
- **Search panel** — text search with filters + AI answer toggle + 🎞 frame search toggle
- **Image search tab** — drag-drop an image to find visually similar frames
- **Frame Analysis panel** (per completed job, 5 tabs):
  - **Visual Alerts** — SigLIP-flagged frames with prompt, severity, timestamp, score
  - **Occupancy** — colour-coded crowd density bar chart over time
  - **Keyframes** — diverse representative moment storyboard
  - **Scene Changes** — detected cuts with cosine distance
  - **Anomalies** — frames visually unlike the rest of the video

---

## Architecture overview

```
Video file / upload
       │
       ▼
  ffprobe + chunk planner
       │
       ▼ (per chunk, MP4 bytes)
  Multimodal LLM  ──────────► observe → main events → security/logistics → attendance
       │
       ▼
  ChunkAnalysisMerged  ──────► ChromaDB (bge-small text embeddings)
       │
       ▼
  JobSummaryPayload
       │
       ├──► 7 text agents (async queue, orchestrable, industry packs)
       │             │
       │             └──► ChromaDB (agent text embeddings)
       │
       └──► SigLIP frame indexer (fire-and-forget background task)
                     │
                     ├──► ChromaDB (SigLIP image embeddings)
                     ├──► visual_alert events (SQLite)
                     ├──► scene_changes event (SQLite)
                     ├──► frame_occupancy event (SQLite)
                     ├──► frame_keyframes event (SQLite)
                     └──► frame_anomalies event (SQLite)

Search query
       │
       ├──► ChromaDB vector search (bge-small)
       ├──► BM25 keyword search (in-memory)
       └──► ChromaDB vector search (SigLIP)
                     │
                     └──► RRF fusion ──► ranked results + optional LLM answer
```

> Full architecture, data model, and all API routes: [docs/technical_report.md](docs/technical_report.md)

---

## Quick start

### Local (no Docker)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATA_DIR=./data/overwatch
export INGEST_DIR=./data/ingest
export VLLM_BASE_URL=https://your-vllm-host.example/v1
export VLLM_MODEL=gemma4
# export VLLM_API_KEY=...   # if required

PYTHONPATH=src uvicorn overwatch.main:app --reload --port 8080
```

Drop `.mp4` / `.mkv` / `.mov` files into `data/ingest/`. They are auto-ingested after `INGEST_STABLE_SEC` (default 2 s).

**Web UI (dev):** in a second terminal, from `frontend/`:
```bash
npm install && npm run dev
```
Open **http://localhost:5173** — the dev server proxies `/api/*` to the API on port 8080.

### Docker Compose

```bash
# .env  (not committed)
# VLLM_BASE_URL=https://your-vllm-host.example/v1
# VLLM_API_KEY=...

export VLLM_MODEL=gemma4
docker compose up --build
```

- **UI + API:** http://localhost/
- **API only:** http://localhost/v1/health
- **OpenAPI docs:** http://localhost/docs

For **local GPU vLLM**:
```bash
export HF_TOKEN=...
docker compose --profile local-vllm up --build
# then set VLLM_BASE_URL=http://vllm-server:10802/v1
```

Mount points: `./data/ingest` → `/data/ingest`, `./data/overwatch` → `/data/overwatch`.

> **SigLIP first-run note:** `FRAME_SEARCH_ENABLED=true` (default) downloads the SigLIP model (~400 MB from HuggingFace Hub) on first startup. Mount `~/.cache/huggingface` or set `TRANSFORMERS_CACHE` to a volume to avoid re-downloading on restarts.

---

## Creating a job (Docker)

Use container-side paths, not host paths:

```bash
# Easiest: filename only (resolved under /data/ingest)
curl -s -X POST http://localhost/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"filename":"warehouse-1.mp4"}'

# Or upload directly
curl -s -X POST http://localhost/v1/jobs/upload \
  -F "file=@/path/to/local/video.mp4"
```

---

## Key API endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /v1/health` | Service health + search/frame search status |
| `GET /v1/jobs` | List recent jobs |
| `POST /v1/jobs/upload` | Upload a video file (multipart) |
| `GET /v1/jobs/{id}/summary` | Aggregated chunk analysis |
| `GET /v1/jobs/{id}/events` | Paginated event log (`after_id`, `limit`) |
| `POST /v1/jobs/{id}/agent-runs` | Queue an agent run (202 async) |
| `POST /v1/jobs/{id}/agent-runs/orchestrate/industry` | Run named industry pipeline |
| `GET /v1/agent-runs/{run_id}` | Poll agent run status |
| `POST /v1/search` | Hybrid RAG search (text + frames + BM25) |
| `POST /v1/search/by-image` | Find frames similar to an uploaded image |
| `GET /v1/jobs/{id}/visual-alerts` | SigLIP zero-shot alert events |
| `GET /v1/jobs/{id}/occupancy` | Per-frame crowd density timeline |
| `GET /v1/jobs/{id}/keyframes` | Diverse representative keyframe timestamps |
| `GET /v1/jobs/{id}/scene-changes` | Detected scene cuts |
| `GET /v1/jobs/{id}/anomalies` | Visually anomalous frames |
| `DELETE /v1/jobs/{id}` | Delete job + events + search index + frame embeddings |

Full API reference: **`GET /docs`** or **`GET /redoc`** on the running server.

---

## Configuration

Key environment variables (all have sensible defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_BASE_URL` | _(empty)_ | **Required** — OpenAI-compatible prefix before `/chat/completions` |
| `VLLM_MODEL` | `gemma4` | Model ID for chat completions |
| `VLLM_API_KEY` | _(unset)_ | Bearer token if required |
| `VLLM_MAX_CHUNKS_PER_JOB` | `4` | Chunks analysed per job (cost cap) |
| `VLLM_MULTIMODAL_ENABLED` | `true` | Send MP4 clips to the model |
| `DATA_DIR` | `./data/overwatch` | SQLite DB, ChromaDB, and state |
| `INGEST_DIR` | `./data/ingest` | Folder watched for new video files |
| `SEARCH_ENABLED` | `true` | Hybrid text RAG search |
| `SEARCH_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | Sentence-transformers model for text search |
| `FRAME_SEARCH_ENABLED` | `true` | SigLIP frame embedding + visual analysis |
| `FRAME_EMBED_MODEL` | `google/siglip-base-patch16-224` | SigLIP model (~400 MB) |
| `FRAME_SAMPLE_FPS` | `1.0` | Frames per second extracted per video |
| `VISUAL_ALERT_PROMPTS` | _(6 safety prompts)_ | Comma-separated zero-shot alert prompts |
| `VISUAL_ALERT_THRESHOLD` | `0.20` | SigLIP cosine similarity threshold for alerts |
| `SCENE_CHANGE_THRESHOLD` | `0.25` | Cosine distance threshold for scene cuts |
| `ANOMALY_THRESHOLD` | `0.30` | Distance from job centroid to flag anomalous frames |
| `FRAME_KEYFRAME_COUNT` | `8` | Diverse representative frames per job |
| `MAX_UPLOAD_BYTES` | `536870912` | Upload cap (512 MiB) |
| `API_RATE_LIMIT_PER_MINUTE` | `0` | Per-IP rate limit (0 = disabled) |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Browser origins for CORS |

Set `VLLM_BASE_URL=` (empty) to disable all LLM calls. Set `SEARCH_ENABLED=false` to disable search entirely.

> Full configuration reference with all 50+ variables: [docs/technical_report.md § 13](docs/technical_report.md)

---

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [docs/technical_report.md](docs/technical_report.md) | Full engineering reference (~18 pages): architecture, data model, chunk pipeline, search system, SigLIP features, API surface, configuration, deployment, limitations |
| [docs/eval_report.md](docs/eval_report.md) | Research evaluation plan — layered methodology, run cards, rubrics |
| [docs/factorio_report.md](docs/factorio_report.md) | Factorio closed-loop agent (research prototype) |

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| API | Python / FastAPI / aiosqlite |
| LLM | Any OpenAI-compatible vLLM endpoint (tested: Gemma 4 E4B-it) |
| Text search | ChromaDB + bge-small-en-v1.5 + rank-bm25 (RRF) |
| Frame AI | SigLIP-ViT (`google/siglip-base-patch16-224`) via HuggingFace transformers |
| Video | ffmpeg (chunking, frame extraction) |
| Frontend | React + Vite + nginx |
| Deployment | Docker Compose (optional local GPU vLLM profile) |
