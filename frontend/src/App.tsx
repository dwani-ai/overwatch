import { useCallback, useEffect, useState } from "react";
import type { JobRecord, SynthesisResult } from "./api";
import { getJob, getSummary, getSynthesis, postSynthesis, uploadVideo } from "./api";
import "./App.css";

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

export default function App() {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<JobRecord | null>(null);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [phase, setPhase] = useState<string>("");

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
        return;
      }
      await sleep(2000);
    }
    setPhase("Timed out waiting for job");
  }, []);

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
    }
  };

  return (
    <div className="app">
      <header className="header">
        <h1>Overwatch</h1>
        <p className="tag">Upload a video — results appear when the job finishes.</p>
      </header>

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

      {job ? (
        <section className="card">
          <h2>Job</h2>
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
      {jobId ? <SynthesisPanel jobId={jobId} /> : null}
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

function SynthesisPanel({ jobId }: { jobId: string }) {
  const [result, setResult] = useState<SynthesisResult | null>(null);
  const [meta, setMeta] = useState<{ cached?: boolean; observed_at?: string; error?: string | null }>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await getSynthesis(jobId);
        if (cancelled) return;
        if (r.result) setResult(r.result);
        setMeta({ observed_at: r.observed_at, error: r.error ?? null });
      } catch {
        /* no prior run */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const run = async (force: boolean) => {
    setErr(null);
    setBusy(true);
    try {
      const r = await postSynthesis(jobId, force);
      if (r.result) setResult(r.result);
      setMeta({
        cached: r.cached,
        observed_at: r.observed_at,
        error: r.error ?? null,
      });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="synthesis">
      <h3 className="synthesis-title">Synthesis agent</h3>
      <p className="muted small">
        Cross-chunk summary from the job JSON (text-only LLM). Stored as an event; safe to re-run.
      </p>
      <div className="synthesis-actions">
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => run(false)}>
          {busy ? "Running…" : result ? "Refresh from server" : "Run synthesis"}
        </button>
        <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => run(true)}>
          Re-run (force)
        </button>
      </div>
      {err ? <p className="error">{err}</p> : null}
      {meta.cached ? <p className="phase">Served from cache (same job, no new LLM call).</p> : null}
      {meta.observed_at ? (
        <p className="muted small">
          Last run: {meta.observed_at}
          {meta.error ? <span className="error"> — {meta.error}</span> : null}
        </p>
      ) : null}
      {result ? (
        <div className="synthesis-body">
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
        </div>
      ) : null}
    </div>
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
