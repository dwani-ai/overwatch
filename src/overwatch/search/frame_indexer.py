from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FRAME_COLLECTION = "overwatch_frames"
_DOC_VERSION = "2"
_EMBED_BATCH = 32

# Default prompts kept separate from config so the module stays dependency-free
_DEFAULT_ALERT_PROMPTS: list[str] = [
    "person lying on the ground",
    "fire or smoke visible",
    "person climbing over a fence or barrier",
    "crowd blocking an emergency exit",
    "forklift operating near a pedestrian",
    "unattended bag or package near a doorway",
]

_OCCUPANCY_EMPTY = "an empty area with no people visible"
_OCCUPANCY_CROWD = "a crowded area with many people"


def _pts_label(pts_ms: int) -> str:
    s = pts_ms // 1000
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Analysis configuration (passed at initialize time)
# ---------------------------------------------------------------------------


@dataclass
class FrameAnalysisConfig:
    """Tunable knobs for all six SigLIP analysis features."""

    # Feature 1 — zero-shot visual alerting
    visual_alert_enabled: bool = True
    visual_alert_prompts: list[str] = field(default_factory=lambda: list(_DEFAULT_ALERT_PROMPTS))
    visual_alert_threshold: float = 0.20  # cosine similarity ≥ this → alert

    # Feature 2 — scene change detection
    scene_change_enabled: bool = True
    scene_change_threshold: float = 0.25  # cosine *distance* ≥ this → cut

    # Feature 3 — occupancy density scoring
    occupancy_scoring_enabled: bool = True

    # Feature 5 — visual diversity keyframes
    keyframe_count: int = 8

    # Feature 6 — baseline anomaly detection
    anomaly_detection_enabled: bool = True
    anomaly_threshold: float = 0.30  # cosine distance from job mean ≥ this → anomaly


# ---------------------------------------------------------------------------
# FrameIndexer
# ---------------------------------------------------------------------------


