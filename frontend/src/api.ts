const base = (import.meta.env.VITE_API_BASE ?? "/api").replace(/\/$/, "");

export function apiUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
}

/** User-facing message for failed HTTP responses (proxy errors, limits, etc.). */
export function formatHttpError(status: number, body: string): string {
  const b = body?.trim();
  if (status === 502) {
    return (
      b ||
      "Bad gateway — the gateway could not reach the API. Check that the API container is healthy (docker compose ps / logs)."
    );
  }
  if (status === 503) {
    return b || "Service unavailable — the server is busy or still starting. Try again in a moment.";
  }
  if (status === 504) {
    return b || "Gateway timeout — the request took too long. Retry or use a smaller upload.";
  }
  if (status === 413) {
    return b || "Upload is larger than the server allows.";
  }
  if (status === 429) {
    return b || "Too many requests. Wait briefly and try again.";
  }
  return b || `Request failed (HTTP ${status}).`;
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(apiUrl(path), init);
  } catch (e) {
    const msg =
      e instanceof Error
        ? e.message
        : "Unknown error";
    throw new Error(`Network error — ${msg}. Is the API reachable?`);
  }
}

export type JobRecord = {
  id: string;
  source_path: string;
  status: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  summary?: Record<string, unknown> | null;
};

export async function uploadVideo(file: File): Promise<JobRecord> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await apiFetch("/jobs/upload", {
    method: "POST",
    body: fd,
  });
  if (!r.ok) {
    throw new Error(formatHttpError(r.status, await r.text()));
  }
  return r.json();
}

export async function listJobs(limit = 50): Promise<JobRecord[]> {
  const lim = Math.min(Math.max(limit, 1), 200);
  const r = await apiFetch(`/jobs?limit=${lim}`);
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()));
  return r.json();
}

export async function getJob(id: string): Promise<JobRecord> {
  const r = await apiFetch(`/jobs/${id}`);
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()));
  return r.json();
}

export async function getSummary(id: string): Promise<Record<string, unknown>> {
  const r = await apiFetch(`/jobs/${id}/summary`);
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()));
  return r.json();
}

export type AgentKind = "synthesis" | "risk_review" | "incident_brief";

export type AgentRunPublic = {
  id: string;
  job_id: string;
  agent: AgentKind;
  status: string;
  force: boolean;
  created_at: string;
  updated_at: string;
  error: string | null;
  result: Record<string, unknown> | null;
  event_id: number | null;
  meta: Record<string, unknown>;
};

export type AgentRunQueued = {
  run_id: string;
  job_id: string;
  agent: AgentKind;
  status: string;
  created_at: string;
  poll_url: string;
};

export async function createAgentRun(
  jobId: string,
  agent: AgentKind,
  force = false,
): Promise<AgentRunQueued> {
  const r = await apiFetch(`/jobs/${jobId}/agent-runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent, force }),
  });
  if (r.status !== 202) {
    throw new Error(formatHttpError(r.status, await r.text()));
  }
  return r.json();
}

export async function getAgentRun(runId: string): Promise<AgentRunPublic> {
  const r = await apiFetch(`/agent-runs/${runId}`);
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()));
  return r.json();
}

export async function listJobAgentRuns(
  jobId: string,
  limit = 100,
): Promise<{ items: AgentRunPublic[] }> {
  const lim = Math.min(Math.max(limit, 1), 100);
  const r = await apiFetch(`/jobs/${jobId}/agent-runs?limit=${lim}`);
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()));
  return r.json();
}

export async function pollAgentRun(
  runId: string,
  opts?: { intervalMs?: number; maxWaitMs?: number },
): Promise<AgentRunPublic> {
  const intervalMs = opts?.intervalMs ?? 600;
  const maxWaitMs = opts?.maxWaitMs ?? 600_000;
  const t0 = Date.now();
  while (Date.now() - t0 < maxWaitMs) {
    const s = await getAgentRun(runId);
    if (s.status === "completed" || s.status === "failed") return s;
    await new Promise((res) => setTimeout(res, intervalMs));
  }
  throw new Error("Timed out waiting for agent run");
}

export type SynthesisResult = {
  schema_version: string;
  executive_summary: string;
  key_observations: string[];
  security_highlights: string[];
  logistics_highlights: string[];
  attendance_summary: string;
  recommended_actions: string[];
};

export type RiskReviewResult = {
  schema_version: string;
  overall_risk: string;
  requires_immediate_review: boolean;
  risk_factors: string[];
  operator_notes: string;
  mitigations_suggested: string[];
};

export type IncidentBriefResult = {
  schema_version: string;
  narrative: string;
  key_moments: string[];
  situational_factors: string[];
  suggested_followups: string[];
};

export type AgentEventPayload = {
  event_id: number;
  observed_at: string;
  result: Record<string, unknown> | null;
  error?: string | null;
  attempts?: number | null;
  truncated_input?: boolean | null;
  model?: string | null;
};

export async function getSynthesis(jobId: string): Promise<AgentEventPayload> {
  const r = await apiFetch(`/jobs/${jobId}/agents/synthesis`);
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()));
  return r.json();
}

export async function getRiskReview(jobId: string): Promise<AgentEventPayload> {
  const r = await apiFetch(`/jobs/${jobId}/agents/risk-review`);
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()));
  return r.json();
}

export async function getIncidentBrief(jobId: string): Promise<AgentEventPayload> {
  const r = await apiFetch(`/jobs/${jobId}/agents/incident-brief`);
  if (!r.ok) throw new Error(formatHttpError(r.status, await r.text()));
  return r.json();
}
