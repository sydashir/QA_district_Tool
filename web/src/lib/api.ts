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
  /**
   * Pages this brand's runs stop at, because its origin cannot survive a full census.
   *
   * MHD is the one that forced this: its server starts 503ing under concurrent requests, so the
   * crawler is pinned to two at a time and manages ~2 pages a minute — a full 11,439-page census
   * is about 54 hours, during which no other brand can be audited at all. Runs of that brand are
   * therefore capped and published as a labelled partial sample, never as a full audit.
   *
   * `null` means no cap: a run of this brand covers the whole site.
   */
  default_sample_size: number | null;
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
  /** Search traffic — only filled in when one brand is selected. `traffic_short` is the table
   *  cell, `traffic_note` the full sentence (weighted, zero, unmatched, by design). */
  traffic_state?: string | null;
  traffic_short?: string | null;
  traffic_note?: string | null;
}

export interface Paged<T> {
  total: number;
  page: number;
  per_page: number;
  items: T[];
  /** How the server ordered this list, in words — traffic changes the order, so it is said. */
  ordering_note?: string | null;
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
  /**
   * The cap this run actually started with. `null` means it was a full census of the site.
   *
   * `pages_audited` alone cannot tell you whether a short run was short on purpose: a run that
   * covered 900 pages because it was capped at 900 and a run that covered 900 because it died
   * look identical. This is the field that separates them.
   */
  max_pages: number | null;
  /**
   * Somebody pressed stop. The crawl does not abort mid-flight, so this stays true while the run
   * is still `running` — it is a request, not a state. The worker records the run as `cancelled`
   * when it next stops.
   */
  cancel_requested: boolean;
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
  /** What the search-traffic data says about this finding's pages, as a sentence. */
  traffic?: { state: string; note: string } | null;
  triage: { state: TriageState; note: string | null };
  history: { run_id: number; at: string; status: string | null }[];
}

export interface StartRunResult {
  queued: boolean;
  brand: string;
  run_id: number;
  job_id: number;
  /**
   * The cap the SERVER resolved for this run — an explicit one if we sent it, otherwise the
   * brand's own default, otherwise null for a full census. Read this back rather than assuming:
   * the brand list in this browser may be minutes old, and the cap the run actually started with
   * is the only one worth telling anybody about.
   */
  max_pages: number | null;
}

/**
 * The answer to a stop request, which is NOT the same as "it stopped".
 *
 * A queued run can be pulled out of the queue before it ever touches the site, so it really is
 * over: `took_effect` true. A run that is already crawling cannot be torn down mid-request, so the
 * server only records the request and the worker acts on it when it next stops: `took_effect`
 * false, and the run is still `running` until then. Saying "stopped" in that second case would be
 * a lie the user could catch simply by watching the row keep moving.
 */
export interface CancelRunResult {
  cancelled: boolean;
  took_effect: boolean;
  detail?: string;
  run_id?: number;
  status?: string;
}

/**
 * An HTTP failure that kept the server's own wording.
 *
 * `POST /api/brands/{code}/runs` answers 409 when that brand already has a run queued or running.
 * That is a normal answer, not a bug, and the UI has to be able to tell it apart from a real
 * failure — so the status code has to survive as far as the screen.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** FastAPI puts the human-readable reason in `detail`; a non-JSON body means it never reached the app. */
async function apiError(res: Response, fallback: string): Promise<ApiError> {
  let detail = fallback;
  try {
    const body: unknown = await res.json();
    if (body !== null && typeof body === "object" && "detail" in body) {
      const d: unknown = (body as { detail: unknown }).detail;
      if (typeof d === "string" && d.trim() !== "") detail = d;
    }
  } catch {
    // HTML error page from a proxy, or an empty body — the fallback is all we have.
  }
  return new ApiError(res.status, detail);
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
  /**
   * Queue an audit. 202 means QUEUED, not finished — the crawl then runs for hours.
   * 409 means that brand already has one in flight; 503 means the worker is not up to take it.
   * Both arrive as an ApiError carrying the server's `detail`.
   *
   * `maxPages` is left OFF for a normal "run this brand" click, even for a brand that has a cap.
   * The server resolves the cap from the brand row itself, so omitting it means the run always
   * uses the cap that is true right now rather than whatever this browser cached — and one place
   * decides it, not two. Pass it only to override the brand's default deliberately; the server
   * rejects zero and negative values with a 422.
   */
  startRun: async (code: string, maxPages?: number): Promise<StartRunResult> => {
    const res = await fetch(`/api/brands/${encodeURIComponent(code)}/runs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(maxPages === undefined ? {} : { max_pages: maxPages }),
    });
    if (!res.ok) throw await apiError(res, `could not start a run for ${code} (${res.status})`);
    return res.json() as Promise<StartRunResult>;
  },
  /**
   * Ask for a run to stop. Read `took_effect` before telling anybody it has: false means the crawl
   * is still going and will only be recorded as cancelled when the worker next stops.
   *
   * 404 is an unknown run; 409 means it already finished, one way or another, so there was nothing
   * left to stop. Both keep the server's own wording via ApiError.
   */
  cancelRun: async (runId: number): Promise<CancelRunResult> => {
    const res = await fetch(`/api/runs/${encodeURIComponent(String(runId))}/cancel`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) throw await apiError(res, `could not stop run #${runId} (${res.status})`);
    return res.json() as Promise<CancelRunResult>;
  },
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

/**
 * A run that has not produced a result yet. Statuses are queued | running | ok | failed | refused |
 * cancelled (server/models.py); the first two are in flight, and a brand may only have one of them
 * at a time.
 *
 * `cancelled` is deliberately NOT in flight — it is terminal. A run whose stop request has landed
 * but whose crawl is still going is still `running` with `cancel_requested` set, and it has to keep
 * counting as in flight or the UI would offer to start a second run of a brand that is still being
 * crawled.
 *
 * These runs carry no findings, so nothing may read their counts as results.
 */
export function isInFlight(run: { status: string } | null | undefined): boolean {
  return run != null && (run.status === "queued" || run.status === "running");
}

export function anyInFlight(runs: readonly Run[] | undefined): boolean {
  return (runs ?? []).some(isInFlight);
}

/** Coarse on purpose: a crawl runs for hours, so seconds only carry information in the first minute. */
export function fmtDuration(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  const total = Math.floor(Math.max(0, ms) / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** E.164 -> the form a person reads: +18889954208 becomes (888) 995-4208.
 *
 *  E.164 is the checks' internal form and the right one for comparison — it is what makes
 *  "+18445760144" and "(844) 576-0144" the same number. It is not what a client should be shown,
 *  and the product was printing it raw in issue text, snippets and suggestions. Done at DISPLAY
 *  time on both surfaces (here and in scripts/client_report.py) rather than in the checks, which
 *  are hashed into checks_version — changing those would invalidate every brand's resume cache to
 *  fix a formatting detail. */
export function humanisePhones(text: string | null | undefined): string {
  if (!text) return "";
  return text.replace(/\+1(\d{3})(\d{3})(\d{4})\b/g, "($1) $2-$3");
}
