from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from overwatch import __version__
from overwatch.api.routes import router
from overwatch.config import Settings
from overwatch.ingest.folder import FolderIngest
from overwatch.agents.runner import agent_worker_loop
from overwatch.middleware import RequestLogMiddleware
from overwatch.middleware.rate_limit import ApiRateLimitMiddleware, SlidingWindowRateLimiter, client_ip_key
from overwatch.store import open_store
from overwatch.worker import worker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


async def _backfill_search_index(store, indexer, limit: int) -> None:
    """Index existing completed jobs that are not yet in the search index."""
    try:
        jobs = await store.list_jobs(limit=limit)
        already_indexed = await asyncio.to_thread(indexer.get_indexed_job_ids)
        count = 0
        for job in jobs:
            if job.status != "completed" or job.id in already_indexed:
                continue
            events = await store.list_events(job.id)
            for ev in events:
                if ev.event_type == "chunk_analysis":
                    await asyncio.to_thread(
                        indexer.index_chunk_analysis, job.id, job.source_path, ev.payload
                    )
                elif ev.event_type.startswith("agent_"):
                    agent_kind = ev.payload.get("agent_id")
                    result = ev.payload.get("result")
                    if agent_kind and result and not ev.payload.get("error"):
                        await asyncio.to_thread(
                            indexer.index_agent_result,
                            job.id,
                            job.source_path,
                            agent_kind,
                            result,
                        )
            count += 1
        if count:
            logger.info("Search back-fill: indexed %d existing jobs", count)
    except Exception:
        logger.exception("Search back-fill failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    conn, store = await open_store(settings.data_dir)
    app.state.settings = settings
    app.state.store = store
    app.state._db_conn = conn

    stop = asyncio.Event()
    app.state._stop = stop

    # --- Search indexer (optional, disabled if packages missing or SEARCH_ENABLED=false) ---
    indexer = None
    retriever = None
    if settings.search_enabled:
        try:
            from overwatch.search.indexer import SearchIndexer
            from overwatch.search.retrieval import SearchRetriever

            chroma_dir = settings.data_dir / "chroma"
            indexer = SearchIndexer(chroma_dir, embedding_model=settings.search_embedding_model)
            await asyncio.to_thread(indexer.initialize)
            retriever = SearchRetriever(indexer, settings)
            asyncio.create_task(
                _backfill_search_index(store, indexer, limit=settings.search_backfill_limit)
            )
        except Exception:
            logger.exception(
                "Search indexer failed to initialize — search will be unavailable. "
                "Ensure chromadb, sentence-transformers, and rank-bm25 are installed, "
                "or set SEARCH_ENABLED=false to suppress this warning."
            )
            indexer = None
            retriever = None

    app.state.search_indexer = indexer
    app.state.search_retriever = retriever

    worker_task = asyncio.create_task(worker_loop(store, settings, stop, indexer=indexer))

    folder = FolderIngest(settings, store)

    async def ingest_loop() -> None:
        while not stop.is_set():
            try:
                await folder.scan_once()
            except Exception:
                logger.exception("Folder ingest scan failed")
            await asyncio.sleep(settings.ingest_poll_interval_sec)

    ingest_task = asyncio.create_task(ingest_loop())

    if settings.api_rate_limit_per_minute > 0:
        app.state._api_rate_limiter = SlidingWindowRateLimiter(settings.api_rate_limit_per_minute)
    else:
        app.state._api_rate_limiter = None

    agent_worker_task = asyncio.create_task(
        agent_worker_loop(store, settings, stop, indexer=indexer)
    )

    logger.info(
        "Overwatch %s — data_dir=%s ingest_dir=%s search=%s",
        __version__,
        settings.data_dir,
        settings.ingest_dir,
        "enabled" if indexer else "disabled",
    )

    yield

    stop.set()
    ingest_task.cancel()
    worker_task.cancel()
    agent_worker_task.cancel()
    await asyncio.gather(
        ingest_task, worker_task, agent_worker_task, return_exceptions=True
    )
    await conn.close()


app = FastAPI(title="Overwatch", version=__version__, lifespan=lifespan)

_settings_for_cors = Settings()
_origins = _settings_for_cors.cors_origin_list
if _settings_for_cors.cors_origins.strip() == "*":
    _origins = ["*"]
app.add_middleware(ApiRateLimitMiddleware, client_key=client_ip_key)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLogMiddleware)

app.include_router(router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "overwatch", "version": __version__}
