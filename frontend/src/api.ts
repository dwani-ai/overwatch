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

export type SynthesisResult = {
  schema_version: string;
  executive_summary: string;
  key_observations: string[];
  security_highlights: string[];
  logistics_highlights: string[];
  attendance_summary: string;
  recommended_actions: string[];
};

export type SynthesisGetResponse = {
  event_id: number;
  observed_at: string;
  result: SynthesisResult | null;
  error?: string | null;
  attempts?: number | null;
  truncated_input?: boolean | null;
  model?: string | null;
};

export type SynthesisPostResponse = SynthesisGetResponse & {
  cached?: boolean;
};

export async function getSynthesis(jobId: string): Promise<SynthesisGetResponse> {
  const r = await fetch(apiUrl(`/jobs/${jobId}/agents/synthesis`));
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function postSynthesis(jobId: string, force = false): Promise<SynthesisPostResponse> {
  const q = force ? "?force=true" : "";
  const r = await fetch(apiUrl(`/jobs/${jobId}/agents/synthesis${q}`), { method: "POST" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
