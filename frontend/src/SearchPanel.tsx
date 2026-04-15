import { useCallback, useEffect, useRef, useState } from "react";
import type { JobRecord, JobSearchStatus, SearchIndexStatus, SearchResult } from "./api";
import { getJobSearchStatus, getSearchIndexStatus, reindexJobSearch, searchByImage, searchEvents } from "./api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const AGENT_TYPE_OPTIONS = [
  { value: "chunk_analysis", label: "Chunk" },
  { value: "synthesis", label: "Synthesis" },
  { value: "risk_review", label: "Risk" },
  { value: "incident_brief", label: "Incident" },
  { value: "compliance_brief", label: "Compliance" },
  { value: "loss_prevention", label: "Loss Prevention" },
  { value: "perimeter_chain", label: "Perimeter" },
  { value: "privacy_review", label: "Privacy" },
  { value: "frame_embed", label: "Frames" },
] as const;

const AGENT_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  AGENT_TYPE_OPTIONS.map((o) => [o.value, o.label]),
);

const CONTENT_TYPE_LABELS: Record<string, string> = {
  scene_summary: "Scene",
  observation: "Observation",
  main_event: "Event",
  security: "Security",
  logistics: "Logistics",
  agent_text: "Agent",
  frame: "Frame",
};

const SEVERITY_COLORS: Record<string, string> = {
  high: "#dc2626",
  medium: "#d97706",
  low: "#16a34a",
  info: "#2563eb",
  unknown: "#6b7280",
};

