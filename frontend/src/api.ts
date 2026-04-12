const base = (import.meta.env.VITE_API_BASE ?? "/api").replace(/\/$/, "");

export function apiUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${base}${p}`;
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
  const r = await fetch(apiUrl("/jobs/upload"), {
    method: "POST",
    body: fd,
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  return r.json();
}

export async function listJobs(limit = 50): Promise<JobRecord[]> {
  const lim = Math.min(Math.max(limit, 1), 200);
  const r = await fetch(apiUrl(`/jobs?limit=${lim}`));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getJob(id: string): Promise<JobRecord> {
  const r = await fetch(apiUrl(`/jobs/${id}`));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getSummary(id: string): Promise<Record<string, unknown>> {
  const r = await fetch(apiUrl(`/jobs/${id}/summary`));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export type AgentKind = "synthesis" | "risk_review";

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
  const r = await fetch(apiUrl(`/jobs/${jobId}/agent-runs`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent, force }),
  });
  if (r.status !== 202) {
    throw new Error(await r.text());
  }
  return r.json();
}

export async function getAgentRun(runId: string): Promise<AgentRunPublic> {
  const r = await fetch(apiUrl(`/agent-runs/${runId}`));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function listJobAgentRuns(
  jobId: string,
  limit = 100,
): Promise<{ items: AgentRunPublic[] }> {
  const lim = Math.min(Math.max(limit, 1), 100);
  const r = await fetch(apiUrl(`/jobs/${jobId}/agent-runs?limit=${lim}`));
  if (!r.ok) throw new Error(await r.text());
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
  const r = await fetch(apiUrl(`/jobs/${jobId}/agents/synthesis`));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function getRiskReview(jobId: string): Promise<AgentEventPayload> {
  const r = await fetch(apiUrl(`/jobs/${jobId}/agents/risk-review`));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
