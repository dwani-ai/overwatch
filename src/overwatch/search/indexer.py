from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_COLLECTION_NAME = "overwatch_events"
_DOC_VERSION = "1"


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


def _doc_id(*parts: str | int) -> str:
    return "__".join(str(p) for p in parts)


def _flatten_agent_result(agent_kind: str, result: dict[str, Any]) -> list[str]:
    """Convert an agent result dict into a list of indexable text strings."""
    texts: list[str] = []

    def _add(text: str) -> None:
        t = str(text).strip()
        if t:
            texts.append(t)

    def _add_list(items: list[Any], prefix: str = "") -> None:
        for item in items or []:
            s = str(item).strip()
            if s:
                texts.append(f"{prefix}{s}" if prefix else s)

    if agent_kind == "synthesis":
        _add(result.get("executive_summary", ""))
        _add_list(result.get("key_observations", []))
        _add_list(result.get("security_highlights", []), "[Security] ")
        _add_list(result.get("logistics_highlights", []), "[Logistics] ")
        _add(result.get("attendance_summary", ""))
        _add_list(result.get("recommended_actions", []), "[Action] ")
    elif agent_kind == "risk_review":
        _add(result.get("operator_notes", ""))
        _add_list(result.get("risk_factors", []), "[Risk] ")
        _add_list(result.get("mitigations_suggested", []), "[Mitigation] ")
    elif agent_kind == "incident_brief":
        _add(result.get("narrative", ""))
        _add_list(result.get("key_moments", []))
        _add_list(result.get("situational_factors", []))
        _add_list(result.get("suggested_followups", []), "[Followup] ")
    elif agent_kind == "compliance_brief":
        _add(result.get("notes", ""))
        _add_list(result.get("observed_practices", []))
        _add_list(result.get("gaps_or_concerns", []), "[Gap] ")
        _add_list(result.get("recommended_verifications", []))
    elif agent_kind == "loss_prevention":
        _add(result.get("narrative", ""))
        _add_list(result.get("behavioral_observations", []))
        _add_list(result.get("suggested_actions", []), "[Action] ")
    elif agent_kind == "perimeter_chain":
        _add(result.get("chain_narrative", ""))
        _add_list(result.get("key_events", []))
        _add_list(result.get("zones_or_segments", []))
        _add_list(result.get("follow_up_checks", []))
    elif agent_kind == "privacy_review":
        _add(result.get("summary", ""))
        _add_list(result.get("identity_inference_risks", []))
        _add_list(result.get("sensitive_descriptors", []))
        _add_list(result.get("safe_output_guidance", []))
    else:
        for v in result.values():
            if isinstance(v, str):
                _add(v)
            elif isinstance(v, list):
                _add_list(v)

    return texts


