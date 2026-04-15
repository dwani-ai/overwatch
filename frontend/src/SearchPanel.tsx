import { useCallback, useEffect, useRef, useState } from "react";
import type { SearchIndexStatus, SearchResult } from "./api";
import { getSearchIndexStatus, searchEvents } from "./api";

const AGENT_TYPE_LABELS: Record<string, string> = {
  chunk_analysis: "Chunk",
  synthesis: "Synthesis",
  risk_review: "Risk",
  incident_brief: "Incident",
  compliance_brief: "Compliance",
  loss_prevention: "Loss Prevention",
  perimeter_chain: "Perimeter",
  privacy_review: "Privacy",
};

const CONTENT_TYPE_LABELS: Record<string, string> = {
  scene_summary: "Scene",
  main_event: "Event",
  security: "Security",
  logistics: "Logistics",
  agent_text: "Agent",
};

const SEVERITY_COLORS: Record<string, string> = {
  high: "#dc2626",
  medium: "#d97706",
  low: "#16a34a",
  info: "#2563eb",
  unknown: "#6b7280",
};

function formatTimestamp(ms: number | null): string {
  if (ms === null || ms < 0) return "";
  const totalSec = Math.floor(ms / 1000);
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return `${min}:${sec.toString().padStart(2, "0")}`;
}

function SourceChip({ result }: { result: SearchResult }) {
  const { source } = result;
  const ts = source.start_pts_ms !== null ? formatTimestamp(source.start_pts_ms) : null;
  const agentLabel = AGENT_TYPE_LABELS[source.agent_type] ?? source.agent_type;
  const contentLabel = CONTENT_TYPE_LABELS[source.content_type] ?? source.content_type;
  const sevColor = source.severity ? SEVERITY_COLORS[source.severity] ?? SEVERITY_COLORS.unknown : null;

  return (
    <div className="search-source">
      <span className="search-source-file" title={source.source_path}>
        {source.video_filename || source.job_id.slice(0, 8)}
      </span>
      {ts && (
        <span className="search-source-ts" title="Video timestamp">
          ⏱ {ts}
        </span>
      )}
      <span className="search-source-tag">{agentLabel}</span>
      <span className="search-source-tag search-source-tag-content">{contentLabel}</span>
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

function ResultCard({ result, index }: { result: SearchResult; index: number }) {
  return (
    <div className="search-result-card">
      <div className="search-result-rank">#{index + 1}</div>
      <p className="search-result-text">{result.text}</p>
      <SourceChip result={result} />
    </div>
  );
}

export default function SearchPanel() {
  const [query, setQuery] = useState("");
  const [synthesize, setSynthesize] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [answer, setAnswer] = useState<string | null>(null);
  const [totalFound, setTotalFound] = useState<number | null>(null);
  const [indexStatus, setIndexStatus] = useState<SearchIndexStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getSearchIndexStatus()
      .then(setIndexStatus)
      .catch((e) => setStatusError(e instanceof Error ? e.message : String(e)));
  }, []);

  const onSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    setResults(null);
    setAnswer(null);
    setTotalFound(null);
    try {
      const resp = await searchEvents({ query: q, limit: 15, synthesize_answer: synthesize });
      setResults(resp.results);
      setAnswer(resp.answer ?? null);
      setTotalFound(resp.total_found);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [query, synthesize]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") onSearch();
  };

  return (
    <section className="card search-panel">
      <h2>Search Events</h2>
      <p className="muted small">
        Hybrid semantic + keyword search across all video analysis — chunk observations, security
        alerts, agent reports, and more.
      </p>

      {indexStatus && (
        <div className="search-index-status">
          <span className={`search-index-dot ${indexStatus.enabled ? "dot-ok" : "dot-off"}`} />
          {indexStatus.enabled ? (
            <span className="muted small">
              {indexStatus.total_documents.toLocaleString()} documents indexed ·{" "}
              {indexStatus.embedding_model}
            </span>
          ) : (
            <span className="muted small error">Search index unavailable</span>
          )}
        </div>
      )}
      {statusError && <p className="error small">{statusError}</p>}

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

      <label className="search-synthesize-label">
        <input
          type="checkbox"
          checked={synthesize}
          onChange={(e) => setSynthesize(e.target.checked)}
          disabled={loading}
        />
        <span className="small">Generate AI answer from results</span>
      </label>

      {error && <p className="error">{error}</p>}

      {totalFound !== null && results !== null && (
        <p className="muted small search-total">
          {totalFound} candidates found · showing {results.length}
        </p>
      )}

      {answer && (
        <div className="search-answer">
          <h4>AI Answer</h4>
          <p>{answer}</p>
        </div>
      )}

      {results !== null && results.length === 0 && (
        <p className="muted small">No results found for this query.</p>
      )}

      {results !== null && results.length > 0 && (
        <div className="search-results">
          {results.map((r, i) => (
            <ResultCard key={`${r.source.job_id}-${i}`} result={r} index={i} />
          ))}
        </div>
      )}
    </section>
  );
}