const SEVERITY_OPTIONS = ["high", "medium", "low", "info"] as const;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTimestamp(ms: number | null): string {
  if (ms === null || ms < 0) return "";
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

function basename(path: string): string {
  const s = path.replace(/\\/g, "/");
  const i = s.lastIndexOf("/");
  return i >= 0 ? s.slice(i + 1) : s;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SourceChip({
  result,
  onNavigateToJob,
}: {
  result: SearchResult;
  onNavigateToJob?: (jobId: string) => void;
}) {
  const { source } = result;
  const isFrame = source.content_type === "frame" || source.agent_type === "frame_embed";
  const ts = source.start_pts_ms !== null ? formatTimestamp(source.start_pts_ms) : null;
  const agentLabel = AGENT_TYPE_LABELS[source.agent_type] ?? source.agent_type;
  const contentLabel = CONTENT_TYPE_LABELS[source.content_type] ?? source.content_type;
  const sevColor =
    source.severity ? (SEVERITY_COLORS[source.severity] ?? SEVERITY_COLORS.unknown) : null;

  const handleFileClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onNavigateToJob?.(source.job_id);
  };

  return (
    <div className={`search-source${isFrame ? " search-source-frame" : ""}`}>
      <button
        type="button"
        className="search-source-file"
        title={`${source.source_path}\nClick to open job`}
        onClick={handleFileClick}
      >
        {isFrame && <span className="frame-icon" aria-label="frame">🎞</span>}
        {source.video_filename || source.job_id.slice(0, 8)}
      </button>
      {ts && (
        <span className="search-source-ts" title="Video timestamp">
          ⏱ {ts}
        </span>
      )}
      {!isFrame && <span className="search-source-tag">{agentLabel}</span>}
      <span className={`search-source-tag${isFrame ? " search-source-tag-frame" : " search-source-tag-content"}`}>
        {contentLabel}
      </span>
      {source.severity && sevColor && (
        <span className="search-source-tag" style={{ color: sevColor, borderColor: sevColor }}>
          {source.severity}
        </span>
      )}
      <span className="search-source-score" title="RRF relevance score">
        {(result.score * 100).toFixed(1)}%
      </span>
    </div>
  );
}

function ResultCard({
  result,
  index,
  onNavigateToJob,
}: {
  result: SearchResult;
  index: number;
  onNavigateToJob?: (jobId: string) => void;
}) {
  const isFrame = result.source.content_type === "frame" || result.source.agent_type === "frame_embed";
  return (
    <div className={`search-result-card${isFrame ? " search-result-card-frame" : ""}`}>
      <div className="search-result-rank">#{index + 1}</div>
      <p className="search-result-text">{result.text}</p>
      <SourceChip result={result} onNavigateToJob={onNavigateToJob} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// JobSearchBadge (used externally on job detail cards)
// ---------------------------------------------------------------------------

export function JobSearchBadge({
  jobId,
  onReindex,
}: {
  jobId: string;
  onReindex?: () => void;
}) {
  const [status, setStatus] = useState<JobSearchStatus | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [reindexErr, setReindexErr] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getJobSearchStatus(jobId)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [jobId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleReindex = async () => {
    setReindexing(true);
    setReindexErr(null);
    try {
      await reindexJobSearch(jobId);
      await refresh();
      onReindex?.();
    } catch (e) {
      setReindexErr(e instanceof Error ? e.message : String(e));
    } finally {
      setReindexing(false);
    }
  };

  if (!status) return null;

  return (
    <div className="job-search-badge">
      <span
        className={`search-index-dot ${status.search_enabled && status.indexed_doc_count > 0 ? "dot-ok" : "dot-off"}`}
      />
      <span className="muted small">
        {status.search_enabled
          ? `${status.indexed_doc_count} docs`
          : "search unavailable"}
      </span>
      {status.frame_search_enabled && (
        <span className="muted small" title="SigLIP frame embeddings indexed">
          · 🎞 {status.indexed_frame_count} frames
        </span>
      )}
      {status.search_enabled && (
        <button
          type="button"
          className="linkish small"
          onClick={handleReindex}
          disabled={reindexing}
          title="Re-index this job (text + frames)"
        >
          {reindexing ? "Reindexing…" : "Reindex"}
        </button>
      )}
      {reindexErr && <span className="error small">{reindexErr}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main SearchPanel
// ---------------------------------------------------------------------------

export type SearchPanelProps = {
  /** Pre-scope search to a specific job ID (e.g. set by "Search this job" button). */
  scopeJobId?: string | null;
  /** Called when scope is cleared by the user. */
  onClearScope?: () => void;
  /** Recent jobs list passed from App for the job filter picker. */
  recentJobs?: JobRecord[];
  /** Called when user clicks a source chip to navigate to a job. */
  onNavigateToJob?: (jobId: string) => void;
};

export default function SearchPanel({
  scopeJobId,
  onClearScope,
  recentJobs = [],
  onNavigateToJob,
}: SearchPanelProps) {
  const [query, setQuery] = useState("");
  const [synthesize, setSynthesize] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [totalFound, setTotalFound] = useState<number | null>(null);
  const [indexStatus, setIndexStatus] = useState<SearchIndexStatus | null>(null);
  const [searchMode, setSearchMode] = useState<"text" | "image">("text");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imageDragOver, setImageDragOver] = useState(false);

  // Filters
  const [showFilters, setShowFilters] = useState(false);
  const [filterJobId, setFilterJobId] = useState<string>("");
  const [filterAgentTypes, setFilterAgentTypes] = useState<Set<string>>(new Set());
  const [filterSeverity, setFilterSeverity] = useState<string>("");
  const [includeFrames, setIncludeFrames] = useState(true);

  const inputRef = useRef<HTMLInputElement>(null);

  // When scopeJobId prop changes (from "Search this job" button), apply it as the job filter
  useEffect(() => {
    if (scopeJobId) {
      setFilterJobId(scopeJobId);
      setShowFilters(true);
    }
  }, [scopeJobId]);

  useEffect(() => {
    getSearchIndexStatus()
      .then(setIndexStatus)
      .catch(() => setIndexStatus(null));
  }, []);

  const toggleAgentType = (type: string) => {
    setFilterAgentTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  const hasFilters =
    filterJobId !== "" || filterAgentTypes.size > 0 || filterSeverity !== "";

  const clearFilters = () => {
    setFilterJobId("");
    setFilterAgentTypes(new Set());
    setFilterSeverity("");
    onClearScope?.();
  };

  const onSearch = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResults(null);
    setAnswer(null);
    setTotalFound(null);
    try {
      if (searchMode === "image") {
        if (!imageFile) return;
        const resp = await searchByImage(imageFile, {
          limit: 15,
          jobIds: filterJobId ? [filterJobId] : undefined,
        });
        setResults(resp.results);
        setTotalFound(resp.total_found);
      } else {
        const q = query.trim();
        if (!q) return;
        const resp = await searchEvents({
          query: q,
          limit: 15,
          synthesize_answer: synthesize,
          include_frames: includeFrames,
          job_ids: filterJobId ? [filterJobId] : null,
          agent_types: filterAgentTypes.size > 0 ? Array.from(filterAgentTypes) : null,
          severity: filterSeverity || null,
        });
        setResults(resp.results);
        setAnswer(resp.answer ?? null);
        setTotalFound(resp.total_found);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [searchMode, query, imageFile, synthesize, includeFrames, filterJobId, filterAgentTypes, filterSeverity]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") onSearch();
  };

  // Resolve the scoped video filename for display
  const scopedJobName = filterJobId
    ? (recentJobs.find((j) => j.id === filterJobId)?.source_path
        ? basename(recentJobs.find((j) => j.id === filterJobId)!.source_path)
        : filterJobId.slice(0, 8))
    : null;

  const frameSearchAvailable = !!(indexStatus?.frame_search_enabled && indexStatus.total_frames > 0);

  return (
    <section className="card search-panel" id="search-panel">
      <h2>Search Events</h2>
      <p className="muted small">
        Hybrid semantic + keyword search across all video analysis — observations, security alerts,
        agent reports, and more.
      </p>

      {indexStatus && (
        <div className="search-index-status">
          <span className={`search-index-dot ${indexStatus.enabled ? "dot-ok" : "dot-off"}`} />
          {indexStatus.enabled ? (
            <span className="muted small">
              {indexStatus.total_documents.toLocaleString()} text docs
              {indexStatus.frame_search_enabled ? (
                indexStatus.total_frames > 0 ? (
                  <> · <span className="frame-index-ready" title="SigLIP-ViT cross-modal frame search ready">🎞 {indexStatus.total_frames.toLocaleString()} frames indexed</span></>
                ) : (
                  <> · <span className="muted" title="Frame indexer is loading or no jobs completed yet">🎞 frames indexing…</span></>
                )
              ) : (
                <> · <span className="muted" title="Set FRAME_SEARCH_ENABLED=true and ensure transformers is installed">🎞 frame search off</span></>
              )}
            </span>
          ) : (
            <span className="error small">Search index unavailable</span>
          )}
        </div>
      )}

      {/* Active scope banner */}
      {scopedJobName && (
        <div className="search-scope-banner">
          <span className="search-source-tag search-source-tag-content">scoped to:</span>
          <strong className="search-source-file">{scopedJobName}</strong>
          <button
            type="button"
            className="linkish small"
            onClick={clearFilters}
            title="Clear scope and search all videos"
          >
            × clear
          </button>
        </div>
      )}

      {/* Mode tabs */}
      <div className="search-mode-tabs">
        <button
          type="button"
          className={`search-mode-tab${searchMode === "text" ? " active" : ""}`}
          onClick={() => setSearchMode("text")}
        >
          Text search
        </button>
        <button
          type="button"
          className={`search-mode-tab${searchMode === "image" ? " active" : ""}${!frameSearchAvailable ? " search-option-unavailable" : ""}`}
          onClick={() => frameSearchAvailable && setSearchMode("image")}
          title={frameSearchAvailable ? "Find frames visually similar to an uploaded image" : "Frame search unavailable — frames still indexing"}
        >
          🖼 Image search
        </button>
      </div>

      {searchMode === "text" ? (
        <div className="search-input-row">
          <input
            ref={inputRef}
            className="search-input"
            type="text"
            placeholder="e.g. forklift near dock, unauthorized access, fire door open…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={loading}
          />
          <button
            type="button"
            className="btn"
            onClick={onSearch}
            disabled={loading || !query.trim()}
          >
            {loading ? "Searching…" : "Search"}
          </button>
        </div>
      ) : (
        <div className="search-image-zone">
          <div
            className={`image-dropzone${imageDragOver ? " drag-over" : ""}${imageFile ? " has-file" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setImageDragOver(true); }}
            onDragLeave={() => setImageDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setImageDragOver(false);
              const f = e.dataTransfer.files[0];
              if (f && f.type.startsWith("image/")) setImageFile(f);
            }}
            onClick={() => document.getElementById("image-file-input")?.click()}
          >
            {imageFile ? (
              <span className="dropzone-filename">📷 {imageFile.name}</span>
            ) : (
              <span className="dropzone-hint">Drop an image here or click to pick one</span>
            )}
          </div>
          <input
            id="image-file-input"
            type="file"
            accept="image/*"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) setImageFile(f);
            }}
          />
          <button
            type="button"
            className="btn"
            onClick={onSearch}
            disabled={loading || !imageFile}
          >
            {loading ? "Searching…" : "Find similar frames"}
          </button>
          {imageFile && (
            <button type="button" className="linkish small" onClick={() => setImageFile(null)}>
              × clear
            </button>
          )}
        </div>
      )}

      {searchMode === "image" && (
        <p className="muted small" style={{ marginTop: "0.4rem" }}>
          Finds video frames visually similar to your image using SigLIP embeddings — no text required.
        </p>
      )}

      <div className="search-options-row">
        {searchMode === "text" && (
          <label className="search-synthesize-label">
            <input
              type="checkbox"
              checked={synthesize}
              onChange={(e) => setSynthesize(e.target.checked)}
              disabled={loading}
            />
            <span className="small">AI answer</span>
          </label>
        )}

        <label
          className={`search-synthesize-label${!frameSearchAvailable ? " search-option-unavailable" : ""}`}
          title={
            frameSearchAvailable
              ? "Include SigLIP cross-modal frame embedding results alongside text analysis results"
              : "Frame search unavailable — frames are still indexing or FRAME_SEARCH_ENABLED=false"
          }
        >
          <input
            type="checkbox"
            checked={includeFrames && frameSearchAvailable}
            onChange={(e) => setIncludeFrames(e.target.checked)}
            disabled={loading || !frameSearchAvailable}
          />
          <span className="small">🎞 Frame search</span>
          {frameSearchAvailable && (
            <span className="muted small" style={{ marginLeft: "0.25rem" }}>
              ({(indexStatus?.total_frames ?? 0).toLocaleString()})
            </span>
          )}
        </label>

        <button
          type="button"
          className={`linkish small search-filter-toggle${showFilters ? " active" : ""}${hasFilters ? " has-filters" : ""}`}
          onClick={() => setShowFilters((v) => !v)}
        >
          {hasFilters ? `Filters (${[filterJobId, filterSeverity, ...filterAgentTypes].filter(Boolean).length})` : "Filters"}
        </button>
      </div>

      {showFilters && (
        <div className="search-filters">
          {/* Job picker */}
          <div className="search-filter-row">
            <label className="search-filter-label small">Video</label>
            <select
              className="search-filter-select"
              value={filterJobId}
              onChange={(e) => {
                setFilterJobId(e.target.value);
                if (!e.target.value) onClearScope?.();
              }}
            >
              <option value="">All videos</option>
              {recentJobs.map((j) => (
                <option key={j.id} value={j.id}>
                  {basename(j.source_path)} · {j.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </div>

          {/* Severity */}
          <div className="search-filter-row">
            <label className="search-filter-label small">Severity</label>
            <select
              className="search-filter-select"
              value={filterSeverity}
              onChange={(e) => setFilterSeverity(e.target.value)}
            >
              <option value="">All</option>
              {SEVERITY_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s.charAt(0).toUpperCase() + s.slice(1)}
                </option>
              ))}
            </select>
          </div>

          {/* Agent type pills */}
          <div className="search-filter-row search-filter-row-pills">
            <label className="search-filter-label small">Sources</label>
            <div className="search-agent-pills">
              {AGENT_TYPE_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={`search-agent-pill${filterAgentTypes.has(opt.value) ? " selected" : ""}`}
                  onClick={() => toggleAgentType(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {hasFilters && (
            <button type="button" className="linkish small" onClick={clearFilters}>
              Clear all filters
            </button>
          )}
        </div>
      )}

      {error && <p className="error">{error}</p>}

      {totalFound !== null && results !== null && (
        <p className="muted small search-total">
          {totalFound} candidates · showing {results.length}
          {results.some(r => r.source.content_type === "frame") && (
            <span title="Includes SigLIP cross-modal frame results">
              {" "}· 🎞 {results.filter(r => r.source.content_type === "frame").length} frame
              {results.filter(r => r.source.content_type === "frame").length !== 1 ? "s" : ""}
            </span>
          )}
        </p>
      )}

      {answer && (
        <div className="search-answer">
          <h4>AI Answer</h4>
          <p>{answer}</p>
        </div>
      )}

      {results !== null && results.length === 0 && (
        <p className="muted small">No results found.</p>
      )}

      {results !== null && results.length > 0 && (
        <div className="search-results">
          {results.map((r, i) => (
            <ResultCard
              key={`${r.source.job_id}-${i}`}
              result={r}
              index={i}
              onNavigateToJob={onNavigateToJob}
            />
          ))}
          {results.some(r => r.source.content_type === "frame") && (
            <p className="muted small search-frame-hint">
              🎞 Frame results are video keyframes matched visually to your query using SigLIP-ViT embeddings. Click the filename to open the job and seek to that timestamp.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