class SearchIndexer:
    """
    Hybrid search indexer: ChromaDB (vector) + BM25 (keyword) over video analysis events.

    All public methods are synchronous (run them via ``asyncio.to_thread`` from async code).
    Thread safety: ChromaDB operations are protected by the internal lock; the BM25 corpus
    is maintained in memory and rebuilt lazily after each batch upsert.
    """

    def __init__(self, chroma_dir: Path, embedding_model: str = "BAAI/bge-small-en-v1.5") -> None:
        self._chroma_dir = chroma_dir
        self._embedding_model = embedding_model
        self._lock = threading.Lock()
        self._client: Any = None
        self._collection: Any = None
        self._bm25: Any = None
        self._bm25_ids: list[str] = []
        self._bm25_corpus: list[str] = []
        self._doc_jobid: dict[str, str] = {}
        self._dirty = False
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Set up ChromaDB and build initial BM25 from any existing documents."""
        try:
            import chromadb
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        except ImportError as exc:
            raise RuntimeError(
                "Search dependencies are not installed. "
                "Run: pip install chromadb sentence-transformers rank-bm25"
            ) from exc

        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._chroma_dir))
        ef = SentenceTransformerEmbeddingFunction(model_name=self._embedding_model)
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        self._load_bm25_from_chroma()
        self._initialized = True
        logger.info(
            "SearchIndexer ready — %d docs, model=%s",
            len(self._bm25_corpus),
            self._embedding_model,
        )

    def _load_bm25_from_chroma(self) -> None:
        if self._collection is None:
            return
        try:
            result = self._collection.get(include=["documents", "metadatas"])
            ids: list[str] = result.get("ids") or []
            docs: list[str] = result.get("documents") or []
            metas: list[dict] = result.get("metadatas") or []
            with self._lock:
                self._bm25_ids = list(ids)
                self._bm25_corpus = [d or "" for d in docs]
                self._doc_jobid = {}
                for doc_id, meta in zip(ids, metas):
                    if meta and meta.get("job_id"):
                        self._doc_jobid[doc_id] = meta["job_id"]
                self._rebuild_bm25_locked()
                self._dirty = False
        except Exception:
            logger.exception("Failed to load BM25 corpus from ChromaDB")

    # ------------------------------------------------------------------
    # BM25 management (call only while holding self._lock)
    # ------------------------------------------------------------------

    def _rebuild_bm25_locked(self) -> None:
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            self._bm25 = None
            return
        if self._bm25_corpus:
            tokenized = [_tokenize(t) for t in self._bm25_corpus]
            self._bm25 = BM25Okapi(tokenized)
        else:
            self._bm25 = None

    def _maybe_rebuild_bm25_locked(self) -> None:
        if self._dirty:
            self._rebuild_bm25_locked()
            self._dirty = False

    # ------------------------------------------------------------------
    # Core upsert
    # ------------------------------------------------------------------

    def _upsert(
        self,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not ids or self._collection is None:
            return

        valid = [(i, t, m) for i, t, m in zip(ids, texts, metadatas) if t.strip()]
        if not valid:
            return
        ids_v, texts_v, metas_v = (list(x) for x in zip(*valid))

        try:
            self._collection.upsert(ids=ids_v, documents=texts_v, metadatas=metas_v)
        except Exception:
            logger.exception("ChromaDB upsert failed")
            return

        with self._lock:
            existing = set(self._bm25_ids)
            for doc_id, text, meta in zip(ids_v, texts_v, metas_v):
                jid = meta.get("job_id", "")
                if jid:
                    self._doc_jobid[doc_id] = jid
                if doc_id not in existing:
                    self._bm25_ids.append(doc_id)
                    self._bm25_corpus.append(text)
                    existing.add(doc_id)
                else:
                    idx = self._bm25_ids.index(doc_id)
                    self._bm25_corpus[idx] = text
            self._dirty = True

    # ------------------------------------------------------------------
    # Public indexing methods
    # ------------------------------------------------------------------

    def index_chunk_analysis(
        self,
        job_id: str,
        source_path: str,
        payload: dict[str, Any],
    ) -> None:
        """Index a chunk_analysis event payload from the video pipeline."""
        if not self._initialized:
            return
        merged = payload.get("merged")
        if not merged:
            return

        chunk_index = int(merged.get("chunk_index", 0))
        start_pts_ms = int(merged.get("start_pts_ms", 0))
        end_pts_ms = int(merged.get("end_pts_ms", 0))

        base_meta: dict[str, Any] = {
            "job_id": job_id,
            "source_path": source_path,
            "chunk_index": chunk_index,
            "start_pts_ms": start_pts_ms,
            "end_pts_ms": end_pts_ms,
            "agent_type": "chunk_analysis",
            "doc_version": _DOC_VERSION,
        }

        ids: list[str] = []
        texts: list[str] = []
        metas: list[dict[str, Any]] = []

        scene_summary = str(merged.get("scene_summary", "")).strip()
        if scene_summary:
            ids.append(_doc_id(job_id, chunk_index, "scene_summary"))
            texts.append(scene_summary)
            metas.append({**base_meta, "content_type": "scene_summary", "severity": ""})

        for i, ev in enumerate(merged.get("main_events", []) or []):
            title = str(ev.get("title", "")).strip()
            detail = str(ev.get("detail", "")).strip()
            text = f"{title}: {detail}" if detail else title
            if text:
                ids.append(_doc_id(job_id, chunk_index, "main_event", i))
                texts.append(text)
                metas.append({**base_meta, "content_type": "main_event", "severity": ""})

        for i, sec in enumerate(merged.get("security", []) or []):
            cat = str(sec.get("category", "")).strip()
            desc = str(sec.get("description", "")).strip()
            severity = str(sec.get("severity", "unknown"))
            text = f"[{cat}] {desc}" if cat else desc
            if text:
                ids.append(_doc_id(job_id, chunk_index, "security", i))
                texts.append(text)
                metas.append({**base_meta, "content_type": "security", "severity": severity})

        for i, log in enumerate(merged.get("logistics", []) or []):
            label = str(log.get("label", "")).strip()
            desc = str(log.get("description", "")).strip()
            text = f"{label}: {desc}" if desc else label
            if text:
                ids.append(_doc_id(job_id, chunk_index, "logistics", i))
                texts.append(text)
                metas.append({**base_meta, "content_type": "logistics", "severity": ""})

        self._upsert(ids, texts, metas)

    def index_agent_result(
        self,
        job_id: str,
        source_path: str,
        agent_kind: str,
        result: dict[str, Any],
    ) -> None:
        """Index a completed job-level agent result."""
        if not self._initialized:
            return
        texts = _flatten_agent_result(agent_kind, result)
        if not texts:
            return

        severity = str(
            result.get("risk_level") or result.get("overall_risk") or result.get("overall_privacy_risk") or ""
        )
        base_meta: dict[str, Any] = {
            "job_id": job_id,
            "source_path": source_path,
            "chunk_index": -1,
            "start_pts_ms": -1,
            "end_pts_ms": -1,
            "agent_type": agent_kind,
            "content_type": "agent_text",
            "severity": severity,
            "doc_version": _DOC_VERSION,
        }

        ids = [_doc_id(job_id, "agent", agent_kind, i) for i in range(len(texts))]
        metas = [dict(base_meta) for _ in texts]
        self._upsert(ids, texts, metas)

    # ------------------------------------------------------------------
    # Retrieval helpers (called from SearchRetriever)
    # ------------------------------------------------------------------

    def vector_search(
        self,
        query: str,
        n_results: int = 20,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Synchronous vector similarity search via ChromaDB."""
        if self._collection is None:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        count = self._collection.count()
        if count == 0:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        n = min(n_results, count)
        kwargs: dict[str, Any] = {
            "query_texts": [query],
            "n_results": n,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where
        try:
            return self._collection.query(**kwargs)
        except Exception:
            logger.exception("ChromaDB query failed")
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def bm25_search(
        self,
        query: str,
        n_results: int = 20,
        job_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """
        Synchronous BM25 keyword search.
        Returns list of (doc_id, score) sorted descending.
        """
        with self._lock:
            self._maybe_rebuild_bm25_locked()
            bm25 = self._bm25
            ids = list(self._bm25_ids)
            doc_jobid = dict(self._doc_jobid)

        if bm25 is None or not ids:
            return []

        tokens = _tokenize(query)
        try:
            scores = bm25.get_scores(tokens)
        except Exception:
            logger.exception("BM25 scoring failed")
            return []

        scored: list[tuple[str, float]] = []
        for doc_id, score in zip(ids, scores):
            if job_ids and doc_jobid.get(doc_id) not in job_ids:
                continue
            scored.append((doc_id, float(score)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n_results]

    def get_doc_texts(self, doc_ids: list[str]) -> dict[str, str]:
        """Fetch texts for doc IDs not already in vector search results."""
        if not doc_ids or self._collection is None:
            return {}
        try:
            result = self._collection.get(ids=doc_ids, include=["documents", "metadatas"])
            out: dict[str, str] = {}
            for did, doc in zip(result.get("ids", []), result.get("documents", []) or []):
                out[did] = doc or ""
            return out
        except Exception:
            logger.exception("ChromaDB get failed")
            return {}

    def get_doc_metas(self, doc_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch metadata for given doc IDs."""
        if not doc_ids or self._collection is None:
            return {}
        try:
            result = self._collection.get(ids=doc_ids, include=["metadatas"])
            out: dict[str, dict[str, Any]] = {}
            for did, meta in zip(result.get("ids", []), result.get("metadatas", []) or []):
                out[did] = meta or {}
            return out
        except Exception:
            logger.exception("ChromaDB get_meta failed")
            return {}

    def get_indexed_job_ids(self) -> set[str]:
        """Return set of job IDs that have at least one document indexed."""
        with self._lock:
            return set(self._doc_jobid.values())

    def get_status(self) -> dict[str, Any]:
        count = self._collection.count() if self._collection else 0
        return {
            "enabled": self._initialized,
            "total_documents": count,
            "collection_name": _COLLECTION_NAME,
            "embedding_model": self._embedding_model,
        }
