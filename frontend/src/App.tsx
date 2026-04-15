import { useCallback, useEffect, useState, type ReactNode } from "react";
import SearchPanel, { JobSearchBadge } from "./SearchPanel";
import type {
  AgentKind,
  AgentRunPublic,
  ComplianceBriefResult,
  IncidentBriefResult,
  IndustryPack,
  JobRecord,
  LossPreventionResult,
  PerimeterChainResult,
  PrivacyReviewResult,
  RiskReviewResult,
  SynthesisResult,
} from "./api";
import {
  createAgentRun,
  CROSS_INDUSTRY_AGENT_PIPELINE,
  DEFAULT_AGENT_PIPELINE,
  deleteJob,
  getJob,
  getSummary,
  INDUSTRY_PACK_OPTIONS,
  listJobAgentRuns,
  listJobs,
  pollAgentOrchestration,
  pollAgentRun,
  startAgentOrchestration,
  startIndustryOrchestration,
  uploadVideo,
} from "./api";
import "./App.css";

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

function basename(path: string): string {
  const s = path.replace(/\\/g, "/");
  const i = s.lastIndexOf("/");
  return i >= 0 ? s.slice(i + 1) : s;
}

export default function App() {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<JobRecord | null>(null);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [recentJobs, setRecentJobs] = useState<JobRecord[]>([]);
  const [recentJobsLoading, setRecentJobsLoading] = useState(false);
  const [recentJobsError, setRecentJobsError] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [phase, setPhase] = useState<string>("");
  const [searchScopeJobId, setSearchScopeJobId] = useState<string | null>(null);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const refreshRecentJobs = useCallback(async () => {
    setRecentJobsError(null);
    setRecentJobsLoading(true);
    try {
      const rows = await listJobs(40);
      setRecentJobs(rows);
    } catch (e) {
      setRecentJobsError(e instanceof Error ? e.message : String(e));
    } finally {
      setRecentJobsLoading(false);
    }
  }, []);

  const searchThisJob = useCallback((jobId: string) => {
    setSearchScopeJobId(jobId);
    setTimeout(() => {
      document.getElementById("search-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  }, []);

  const onDeleteJob = useCallback(async (jobId: string) => {
    if (!confirm("Delete this job and all its data? This cannot be undone.")) return;
    setDeleting(true);
    setDeleteErr(null);
    try {
      await deleteJob(jobId);
      setJob(null);
      setSummary(null);
      await refreshRecentJobs();
    } catch (e) {
      setDeleteErr(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }, [refreshRecentJobs]);

  useEffect(() => {
    refreshRecentJobs();
  }, [refreshRecentJobs]);

  useEffect(() => {
    const busy = recentJobs.some((j) => j.status === "pending" || j.status === "processing");
    if (!busy) return;
    const t = setInterval(() => {
      refreshRecentJobs();
    }, 2500);
    return () => clearInterval(t);
  }, [recentJobs, refreshRecentJobs]);

  const openJob = useCallback(async (jobId: string) => {
    setErr(null);
    setPhase("Loading job…");
    try {
      const j = await getJob(jobId);
      setJob(j);
      if (j.status === "completed") {
        if (j.summary) {
          setSummary(j.summary);
        } else {
          try {
            setSummary(await getSummary(jobId));
          } catch {
            setSummary(null);
          }
        }
      } else {
        setSummary(null);
      }
      setPhase("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPhase("");
    }
  }, []);

  const pollUntilDone = useCallback(async (jobId: string) => {
    setPhase("Processing…");
    for (let i = 0; i < 900; i++) {
      const j = await getJob(jobId);
      setJob(j);
      if (j.status === "completed" || j.status === "failed") {
        if (j.status === "completed") {
          setPhase("Loading summary…");
          for (let k = 0; k < 30; k++) {
            try {
              const s = await getSummary(jobId);
              setSummary(s);
              setPhase("");
              await refreshRecentJobs();
              return;
            } catch {
              await sleep(1000);
            }
          }
          setSummary(null);
          setPhase("Completed (summary not ready yet — refresh job in API)");
        } else {
          setPhase("");
        }
        await refreshRecentJobs();
        return;
      }
      await sleep(2000);
    }
    setPhase("Timed out waiting for job");
  }, [refreshRecentJobs]);

  const onUpload = async () => {
    if (!file) return;
    setErr(null);
    setSummary(null);
    setJob(null);
    setBusy(true);
    setPhase("Uploading…");
    try {
      const j = await uploadVideo(file);
      setJob(j);
      await pollUntilDone(j.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPhase("");
    } finally {
      setBusy(false);
      await refreshRecentJobs();
    }
  };

  return (
    <div className="app">
      <header className="header">
        <h1>Overwatch</h1>
        <p className="tag">Upload a video — results appear when the job finishes.</p>
      </header>

      <section className="card">
        <h2>Recent jobs</h2>
        <p className="muted small">
          Past uploads stay in the API — select one to view chunk results and agent history.
        </p>
        <div className="recent-jobs-toolbar">
          <button
            type="button"
            className="linkish"
            disabled={recentJobsLoading}
            onClick={() => refreshRecentJobs()}
          >
            {recentJobsLoading ? "Refreshing…" : "Refresh list"}
          </button>
        </div>
        {recentJobsError ? <p className="error small">{recentJobsError}</p> : null}
        {recentJobsLoading && recentJobs.length === 0 ? (
          <p className="phase small">Loading recent jobs…</p>
        ) : null}
        {!recentJobsLoading && !recentJobsError && recentJobs.length === 0 ? (
          <p className="muted small">No jobs yet.</p>
        ) : null}
        {recentJobs.length > 0 ? (
          <ul className="recent-jobs">
            {recentJobs.map((j) => (
              <li key={j.id}>
                <button
                  type="button"
                  className={`recent-job-btn${job?.id === j.id ? " selected" : ""}`}
                  onClick={() => openJob(j.id)}
                >
                  <span className={`recent-job-status st-${j.status}`}>{j.status}</span>
                  <span className="recent-job-name">{basename(j.source_path)}</span>
                  <span className="mono small recent-job-id">{j.id.slice(0, 8)}…</span>
                  <span className="muted small recent-job-time">{j.created_at}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="card">
        <label className="label">Video file</label>
        <input
          type="file"
          accept="video/mp4,video/webm,video/quicktime,.mkv,.avi,.m4v"
          disabled={busy}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button type="button" className="btn" disabled={!file || busy} onClick={onUpload}>
          {busy ? "Working…" : "Upload & analyse"}
        </button>
        {phase ? <p className="phase">{phase}</p> : null}
        {err ? <p className="error">{err}</p> : null}
      </section>

      <SearchPanel
        scopeJobId={searchScopeJobId}
        onClearScope={() => setSearchScopeJobId(null)}
        recentJobs={recentJobs}
        onNavigateToJob={(jobId) => {
          setSearchScopeJobId(null);
          openJob(jobId);
          setTimeout(() => {
            document.getElementById("job-detail")?.scrollIntoView({ behavior: "smooth", block: "start" });
          }, 80);
        }}
      />

      {job ? (
        <section className="card" id="job-detail">
          <div className="job-detail-header">
            <h2>Job</h2>
            <div className="job-detail-actions">
              <button
                type="button"
                className="linkish small"
                onClick={() => searchThisJob(job.id)}
                title="Search within this video only"
              >
                Search this job
              </button>
              <button
                type="button"
                className="linkish small danger"
                onClick={() => onDeleteJob(job.id)}
                disabled={deleting}
                title="Delete this job and all its data"
              >
                {deleting ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
          {deleteErr && <p className="error small">{deleteErr}</p>}
          <dl className="kv">
            <dt>ID</dt>
            <dd className="mono">{job.id}</dd>
            <dt>Status</dt>
            <dd>{job.status}</dd>
            <dt>Source</dt>
            <dd className="mono small">{job.source_path}</dd>
            {job.error ? (
              <>
                <dt>Error</dt>
                <dd className="error">{job.error}</dd>
              </>
            ) : null}
          </dl>
          {job.status === "completed" && (
            <JobSearchBadge jobId={job.id} />
          )}
        </section>
      ) : null}

      {job?.status === "completed" && !summary ? (
        <section className="card">
          <h2>Results</h2>
          <p className="muted small">
            No summary is stored for this job (for example vLLM was off or the job finished before summaries were
            written). Chunk and agent views need a summary.
          </p>
        </section>
      ) : null}

      {summary ? (
        <SummaryView jobId={job?.status === "completed" ? job.id : null} data={summary} />
      ) : null}
    </div>
  );
}

function SummaryView({ jobId, data }: { jobId: string | null; data: Record<string, unknown> }) {
  const chunks = data.chunk_analyses;
  return (
    <section className="card">
      <h2>Results</h2>
      <p className="muted">
        {String(data.analysed_chunk_count ?? 0)} / {String(data.planned_chunk_count ?? "?")} chunks
      </p>
      {jobId ? <AgentsPanel jobId={jobId} /> : null}
      {Array.isArray(chunks) && chunks.length > 0 ? (
        <ul className="chunk-list">
          {chunks.map((c, i) => (
            <li key={i} className="chunk-item">
              <ChunkCard chunk={c as Record<string, unknown>} index={i} />
            </li>
          ))}
        </ul>
      ) : (
        <pre className="json">{JSON.stringify(data, null, 2)}</pre>
      )}
    </section>
  );
}

function AgentsPanel({ jobId }: { jobId: string }) {
  const [allRuns, setAllRuns] = useState<AgentRunPublic[]>([]);
  const [orchPhase, setOrchPhase] = useState("");
  const [orchErr, setOrchErr] = useState<string | null>(null);
  const [orchBusy, setOrchBusy] = useState(false);
  const [industryPick, setIndustryPick] = useState<IndustryPack>("general");

  const refreshRuns = useCallback(async () => {
    try {
      const { items } = await listJobAgentRuns(jobId, 100);
      items.sort((a, b) => b.created_at.localeCompare(a.created_at));
      setAllRuns(items);
    } catch {
      setAllRuns([]);
    }
  }, [jobId]);

  useEffect(() => {
    refreshRuns();
  }, [refreshRuns]);

  useEffect(() => {
    const busy = allRuns.some((r) => r.status === "pending" || r.status === "processing");
    if (!busy) return;
    const t = setInterval(() => {
      refreshRuns();
    }, 2000);
    return () => clearInterval(t);
  }, [allRuns, refreshRuns]);

  const runOrchestratedPipeline = async (force: boolean, steps: AgentKind[]) => {
    setOrchErr(null);
    setOrchPhase("");
    setOrchBusy(true);
    try {
      const q = await startAgentOrchestration(jobId, steps, force);
      setOrchPhase(
        `Running ${q.steps.join(" → ")}… (${q.orchestration_id.slice(0, 8)}…)`,
      );
      const long = steps.length > 4;
      const done = await pollAgentOrchestration(q.orchestration_id, {
        intervalMs: long ? 2000 : 1200,
        maxWaitMs: long ? 1_800_000 : 900_000,
      });
      await refreshRuns();
      if (done.status === "failed") {
        setOrchErr(done.error || "Orchestration failed");
        setOrchPhase("");
        return;
      }
      setOrchPhase("Orchestrated pipeline completed.");
    } catch (e) {
      setOrchErr(e instanceof Error ? e.message : String(e));
      setOrchPhase("");
    } finally {
      setOrchBusy(false);
    }
  };

  const runNamedIndustryPipeline = async (force: boolean) => {
    setOrchErr(null);
    setOrchPhase("");
    setOrchBusy(true);
    try {
      const q = await startIndustryOrchestration(jobId, industryPick, force);
      const label = INDUSTRY_PACK_OPTIONS.find((o) => o.value === industryPick)?.label ?? industryPick;
      setOrchPhase(
        `${label}: ${q.steps.join(" → ")}… (${q.orchestration_id.slice(0, 8)}…)`,
      );
      const long = q.steps.length > 4;
      const done = await pollAgentOrchestration(q.orchestration_id, {
        intervalMs: long ? 2000 : 1200,
        maxWaitMs: long ? 1_800_000 : 900_000,
      });
      await refreshRuns();
      if (done.status === "failed") {
        setOrchErr(done.error || "Orchestration failed");
        setOrchPhase("");
        return;
      }
      setOrchPhase("Industry pipeline completed.");
    } catch (e) {
      setOrchErr(e instanceof Error ? e.message : String(e));
      setOrchPhase("");
    } finally {
      setOrchBusy(false);
    }
  };

  return (
    <div className="agents-panel">
      <div className="synthesis agent-block orchestration-block">
        <h3 className="synthesis-title">Orchestrated pipelines</h3>
        <p className="muted small">
          <strong>Core (3)</strong>: synthesis → risk review → incident brief.{" "}
          <strong>Cross-industry (7)</strong>: all agents in one fixed order.{" "}
          <strong>Industry pipeline</strong>: same agents, <em>reordered per vertical</em> (named static graph —
          auditable in code).
        </p>
        <div className="orchestration-industry">
          <label className="label small">Industry vertical</label>
          <select
            className="industry-select"
            value={industryPick}
            disabled={orchBusy}
            onChange={(e) => setIndustryPick(e.target.value as IndustryPack)}
          >
            {INDUSTRY_PACK_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <div className="synthesis-actions">
            <button
              type="button"
              className="btn btn-secondary"
              disabled={orchBusy}
              onClick={() => runNamedIndustryPipeline(false)}
            >
              Run industry pipeline
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={orchBusy}
              onClick={() => runNamedIndustryPipeline(true)}
            >
              Force industry pipeline
            </button>
          </div>
        </div>
        <div className="synthesis-actions">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={orchBusy}
            onClick={() => runOrchestratedPipeline(false, DEFAULT_AGENT_PIPELINE)}
          >
            {orchBusy ? "Running…" : "Run core pipeline (3)"}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={orchBusy}
            onClick={() => runOrchestratedPipeline(true, DEFAULT_AGENT_PIPELINE)}
          >
            Force core (no cache)
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={orchBusy}
            onClick={() => runOrchestratedPipeline(false, CROSS_INDUSTRY_AGENT_PIPELINE)}
          >
            Run cross-industry suite (7)
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={orchBusy}
            onClick={() => runOrchestratedPipeline(true, CROSS_INDUSTRY_AGENT_PIPELINE)}
          >
            Force suite (no cache)
          </button>
        </div>
        {orchPhase ? <p className="phase small">{orchPhase}</p> : null}
        {orchErr ? <p className="error small">{orchErr}</p> : null}
      </div>
      <AgentAsyncBlock
        jobId={jobId}
        agent="synthesis"
        title="Synthesis agent"
        blurb="Cross-chunk narrative from the job JSON. Runs asynchronously; each run is listed below."
        runs={allRuns.filter((r) => r.agent === "synthesis")}
        onRefreshRuns={refreshRuns}
        render={(r) => <SynthesisBody result={r as SynthesisResult} />}
        preview={previewSynthesisRun}
      />
      <AgentAsyncBlock
        jobId={jobId}
        agent="risk_review"
        title="Risk review agent"
        blurb="Safety and security triage from the same summary. All runs are kept in history."
        runs={allRuns.filter((r) => r.agent === "risk_review")}
        onRefreshRuns={refreshRuns}
        render={(r) => <RiskReviewBody result={r as RiskReviewResult} />}
        preview={previewRiskRun}
      />
      <AgentAsyncBlock
        jobId={jobId}
        agent="incident_brief"
        title="Incident brief agent"
        blurb="Short incident-style narrative and follow-ups from the summary (no identities)."
        runs={allRuns.filter((r) => r.agent === "incident_brief")}
        onRefreshRuns={refreshRuns}
        render={(r) => <IncidentBriefBody result={r as IncidentBriefResult} />}
        preview={previewIncidentBriefRun}
      />
      <AgentAsyncBlock
        jobId={jobId}
        agent="compliance_brief"
        title="Compliance brief"
        blurb="Cross-industry SOP / safety alignment read from the summary (warehouse, retail, plant, office)."
        runs={allRuns.filter((r) => r.agent === "compliance_brief")}
        onRefreshRuns={refreshRuns}
        render={(r) => <ComplianceBriefBody result={r as ComplianceBriefResult} />}
        preview={previewComplianceBriefRun}
      />
      <AgentAsyncBlock
        jobId={jobId}
        agent="loss_prevention"
        title="Loss prevention"
        blurb="Retail / logistics LP-style behavioural narrative without identities."
        runs={allRuns.filter((r) => r.agent === "loss_prevention")}
        onRefreshRuns={refreshRuns}
        render={(r) => <LossPreventionBody result={r as LossPreventionResult} />}
        preview={previewLossPreventionRun}
      />
      <AgentAsyncBlock
        jobId={jobId}
        agent="perimeter_chain"
        title="Perimeter chain"
        blurb="Ordered boundary / access storyline for sites, yards, campuses, and similar."
        runs={allRuns.filter((r) => r.agent === "perimeter_chain")}
        onRefreshRuns={refreshRuns}
        render={(r) => <PerimeterChainBody result={r as PerimeterChainResult} />}
        preview={previewPerimeterChainRun}
      />
      <AgentAsyncBlock
        jobId={jobId}
        agent="privacy_review"
        title="Privacy review"
        blurb="Flags identity-inference and sensitive-descriptor risks in the structured summary text."
        runs={allRuns.filter((r) => r.agent === "privacy_review")}
        onRefreshRuns={refreshRuns}
        render={(r) => <PrivacyReviewBody result={r as PrivacyReviewResult} />}
        preview={previewPrivacyReviewRun}
      />
    </div>
  );
}

function previewSynthesisRun(result: Record<string, unknown> | null): string {
  if (!result) return "";
  const s = result.executive_summary;
  if (typeof s !== "string" || !s.trim()) return "";
  const t = s.trim();
  return t.length > 140 ? `${t.slice(0, 140)}…` : t;
}

function previewRiskRun(result: Record<string, unknown> | null): string {
  if (!result) return "";
  const risk = result.overall_risk;
  const factors = result.risk_factors;
  const r = typeof risk === "string" ? risk : "?";
  if (Array.isArray(factors) && factors.length && typeof factors[0] === "string") {
    const f0 = factors[0];
    return `${r}: ${f0.length > 100 ? `${f0.slice(0, 100)}…` : f0}`;
  }
  return r;
}

function previewIncidentBriefRun(result: Record<string, unknown> | null): string {
  if (!result) return "";
  const n = result.narrative;
  if (typeof n !== "string" || !n.trim()) return "";
  const t = n.trim();
  return t.length > 160 ? `${t.slice(0, 160)}…` : t;
}

function previewComplianceBriefRun(result: Record<string, unknown> | null): string {
  if (!result) return "";
  const a = result.overall_alignment;
  const n = result.notes;
  const align = typeof a === "string" ? a : "?";
  if (typeof n === "string" && n.trim()) {
    const t = n.trim();
    return `${align}: ${t.length > 100 ? `${t.slice(0, 100)}…` : t}`;
  }
  return align;
}

function previewLossPreventionRun(result: Record<string, unknown> | null): string {
  if (!result) return "";
  const risk = result.risk_level;
  const r = typeof risk === "string" ? risk : "?";
  const obs = result.behavioral_observations;
  if (Array.isArray(obs) && obs.length && typeof obs[0] === "string") {
    const o0 = obs[0];
    return `${r}: ${o0.length > 90 ? `${o0.slice(0, 90)}…` : o0}`;
  }
  return r;
}

function previewPerimeterChainRun(result: Record<string, unknown> | null): string {
  if (!result) return "";
  const c = result.chain_narrative;
  if (typeof c !== "string" || !c.trim()) return "";
  const t = c.trim();
  return t.length > 160 ? `${t.slice(0, 160)}…` : t;
}

function previewPrivacyReviewRun(result: Record<string, unknown> | null): string {
  if (!result) return "";
  const pr = result.overall_privacy_risk;
  const s = result.summary;
  const risk = typeof pr === "string" ? pr : "?";
  if (typeof s === "string" && s.trim()) {
    const t = s.trim();
    return `${risk}: ${t.length > 100 ? `${t.slice(0, 100)}…` : t}`;
  }
  return risk;
}

function AgentAsyncBlock({
  jobId,
  agent,
  title,
  blurb,
  runs,
  onRefreshRuns,
  render,
  preview: previewFn,
}: {
  jobId: string;
  agent: AgentKind;
  title: string;
  blurb: string;
  runs: AgentRunPublic[];
  onRefreshRuns: () => Promise<void>;
  render: (result: Record<string, unknown>) => ReactNode;
  preview?: (result: Record<string, unknown> | null) => string;
}) {
  const [openIds, setOpenIds] = useState<Record<string, boolean>>({});
  const [phase, setPhase] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const preview = previewFn ?? (() => "");

  const toggleOpen = (id: string) => {
    setOpenIds((o) => ({ ...o, [id]: !o[id] }));
  };

  const run = async (force: boolean) => {
    setErr(null);
    setPhase("");
    setBusy(true);
    try {
      const q = await createAgentRun(jobId, agent, force);
      setPhase(`Queued (${q.run_id.slice(0, 8)}…)`);
      const done = await pollAgentRun(q.run_id);
      await onRefreshRuns();
      if (done.status === "failed") {
        setErr(done.error || "Agent run failed");
        setPhase("");
        return;
      }
      if (done.meta?.cached) setPhase("Served from cache (no new LLM call for this agent).");
      else setPhase("");
      if (done.result) setOpenIds((o) => ({ ...o, [done.id]: true }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPhase("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="synthesis agent-block">
      <h3 className="synthesis-title">{title}</h3>
      <p className="muted small">{blurb}</p>
      <div className="synthesis-actions">
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => run(false)}>
          {busy ? "Running…" : runs.length ? "New run" : "Run"}
        </button>
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => run(true)}>
          Force re-run
        </button>
      </div>
      {phase ? <p className="phase">{phase}</p> : null}
      {err ? <p className="error">{err}</p> : null}

      <h4 className="agent-history-title">
        Run history ({runs.length})
        <button type="button" className="linkish" onClick={() => onRefreshRuns()}>
          Refresh
        </button>
      </h4>
      {runs.length === 0 ? (
        <p className="muted small">No runs yet for this agent on this job.</p>
      ) : (
        <ul className="agent-run-list">
          {runs.map((row) => (
            <li key={row.id} className="agent-run-row">
              <div className="agent-run-head">
                <span className={`agent-run-status st-${row.status}`}>{row.status}</span>
                <span className="mono small agent-run-id" title={row.id}>
                  {row.id.slice(0, 8)}…
                </span>
                <span className="muted small">{row.created_at}</span>
                {row.force ? <span className="agent-run-badge">force</span> : null}
                {row.meta?.cached ? <span className="agent-run-badge">cached</span> : null}
                {row.event_id != null ? (
                  <span className="muted small">event #{row.event_id}</span>
                ) : null}
              </div>
              {row.status === "completed" && row.result ? (
                <p className="agent-run-preview small">{preview(row.result) || "(no preview)"}</p>
              ) : null}
              {row.status === "failed" && row.error ? (
                <p className="error small">{row.error}</p>
              ) : null}
              {row.status === "pending" || row.status === "processing" ? (
                <p className="phase small">In queue or running…</p>
              ) : null}
              {row.status === "completed" && row.result ? (
                <>
                  <button type="button" className="linkish agent-run-toggle" onClick={() => toggleOpen(row.id)}>
                    {openIds[row.id] ? "Hide full output" : "Show full output"}
                  </button>
                  {openIds[row.id] ? (
                    <div className="synthesis-body agent-run-body">{render(row.result)}</div>
                  ) : null}
                </>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RiskReviewBody({ result }: { result: RiskReviewResult }) {
  const sev =
    result.overall_risk === "high"
      ? "risk-high"
      : result.overall_risk === "medium"
        ? "risk-medium"
        : "risk-low";
  return (
    <>
      <p className={`risk-badge ${sev}`}>
        Overall risk: <strong>{result.overall_risk}</strong>
        {result.requires_immediate_review ? " · Immediate review suggested" : null}
      </p>
      {result.operator_notes ? <p className="scene">{result.operator_notes}</p> : null}
      <StringList title="Risk factors" items={result.risk_factors} />
      <StringList title="Mitigations" items={result.mitigations_suggested} />
    </>
  );
}

function IncidentBriefBody({ result }: { result: IncidentBriefResult }) {
  return (
    <>
      <p className="scene">{result.narrative}</p>
      <StringList title="Key moments" items={result.key_moments} />
      <StringList title="Situational factors" items={result.situational_factors} />
      <StringList title="Suggested follow-ups" items={result.suggested_followups} />
    </>
  );
}

function ComplianceBriefBody({ result }: { result: ComplianceBriefResult }) {
  return (
    <>
      <p className="muted small">
        Overall alignment: <strong>{result.overall_alignment}</strong>
      </p>
      {result.notes ? <p className="scene">{result.notes}</p> : null}
      <StringList title="Observed practices" items={result.observed_practices} />
      <StringList title="Gaps or concerns" items={result.gaps_or_concerns} />
      <StringList title="Recommended verifications" items={result.recommended_verifications} />
    </>
  );
}

function LossPreventionBody({ result }: { result: LossPreventionResult }) {
  const sev =
    result.risk_level === "high"
      ? "risk-high"
      : result.risk_level === "medium"
        ? "risk-medium"
        : "risk-low";
  return (
    <>
      <p className={`risk-badge ${sev}`}>
        LP risk: <strong>{result.risk_level}</strong>
      </p>
      <p className="scene">{result.narrative}</p>
      <StringList title="Behavioral observations" items={result.behavioral_observations} />
      <StringList title="Suggested actions" items={result.suggested_actions} />
    </>
  );
}

function PerimeterChainBody({ result }: { result: PerimeterChainResult }) {
  return (
    <>
      <p className="scene">{result.chain_narrative}</p>
      <StringList title="Key events" items={result.key_events} />
      <StringList title="Zones / segments" items={result.zones_or_segments} />
      <StringList title="Follow-up checks" items={result.follow_up_checks} />
    </>
  );
}

function PrivacyReviewBody({ result }: { result: PrivacyReviewResult }) {
  const sev =
    result.overall_privacy_risk === "high"
      ? "risk-high"
      : result.overall_privacy_risk === "medium"
        ? "risk-medium"
        : "risk-low";
  return (
    <>
      <p className={`risk-badge ${sev}`}>
        Privacy risk: <strong>{result.overall_privacy_risk}</strong>
      </p>
      <p className="scene">{result.summary}</p>
      <StringList title="Identity inference risks" items={result.identity_inference_risks} />
      <StringList title="Sensitive descriptors (in input)" items={result.sensitive_descriptors} />
      <StringList title="Safe output guidance" items={result.safe_output_guidance} />
    </>
  );
}

function SynthesisBody({ result }: { result: SynthesisResult }) {
  return (
    <>
      <p className="scene">{result.executive_summary}</p>
      {result.attendance_summary ? (
        <div className="section">
          <h4>Attendance</h4>
          <p className="small">{result.attendance_summary}</p>
        </div>
      ) : null}
      <StringList title="Key observations" items={result.key_observations} />
      <StringList title="Security" items={result.security_highlights} />
      <StringList title="Logistics" items={result.logistics_highlights} />
      <StringList title="Recommended actions" items={result.recommended_actions} />
    </>
  );
}

function StringList({ title, items }: { title: string; items: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="section">
      <h4>{title}</h4>
      <ul>
        {items.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    </div>
  );
}

function ChunkCard({ chunk, index }: { chunk: Record<string, unknown>; index: number }) {
  const [open, setOpen] = useState(true);
  const merged = chunk.merged as Record<string, unknown> | undefined;
  if (!merged) {
    return (
      <div>
        <button type="button" className="toggle" onClick={() => setOpen(!open)}>
          Chunk {index + 1} (raw)
        </button>
        {open ? <pre className="json">{JSON.stringify(chunk, null, 2)}</pre> : null}
      </div>
    );
  }
  return (
    <div>
      <button type="button" className="toggle" onClick={() => setOpen(!open)}>
        Chunk {(typeof merged.chunk_index === "number" ? merged.chunk_index : index) + 1} ·{" "}
        {(merged.scene_summary as string)?.slice(0, 80) || "…"}
        {(merged.scene_summary as string)?.length > 80 ? "…" : ""}
      </button>
      {open ? (
        <div className="chunk-body">
          <p className="scene">{String(merged.scene_summary ?? "")}</p>
          <Section title="Main events" items={merged.main_events as object[] | undefined} />
          <Section title="Security" items={merged.security as object[] | undefined} />
          <Section title="Logistics" items={merged.logistics as object[] | undefined} />
          <Attendance att={merged.attendance as Record<string, unknown> | undefined} />
        </div>
      ) : null}
    </div>
  );
}

function Section({ title, items }: { title: string; items?: object[] }) {
  if (!items?.length) return <p className="muted">{title}: none listed</p>;
  return (
    <div className="section">
      <h3>{title}</h3>
      <ul>
        {items.map((it, i) => (
          <li key={i}>{formatItem(it)}</li>
        ))}
      </ul>
    </div>
  );
}

function formatItem(it: object): string {
  const o = it as Record<string, unknown>;
  if (typeof o.title === "string" && typeof o.detail === "string") return `${o.title}: ${o.detail}`;
  if (typeof o.category === "string" && typeof o.description === "string")
    return `[${o.severity ?? "?"}] ${o.category}: ${o.description}`;
  if (typeof o.label === "string") return `${o.label}${o.description ? ` — ${o.description}` : ""}`;
  return JSON.stringify(it);
}

function Attendance({ att }: { att?: Record<string, unknown> }) {
  if (!att) return null;
  return (
    <div className="section">
      <h3>Attendance (counts)</h3>
      <ul>
        <li>Approx. visible: {String(att.approx_people_visible ?? "—")}</li>
        <li>Entries / exits: {String(att.entries)} / {String(att.exits)}</li>
        {att.notes ? <li>{String(att.notes)}</li> : null}
      </ul>
    </div>
  );
}
