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
