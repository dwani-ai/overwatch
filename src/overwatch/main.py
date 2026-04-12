from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from overwatch import __version__
from overwatch.api.routes import router
from overwatch.config import Settings
from overwatch.ingest.folder import FolderIngest
from overwatch.store import open_store
from overwatch.worker import worker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    conn, store = await open_store(settings.data_dir)
    app.state.settings = settings
    app.state.store = store
    app.state._db_conn = conn

    stop = asyncio.Event()
    app.state._stop = stop

    worker_task = asyncio.create_task(worker_loop(store, settings, stop))

    folder = FolderIngest(settings, store)

    async def ingest_loop() -> None:
        while not stop.is_set():
            try:
                await folder.scan_once()
            except Exception:
                logger.exception("Folder ingest scan failed")
            await asyncio.sleep(settings.ingest_poll_interval_sec)

    ingest_task = asyncio.create_task(ingest_loop())

    logger.info(
        "Overwatch %s — data_dir=%s ingest_dir=%s",
        __version__,
        settings.data_dir,
        settings.ingest_dir,
    )

    yield

    stop.set()
    ingest_task.cancel()
    worker_task.cancel()
    await asyncio.gather(ingest_task, worker_task, return_exceptions=True)
    await conn.close()


app = FastAPI(title="Overwatch", version=__version__, lifespan=lifespan)
app.include_router(router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "overwatch", "version": __version__}
