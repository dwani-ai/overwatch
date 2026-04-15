from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FRAME_COLLECTION = "overwatch_frames"
_DOC_VERSION = "1"
_EMBED_BATCH = 32  # frames per SigLIP forward pass


def _pts_label(pts_ms: int) -> str:
    total_sec = pts_ms // 1000
    return f"{total_sec // 60}:{total_sec % 60:02d}"


class FrameIndexer:
    """
    Indexes video keyframes using SigLIP vision-language embeddings.

    Architecture:
    - Video → ffmpeg → JPEG keyframes (in temp dir, immediately discarded)
    - Keyframes → SigLIP image encoder → float32 vectors
    - Vectors + metadata → ChromaDB ``overwatch_frames`` collection
    - At search time: text query → SigLIP text encoder → vector → ChromaDB ANN

    Privacy: pixel data is never persisted.  Only embedding vectors and metadata
    (job_id, pts_ms, source_path) are stored.
    """

    def __init__(
        self,
        chroma_dir: Path,
        model_name: str = "google/siglip-base-patch16-224",
    ) -> None:
        self._chroma_dir = chroma_dir
        self._model_name = model_name
        self._lock = threading.Lock()
        self._client: Any = None
        self._collection: Any = None
        self._model: Any = None
        self._processor: Any = None
        self._torch: Any = None
        self._device = "cpu"
        self._embedding_dim = 0
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Load SigLIP model and open the ChromaDB frame collection."""
        try:
            import chromadb
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise RuntimeError(
                "Frame search requires chromadb, transformers, and torch. "
                "Install with: pip install chromadb sentence-transformers"
            ) from exc

        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info("Loading SigLIP %s on %s …", self._model_name, self._device)
        self._processor = AutoProcessor.from_pretrained(self._model_name)
        self._model = AutoModel.from_pretrained(self._model_name)
        self._model.eval()
        self._model = self._model.to(self._device)

        # Determine embedding dimension via dummy forward pass
        with torch.no_grad():
            dummy = self._processor(
                text=["warmup"], return_tensors="pt"
            ).to(self._device)
            out = self._model.get_text_features(**dummy)
            self._embedding_dim = int(out.shape[-1])

        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._chroma_dir))
        # No embedding_function — all embeddings are supplied pre-computed
        self._collection = self._client.get_or_create_collection(
            name=_FRAME_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

        self._initialized = True
        logger.info(
            "FrameIndexer ready — %d frames, model=%s, dim=%d, device=%s",
            self._collection.count(),
            self._model_name,
            self._embedding_dim,
            self._device,
        )

    # ------------------------------------------------------------------
    # Embedding helpers (synchronous, call from thread pool)
    # ------------------------------------------------------------------

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        inputs = self._processor(
            text=texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=64,
        ).to(self._device)
        with self._torch.no_grad():
            feats = self._model.get_text_features(**inputs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.cpu().float().numpy().tolist()

    def _embed_jpeg_batch(self, jpeg_list: list[bytes]) -> list[list[float]]:
        import io
        from PIL import Image

        images = [Image.open(io.BytesIO(b)).convert("RGB") for b in jpeg_list]
        inputs = self._processor(images=images, return_tensors="pt").to(self._device)
        with self._torch.no_grad():
            feats = self._model.get_image_features(**inputs)
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.cpu().float().numpy().tolist()

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_video_frames(
        self,
        job_id: str,
        source_path: str,
        fps: float = 1.0,
        max_frames: int = 500,
    ) -> int:
        """
        Extract keyframes, embed with SigLIP, upsert to ChromaDB.
        Returns the number of frames indexed.  Safe to call multiple times
        (uses deterministic IDs so existing frames are updated in-place).
        """
        if not self._initialized:
            return 0

        from overwatch.video.frames import extract_frames_for_indexing

        path = Path(source_path)
        if not path.is_file():
            logger.warning("Frame indexing skipped — file not found: %s", source_path)
            return 0

        try:
            frames = extract_frames_for_indexing(path, fps=fps, max_frames=max_frames)
        except Exception:
            logger.exception("Frame extraction failed for %s", source_path)
            return 0

        if not frames:
            return 0

        video_filename = path.name
        ids: list[str] = []
        metas: list[dict[str, Any]] = []
        jpeg_list: list[bytes] = []

        for i, (pts_ms, jpeg_bytes) in enumerate(frames):
            ids.append(f"{job_id}__frame__{i}")
            metas.append(
                {
                    "job_id": job_id,
                    "source_path": source_path,
                    "video_filename": video_filename,
                    "pts_ms": pts_ms,
                    "frame_index": i,
                    "doc_type": "frame",
                    "doc_version": _DOC_VERSION,
                }
            )
            jpeg_list.append(jpeg_bytes)

        # Embed in batches to keep peak memory bounded
        all_embeddings: list[list[float]] = []
        for start in range(0, len(jpeg_list), _EMBED_BATCH):
            batch = jpeg_list[start : start + _EMBED_BATCH]
            try:
                all_embeddings.extend(self._embed_jpeg_batch(batch))
            except Exception:
                logger.exception(
                    "SigLIP image embedding failed — batch %d–%d of %s",
                    start,
                    start + len(batch),
                    source_path,
                )
                # Zero-fill so the collection stays consistent in size
                all_embeddings.extend([[0.0] * self._embedding_dim] * len(batch))

        docs = [f"frame {i}" for i in range(len(ids))]
        try:
            self._collection.upsert(
                ids=ids,
                embeddings=all_embeddings,
                metadatas=metas,
                documents=docs,
            )
        except Exception:
            logger.exception("ChromaDB frame upsert failed for %s", source_path)
            return 0

        logger.info("Indexed %d frames for job %s (%s)", len(ids), job_id, video_filename)
        return len(ids)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def search_by_text(
        self,
        query: str,
        n_results: int = 10,
        job_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Cross-modal text-to-frame search.
        Encodes the text query with SigLIP's text encoder, then finds the
        nearest frame embeddings in the vector space.
        """
        if not self._initialized or self._collection is None:
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

        count = self._collection.count()
        if count == 0:
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

        try:
            query_embed = self._embed_texts([query])[0]
        except Exception:
            logger.exception("SigLIP text embedding failed for query: %.80s", query)
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

        n = min(n_results, count)
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embed],
            "n_results": n,
            "include": ["metadatas", "distances"],
        }
        if job_ids:
            kwargs["where"] = (
                {"job_id": job_ids[0]} if len(job_ids) == 1 else {"job_id": {"$in": list(job_ids)}}
            )

        try:
            return self._collection.query(**kwargs)
        except Exception:
            logger.exception("Frame ChromaDB query failed")
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def delete_job_frames(self, job_id: str) -> int:
        """Remove all frame documents for a job. Returns count deleted."""
        if self._collection is None:
            return 0
        try:
            result = self._collection.get(where={"job_id": job_id}, include=[])
            ids = result.get("ids", []) or []
            if ids:
                self._collection.delete(ids=ids)
            return len(ids)
        except Exception:
            logger.exception("delete_job_frames failed for %s", job_id)
            return 0

    def get_job_frame_count(self, job_id: str) -> int:
        if self._collection is None:
            return 0
        try:
            result = self._collection.get(where={"job_id": job_id}, include=[])
            return len(result.get("ids", []) or [])
        except Exception:
            return 0

    def get_indexed_job_ids(self) -> set[str]:
        if self._collection is None:
            return set()
        try:
            result = self._collection.get(include=["metadatas"])
            metas: list[dict] = result.get("metadatas", []) or []
            return {str(m.get("job_id", "")) for m in metas if m.get("job_id")}
        except Exception:
            return set()

    def get_status(self) -> dict[str, Any]:
        count = self._collection.count() if self._collection else 0
        return {
            "enabled": self._initialized,
            "total_frames": count,
            "frame_embed_model": self._model_name,
            "embedding_dim": self._embedding_dim,
            "device": self._device,
        }
