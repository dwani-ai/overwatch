from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from overwatch.config import Settings
from overwatch.search.indexer import SearchIndexer
from overwatch.search.models import SearchQuery, SearchResponse, SearchResult, SearchSource
from overwatch.vllm_client import chat_completion, extract_assistant_text

logger = logging.getLogger(__name__)

_RRF_K = 60
_MAX_CONTEXT_CHARS = 12_000
_MAX_ANSWER_TOKENS = 512


def _rrf(
    vec_ranking: list[str],
    bm25_ranking: list[str],
    k: int = _RRF_K,
) -> dict[str, float]:
    """Reciprocal Rank Fusion — merge two ranked lists into a single score map."""
    scores: dict[str, float] = {}
    for rank, doc_id in enumerate(vec_ranking):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, doc_id in enumerate(bm25_ranking):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _make_source(meta: dict[str, Any]) -> SearchSource:
    sp = meta.get("source_path", "")
    ci = meta.get("chunk_index")
    spm = meta.get("start_pts_ms")
    epm = meta.get("end_pts_ms")
    sev = meta.get("severity") or None
    return SearchSource(
        job_id=meta.get("job_id", ""),
        source_path=sp,
        video_filename=Path(sp).name if sp else "",
        chunk_index=int(ci) if ci is not None and int(ci) >= 0 else None,
        start_pts_ms=int(spm) if spm is not None and int(spm) >= 0 else None,
        end_pts_ms=int(epm) if epm is not None and int(epm) >= 0 else None,
        agent_type=meta.get("agent_type", ""),
        content_type=meta.get("content_type", ""),
        severity=sev if sev and str(sev).strip() else None,
    )


class SearchRetriever:
    """
    Hybrid retriever: ChromaDB vector search + BM25 keyword search, fused with RRF.
    Optionally synthesizes a short LLM answer from the top results.
    """

    def __init__(self, indexer: SearchIndexer, settings: Settings) -> None:
        self._indexer = indexer
        self._settings = settings

    async def search(self, query: SearchQuery) -> SearchResponse:
        n_candidates = min(max(query.limit * 4, 30), 80)

        # Build ChromaDB where filter
        where: dict[str, Any] | None = None
        if query.job_ids:
            if len(query.job_ids) == 1:
                where = {"job_id": query.job_ids[0]}
            else:
                where = {"job_id": {"$in": list(query.job_ids)}}

        # 1. Vector search (async → thread)
        vr = await asyncio.to_thread(
            self._indexer.vector_search,
            query.query,
            n_candidates,
            where,
        )
        vec_ids: list[str] = (vr.get("ids") or [[]])[0] or []
        vec_docs: list[str] = (vr.get("documents") or [[]])[0] or []
        vec_metas: list[dict] = (vr.get("metadatas") or [[]])[0] or []
        vec_dists: list[float] = (vr.get("distances") or [[]])[0] or []

        # Build id → (text, meta, dist) map from vector results
        doc_map: dict[str, tuple[str, dict[str, Any], float]] = {}
        for did, doc, meta, dist in zip(vec_ids, vec_docs, vec_metas, vec_dists):
            doc_map[did] = (doc or "", meta or {}, float(dist))

        # 2. BM25 search (async → thread)
        bm25_results: list[tuple[str, float]] = await asyncio.to_thread(
            self._indexer.bm25_search,
            query.query,
            n_candidates,
            list(query.job_ids) if query.job_ids else None,
        )
        bm25_ranking = [did for did, _ in bm25_results]

        # Fetch texts/metas for BM25-only hits not already in vector results
        bm25_only_ids = [did for did, _ in bm25_results if did not in doc_map]
        if bm25_only_ids:
            extra_texts = await asyncio.to_thread(
                self._indexer.get_doc_texts, bm25_only_ids
            )
            extra_metas = await asyncio.to_thread(
                self._indexer.get_doc_metas, bm25_only_ids
            )
            for did in bm25_only_ids:
                doc_map[did] = (extra_texts.get(did, ""), extra_metas.get(did, {}), 1.0)

        # 3. RRF fusion
        rrf_scores = _rrf(vec_ids, bm25_ranking)

        # 4. Filter + rank
        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        results: list[SearchResult] = []
        for did, rrf_score in ranked:
            if len(results) >= query.limit:
                break
            text, meta, _ = doc_map.get(did, ("", {}, 1.0))
            if not text or not meta:
                continue
            # Apply post-hoc filters (for BM25-only hits that bypass ChromaDB where)
            if query.job_ids and meta.get("job_id") not in query.job_ids:
                continue
            if query.agent_types and meta.get("agent_type") not in query.agent_types:
                continue
            if query.severity and meta.get("severity") != query.severity:
                continue
            results.append(SearchResult(text=text, score=rrf_score, source=_make_source(meta)))

        # 5. Optional LLM answer synthesis
        answer: str | None = None
        if query.synthesize_answer and results and self._settings.vllm_base_url.strip():
            answer = await self._synthesize_answer(query.query, results)

        return SearchResponse(
            query=query.query,
            answer=answer,
            results=results,
            total_found=len(rrf_scores),
        )

    async def _synthesize_answer(self, query: str, results: list[SearchResult]) -> str | None:
        context = ""
        for r in results:
            label = r.source.video_filename or r.source.job_id
            if r.source.start_pts_ms is not None:
                secs = r.source.start_pts_ms // 1000
                ts = f" @ {secs // 60}:{secs % 60:02d}"
            else:
                ts = ""
            chunk = f"[{label}{ts} — {r.source.content_type}]\n{r.text}\n\n"
            if len(context) + len(chunk) > _MAX_CONTEXT_CHARS:
                break
            context += chunk

        prompt = (
            "You are an Overwatch security analytics assistant. "
            "Answer the following question using ONLY the provided source excerpts. "
            "Be concise (2-4 sentences). Cite video filenames and timestamps where available.\n\n"
            f"Sources:\n{context.strip()}\n\n"
            f"Question: {query}\n\nAnswer:"
        )

        try:
            res = await chat_completion(
                self._settings.vllm_base_url.strip(),
                model=self._settings.vllm_model,
                messages=[{"role": "user", "content": prompt}],
                api_key=self._settings.vllm_api_key,
                timeout_sec=min(self._settings.vllm_chat_timeout_sec, 60.0),
                max_tokens=_MAX_ANSWER_TOKENS,
                temperature=0.1,
            )
            return extract_assistant_text(res.data) or None
        except Exception:
            logger.warning("Search answer synthesis failed", exc_info=True)
            return None
