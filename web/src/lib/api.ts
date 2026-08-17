/**
 * API client + types. Mirrors server/api.py exactly — this file is the contract every screen
 * codes against, so screens never guess a field name.
 */

export type Severity = "error" | "warning" | "info";
export type TriageState = "open" | "acknowledged" | "wontfix" | "fixed";

export interface Brand {
  code: string;
  name: string;
  base_url: string;
  enumeration_mode: string;
  scheduled: boolean;
  last_run_at: string | null;
  last_run_status: string | null;
  open_error: number;
  open_warning: number;
  open_info: number;
  untriaged_error: number;
}

export interface Finding {
  id: number;
  brand: string;
  fingerprint: string;
  url: string;
  check: string;
  check_label: string;
  severity: Severity;
  issue: string;
  location: string | null;
  snippet: string | null;
  suggestion: string | null;
  status: string | null;
  first_seen: string | null;
  page_count: number;
  triage_state: TriageState;
  triage_note: string | null;
}

export interface Paged<T> {
  total: number;
  page: number;
  per_page: number;
  items: T[];
}

export interface Run {
  id: number;
  brand: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  pages_audited: number;
  enumeration_method: string | null;
  partial_sample: boolean;
  history_written: boolean;
  open_error: number;
  new_count: number;
  error_text: string | null;
}

export interface CheckOption {
  check: string;
  label: string;
  count: number;
}

export interface ChangeRow {
  fingerprint_hash: string;
  url: string;
  check: string;
  check_label: string;
  severity: Severity;
  issue: string;
  page_count: number;
}

export interface Changes {
  brand: string;
  run_id: number | null;
  new: ChangeRow[];
  resolved: ChangeRow[];
  new_pages: string[];
}

export interface FindingDetail {
  fingerprint: string;
  fingerprint_hash: string;
  brand: string;
  url: string;
  check: string;
  check_label: string;
  severity: Severity;
  issue: string;
  snippet: string | null;
  suggestion: string | null;
  details: Record<string, unknown>;
  page_count: number;
  sources: string[] | null;
  triage: { state: TriageState; note: string | null };
  history: { run_id: number; at: string; status: string | null }[];
}

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== "" && v !== null) qs.set(k, String(v));
  }
  const url = qs.toString() ? `${path}?${qs}` : path;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${url}`);
  return res.json() as Promise<T>;
}

export const api = {
  brands: () => get<Brand[]>("/api/brands"),
  checks: () => get<CheckOption[]>("/api/checks"),
  findings: (p: {
    brand?: string; check?: string; severity?: string; state?: string;
    status?: string; q?: string; page?: number; per_page?: number;
  }) => get<Paged<Finding>>("/api/findings", p),
  finding: (hash: string) => get<FindingDetail>(`/api/findings/${hash}`),
  runs: (p: { brand?: string; limit?: number }) => get<Run[]>("/api/runs", p),
  changes: (brand: string) => get<Changes>("/api/changes", { brand }),
  setTriage: async (hash: string, body: { state: TriageState; note?: string | null }) => {
    const res = await fetch(`/api/triage/${hash}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`triage failed: ${res.status}`);
    return res.json();
  },
};

/** sha256 hex of a fingerprint — matches server/models.py::fp_hash, used for detail links. */
export async function fpHash(fingerprint: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(fingerprint));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export const SEVERITY_ORDER: Severity[] = ["error", "warning", "info"];

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}