class FrameIndexer:
    """
    Indexes video keyframes using SigLIP vision-language embeddings and runs
    six analysis passes over the same set of per-frame vectors:

    1. Zero-shot visual alerting     — text-prompt × frame similarity
    2. Scene change detection        — consecutive-frame cosine distance
    3. Occupancy density scoring     — empty↔crowd probe embeddings
    4. Image-to-frame search         — query with an uploaded image
    5. Visual diversity keyframes    — greedy farthest-point sampling
    6. Baseline anomaly detection    — distance from per-job centroid

    Pixel data is never persisted.  Only float32 vectors + metadata go to
    ChromaDB.
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
        self._analysis_config = FrameAnalysisConfig()

        # Pre-computed prompt embeddings (set in _precompute_embeddings)
        self._alert_embed_data: list[tuple[str, list[float], float]] = []
        self._empty_embed: list[float] | None = None
        self._crowd_embed: list[float] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, analysis_config: FrameAnalysisConfig | None = None) -> None:
        """Load SigLIP model, open ChromaDB, pre-compute prompt embeddings."""
        try:
            import chromadb
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise RuntimeError(
                "Frame search requires chromadb, transformers, and torch."
            ) from exc

        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info("Loading SigLIP %s on %s …", self._model_name, self._device)
        self._processor = AutoProcessor.from_pretrained(self._model_name)
        self._model = AutoModel.from_pretrained(self._model_name)
        self._model.eval()
        self._model = self._model.to(self._device)

        with torch.no_grad():
            dummy = self._processor(text=["warmup"], return_tensors="pt", padding=True).to(self._device)
            out = self._model.get_text_features(**dummy)
            self._embedding_dim = int(self._extract_tensor(out).shape[-1])

        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._chroma_dir))
        self._collection = self._client.get_or_create_collection(
            name=_FRAME_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

        if analysis_config is not None:
            self._analysis_config = analysis_config
        self._precompute_embeddings()

        self._initialized = True
        logger.info(
            "FrameIndexer ready — %d frames, model=%s, dim=%d, device=%s, "
            "alerts=%d prompts, occupancy=%s, scene_change=%s, anomaly=%s",
            self._collection.count(),
            self._model_name,
            self._embedding_dim,
            self._device,
            len(self._alert_embed_data),
            self._analysis_config.occupancy_scoring_enabled,
            self._analysis_config.scene_change_enabled,
            self._analysis_config.anomaly_detection_enabled,
        )

    def _precompute_embeddings(self) -> None:
        cfg = self._analysis_config

        # Alert prompts
        self._alert_embed_data = []
        if cfg.visual_alert_enabled and cfg.visual_alert_prompts:
            try:
                embeds = self._embed_texts(cfg.visual_alert_prompts)
                for text, emb in zip(cfg.visual_alert_prompts, embeds):
                    self._alert_embed_data.append((text, emb, cfg.visual_alert_threshold))
                logger.info("Pre-computed %d alert prompt embeddings", len(self._alert_embed_data))
            except Exception:
                logger.exception("Alert prompt embedding failed")

        # Occupancy probe pair
        self._empty_embed = None
        self._crowd_embed = None
        if cfg.occupancy_scoring_enabled:
            try:
                occ = self._embed_texts([_OCCUPANCY_EMPTY, _OCCUPANCY_CROWD])
                self._empty_embed = occ[0]
                self._crowd_embed = occ[1]
            except Exception:
                logger.exception("Occupancy probe embedding failed")

    # ------------------------------------------------------------------
    # Low-level embedding helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tensor(out: Any) -> Any:
        """Return a plain tensor regardless of whether the model returned a
        tensor directly or wrapped it in a ModelOutput dataclass.

        SiglipModel.get_text/image_features() returns a tensor in most
        transformers versions, but some builds return BaseModelOutputWithPooling.
        We handle both cases gracefully.
        """
        if hasattr(out, "shape"):
            return out  # already a tensor
        if hasattr(out, "pooler_output") and out.pooler_output is not None:
            return out.pooler_output
        if hasattr(out, "last_hidden_state"):
            return out.last_hidden_state[:, 0]
        raise ValueError(f"Cannot extract feature tensor from model output of type {type(out)}")

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        inputs = self._processor(
            text=texts, return_tensors="pt", padding=True, truncation=True, max_length=64
        ).to(self._device)
        with self._torch.no_grad():
            feats = self._extract_tensor(self._model.get_text_features(**inputs))
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.cpu().float().numpy().tolist()

    def _embed_jpeg_batch(self, jpeg_list: list[bytes]) -> list[list[float]]:
        import io
        from PIL import Image

        images = [Image.open(io.BytesIO(b)).convert("RGB") for b in jpeg_list]
        inputs = self._processor(images=images, return_tensors="pt").to(self._device)
        with self._torch.no_grad():
            feats = self._extract_tensor(self._model.get_image_features(**inputs))
            feats = feats / feats.norm(dim=-1, keepdim=True)
        return feats.cpu().float().numpy().tolist()

    def _embed_single_jpeg(self, jpeg_bytes: bytes) -> list[float]:
        return self._embed_jpeg_batch([jpeg_bytes])[0]

    # ------------------------------------------------------------------
    # Feature helpers (all operate on pre-computed numpy arrays)
    # ------------------------------------------------------------------

    def _run_visual_alerts(
        self, pts_list: list[int], embeddings: list[list[float]]
    ) -> list[dict[str, Any]]:
        """Feature 1: Score each frame against alert prompts."""
        import numpy as np

        if not self._alert_embed_data or not embeddings:
            return []

        emb_arr = np.array(embeddings, dtype=np.float32)  # (M, D)
        alerts: list[dict[str, Any]] = []
        for prompt_text, prompt_emb, threshold in self._alert_embed_data:
            p = np.array(prompt_emb, dtype=np.float32)
            sims = (emb_arr @ p).tolist()  # cosine sims (both normalized)
            for i, (pts_ms, sim) in enumerate(zip(pts_list, sims)):
                if sim >= threshold:
                    alerts.append(
                        {
                            "pts_ms": pts_ms,
                            "frame_index": i,
                            "prompt": prompt_text,
                            "score": round(float(sim), 4),
                            "ts_label": _pts_label(pts_ms),
                        }
                    )
        # Sort by score descending
        alerts.sort(key=lambda a: a["score"], reverse=True)
        return alerts

    def _run_scene_changes(
        self, pts_list: list[int], embeddings: list[list[float]]
    ) -> list[dict[str, Any]]:
        """Feature 2: Detect scene cuts via consecutive frame cosine distance."""
        import numpy as np

        if len(embeddings) < 2:
            return []

        threshold = self._analysis_config.scene_change_threshold
        emb_arr = np.array(embeddings, dtype=np.float32)
        # cosine distance = 1 − cosine_similarity (embeddings already normalized)
        sims = (emb_arr[:-1] * emb_arr[1:]).sum(axis=1)
        distances = (1 - sims).tolist()

        changes: list[dict[str, Any]] = []
        for i, dist in enumerate(distances):
            if dist >= threshold:
                changes.append(
                    {
                        "pts_ms": pts_list[i + 1],
                        "frame_index": i + 1,
                        "distance": round(float(dist), 4),
                        "ts_label": _pts_label(pts_list[i + 1]),
                    }
                )
        return changes

    def _run_occupancy(
        self, pts_list: list[int], embeddings: list[list[float]]
    ) -> list[dict[str, Any]]:
        """Feature 3: Score each frame on an empty↔crowd density axis."""
        import numpy as np

        if self._empty_embed is None or self._crowd_embed is None:
            return []

        emb_arr = np.array(embeddings, dtype=np.float32)
        empty = np.array(self._empty_embed, dtype=np.float32)
        crowd = np.array(self._crowd_embed, dtype=np.float32)
        sim_empty = (emb_arr @ empty).tolist()
        sim_crowd = (emb_arr @ crowd).tolist()

        timeline: list[dict[str, Any]] = []
        for pts_ms, se, sc in zip(pts_list, sim_empty, sim_crowd):
            # Score ∈ [0, 1]: 0 = empty, 1 = crowded
            raw = (sc - se + 1.0) / 2.0
            score = max(0.0, min(1.0, raw))
            timeline.append(
                {
                    "pts_ms": pts_ms,
                    "occupancy_score": round(float(score), 3),
                    "ts_label": _pts_label(pts_ms),
                }
            )
        return timeline

    def _run_diverse_keyframes(
        self, pts_list: list[int], embeddings: list[list[float]], n: int
    ) -> list[dict[str, Any]]:
        """Feature 5: Greedy farthest-point sampling for visual storyboard."""
        import numpy as np

        m = len(embeddings)
        if m == 0 or n <= 0:
            return []
        n = min(n, m)
        emb_arr = np.array(embeddings, dtype=np.float32)

        # Seed: frame nearest to the global mean (most representative)
        mean = emb_arr.mean(axis=0)
        mean /= np.linalg.norm(mean) + 1e-8
        sims_to_mean = (emb_arr @ mean).tolist()
        selected = [int(np.argmax(sims_to_mean))]

        # Greedily add the frame farthest from all already-selected frames
        while len(selected) < n:
            sel_emb = emb_arr[selected]  # (k, D)
            max_sim_to_sel = (emb_arr @ sel_emb.T).max(axis=1)  # (M,)
            # Exclude already-selected
            for idx in selected:
                max_sim_to_sel[idx] = 2.0
            farthest = int(np.argmin(max_sim_to_sel))
            selected.append(farthest)

        return [
            {
                "pts_ms": pts_list[i],
                "frame_index": i,
                "ts_label": _pts_label(pts_list[i]),
                "sim_to_mean": round(float(sims_to_mean[i]), 4),
            }
            for i in sorted(selected)
        ]

    def _run_anomaly_detection(
        self, pts_list: list[int], embeddings: list[list[float]]
    ) -> list[dict[str, Any]]:
        """Feature 6: Flag frames far from the job's embedding centroid."""
        import numpy as np

        if len(embeddings) < 4:
            return []

        threshold = self._analysis_config.anomaly_threshold
        emb_arr = np.array(embeddings, dtype=np.float32)
        centroid = emb_arr.mean(axis=0)
        centroid /= np.linalg.norm(centroid) + 1e-8
        sims = (emb_arr @ centroid).tolist()
        distances = [1 - s for s in sims]

        anomalies: list[dict[str, Any]] = []
        for i, (pts_ms, dist) in enumerate(zip(pts_list, distances)):
            if dist >= threshold:
                anomalies.append(
                    {
                        "pts_ms": pts_ms,
                        "frame_index": i,
                        "anomaly_score": round(float(dist), 4),
                        "ts_label": _pts_label(pts_ms),
                    }
                )
        anomalies.sort(key=lambda a: a["anomaly_score"], reverse=True)
        return anomalies

    # ------------------------------------------------------------------
    # Feature 4 — image-to-frame search (standalone, no index pass needed)
    # ------------------------------------------------------------------

    def search_by_image(
        self,
        jpeg_bytes: bytes,
        n_results: int = 10,
        job_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Cross-modal image-to-frame search: embed query image, find nearest frames."""
        if not self._initialized or self._collection is None:
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

        count = self._collection.count()
        if count == 0:
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

        try:
            img_embed = self._embed_single_jpeg(jpeg_bytes)
        except Exception:
            logger.exception("Image embedding failed for search_by_image")
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

        n = min(n_results, count)
        kwargs: dict[str, Any] = {
            "query_embeddings": [img_embed],
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
            logger.exception("ChromaDB image query failed")
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

    # ------------------------------------------------------------------
    # Main indexing entry point
    # ------------------------------------------------------------------

    def index_video_frames(
        self,
        job_id: str,
        source_path: str,
        fps: float = 1.0,
        max_frames: int = 500,
        analysis_config: FrameAnalysisConfig | None = None,
    ) -> dict[str, Any]:
        """
        Extract frames, embed with SigLIP, store in ChromaDB, and run all
        six analysis passes.  Returns a result dict with keys:

            frame_count, visual_alerts, scene_changes, occupancy_timeline,
            keyframes, anomalies
        """
        if not self._initialized:
            return _empty_result()

        if analysis_config is not None:
            self._analysis_config = analysis_config
            self._precompute_embeddings()

        from overwatch.video.frames import extract_frames_for_indexing

        path = Path(source_path)
        if not path.is_file():
            logger.warning("Frame indexing skipped — file not found: %s", source_path)
            return _empty_result()

        try:
            frames = extract_frames_for_indexing(path, fps=fps, max_frames=max_frames)
        except Exception:
            logger.exception("Frame extraction failed for %s", source_path)
            return _empty_result()

        if not frames:
            return _empty_result()

        video_filename = path.name
        ids: list[str] = []
        metas: list[dict[str, Any]] = []
        jpeg_list: list[bytes] = []
        pts_list: list[int] = []

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
            pts_list.append(pts_ms)

        # Embed all frames in batches — embeddings shared by all analysis passes
        all_embeddings: list[list[float]] = []
        for start in range(0, len(jpeg_list), _EMBED_BATCH):
            batch = jpeg_list[start : start + _EMBED_BATCH]
            try:
                all_embeddings.extend(self._embed_jpeg_batch(batch))
            except Exception:
                logger.exception(
                    "SigLIP image embedding failed — batch %d–%d of %s",
                    start, start + len(batch), source_path,
                )
                all_embeddings.extend([[0.0] * self._embedding_dim] * len(batch))

        # Upsert frame vectors to ChromaDB
        docs = [f"frame {i}" for i in range(len(ids))]
        try:
            self._collection.upsert(
                ids=ids, embeddings=all_embeddings, metadatas=metas, documents=docs
            )
        except Exception:
            logger.exception("ChromaDB frame upsert failed for %s", source_path)
            return _empty_result()

        # --- Feature 1: Visual alerting ---
        visual_alerts: list[dict[str, Any]] = []
        if self._analysis_config.visual_alert_enabled:
            try:
                visual_alerts = self._run_visual_alerts(pts_list, all_embeddings)
            except Exception:
                logger.exception("Visual alerting failed for %s", source_path)

        # --- Feature 2: Scene change detection ---
        scene_changes: list[dict[str, Any]] = []
        if self._analysis_config.scene_change_enabled:
            try:
                scene_changes = self._run_scene_changes(pts_list, all_embeddings)
            except Exception:
                logger.exception("Scene change detection failed for %s", source_path)

        # --- Feature 3: Occupancy scoring ---
        occupancy_timeline: list[dict[str, Any]] = []
        if self._analysis_config.occupancy_scoring_enabled:
            try:
                occupancy_timeline = self._run_occupancy(pts_list, all_embeddings)
            except Exception:
                logger.exception("Occupancy scoring failed for %s", source_path)

        # --- Feature 5: Diversity keyframes ---
        keyframes: list[dict[str, Any]] = []
        if self._analysis_config.keyframe_count > 0:
            try:
                keyframes = self._run_diverse_keyframes(
                    pts_list, all_embeddings, self._analysis_config.keyframe_count
                )
            except Exception:
                logger.exception("Keyframe selection failed for %s", source_path)

        # --- Feature 6: Anomaly detection ---
        anomalies: list[dict[str, Any]] = []
        if self._analysis_config.anomaly_detection_enabled:
            try:
                anomalies = self._run_anomaly_detection(pts_list, all_embeddings)
            except Exception:
                logger.exception("Anomaly detection failed for %s", source_path)

        logger.info(
            "Indexed %d frames for job %s — %d alerts, %d cuts, %d anomalies",
            len(ids), job_id, len(visual_alerts), len(scene_changes), len(anomalies),
        )
        return {
            "frame_count": len(ids),
            "visual_alerts": visual_alerts,
            "scene_changes": scene_changes,
            "occupancy_timeline": occupancy_timeline,
            "keyframes": keyframes,
            "anomalies": anomalies,
        }

    # ------------------------------------------------------------------
    # Text-based frame search (existing)
    # ------------------------------------------------------------------

    def search_by_text(
        self,
        query: str,
        n_results: int = 10,
        job_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Cross-modal text-to-frame search using SigLIP text encoder."""
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
            logger.exception("Frame ChromaDB text query failed")
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def delete_job_frames(self, job_id: str) -> int:
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
            "alert_prompts": len(self._alert_embed_data),
            "features": {
                "visual_alerts": self._analysis_config.visual_alert_enabled,
                "scene_changes": self._analysis_config.scene_change_enabled,
                "occupancy": self._analysis_config.occupancy_scoring_enabled,
                "keyframes": self._analysis_config.keyframe_count > 0,
                "anomaly": self._analysis_config.anomaly_detection_enabled,
                "image_search": True,
            },
        }


def _empty_result() -> dict[str, Any]:
    return {
        "frame_count": 0,
        "visual_alerts": [],
        "scene_changes": [],
        "occupancy_timeline": [],
        "keyframes": [],
        "anomalies": [],
    }
