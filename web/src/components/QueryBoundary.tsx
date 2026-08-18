import type { ReactNode } from "react";
import { ApiError } from "../lib/api";
import EmptyState from "./EmptyState";

/**
 * The one place that answers "the request did not come back with what we wanted".
 *
 * This screen's whole character is that silence must never read as good news. A page that spins
 * forever, or renders an empty white table when the API is down, looks exactly like a site that was
 * audited and found clean — which is the single thing the client asked us never to do. So every
 * fetch ends in one of four *stated* outcomes: loading, failed, empty-and-explained, or data.
 *
 * The failure text also distinguishes "we never reached the service" from "the service answered
 * with an error", because those need different actions from a QA team that cannot read a stack
 * trace: one means wait and retry, the other means something is actually broken.
 */

/** What kind of failure this was, as far as the browser can tell. */
export type FailureKind = "unreachable" | "http" | "unknown";

export interface Failure {
  kind: FailureKind;
  /** Plain English, for someone who is not a developer. */
  headline: string;
  /** The underlying reason. Always shown, always smaller — never the only thing on screen. */
  detail: string;
}

/** The wording for "the browser never got an answer at all". */
const UNREACHABLE = "The audit service is not responding. It may be restarting.";

/** Statuses a proxy returns when nothing is listening behind it — same story as no answer at all. */
const GATEWAY_STATUSES = new Set([502, 503, 504]);

/** Enough plain English to act on. Anything not listed just shows its number. */
const STATUS_MEANING: Record<number, string> = {
  400: "it did not understand the request",
  401: "the request was not signed in",
  403: "access was refused",
  404: "there is nothing at that address, so it may have been removed",
  409: "that conflicts with something already in progress",
  422: "the request was not valid",
  429: "too many requests were made at once",
  // Deliberately NOT "it hit an error of its own". Verified by killing the API and loading the
  // page: the dev proxy — and a production reverse proxy will do the same — answers 502/500 when
  // the service is simply DOWN. Blaming the service for an error it never had sends someone
  // reading application logs that contain nothing.
  500: "it is not responding properly — it may be down or restarting",
  502: "it could not be reached — it may be down or restarting",
  503: "it is unavailable — it may be restarting or overloaded",
  504: "it did not respond in time",
};

function messageOf(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  if (error === null || error === undefined) return "";
  return String(error);
}

/**
 * `fetch` rejects with a TypeError when the request never reached a server: connection refused,
 * DNS, CORS, the tab going offline. Every browser words it differently ("Failed to fetch",
 * "NetworkError when attempting to fetch resource", Safari's "Load failed"), so the type is the
 * reliable signal and the wording is only a fallback.
 */
function isNetworkFailure(error: unknown): boolean {
  if (error instanceof ApiError) return false;
  if (error instanceof TypeError) return true;
  const m = messageOf(error).toLowerCase();
  return (
    m.includes("failed to fetch") ||
    m.includes("networkerror") ||
    m.includes("load failed") ||
    m.includes("network request failed")
  );
}

/**
 * The HTTP status, if this failure has one. `api.ts` throws ApiError (status + the server's own
 * `detail`) for the write calls, and a plain `Error("503 Service Unavailable on /api/runs")` for
 * the reads — so read both rather than only the typed one.
 */
function httpStatus(error: unknown): number | null {
  if (error instanceof ApiError) return error.status;
  const m = /^\s*(\d{3})\b/.exec(messageOf(error));
  if (m === null) return null;
  const code = Number(m[1]);
  return code >= 100 && code <= 599 ? code : null;
}

/**
 * Turn whatever was thrown into something a QA analyst can read and act on.
 * `label` names the thing that failed to load, e.g. "the findings", "the run history".
 */
export function describeFailure(error: unknown, label = "this"): Failure {
  const raw = messageOf(error);
  const status = httpStatus(error);

  if (isNetworkFailure(error) || (status !== null && GATEWAY_STATUSES.has(status))) {
    return {
      kind: "unreachable",
      headline: UNREACHABLE,
      detail:
        status !== null
          ? `The request for ${label} came back as ${status}, which means nothing was listening behind it.`
          : `The browser could not reach the service at all to load ${label}.${raw ? ` (${raw})` : ""}`,
    };
  }

  if (status !== null) {
    const meaning = STATUS_MEANING[status];
    return {
      kind: "http",
      headline: `Could not load ${label}.`,
      detail: `The audit service answered ${status}${meaning ? ` — ${meaning}` : ""}.${raw ? ` (${raw})` : ""}`,
    };
  }

  return {
    kind: "unknown",
    headline: `Could not load ${label}.`,
    detail: raw !== "" ? raw : "No reason was reported.",
  };
}

/**
 * The slice of a react-query result this component needs. Structural on purpose: any
 * `useQuery(...)` result satisfies it, and the component stays testable without a QueryClient.
 */
export interface QueryLike<T> {
  readonly data: T | undefined;
  readonly error: unknown;
  readonly isPending: boolean;
  readonly isError: boolean;
  readonly isFetching: boolean;
  readonly fetchStatus: "fetching" | "paused" | "idle";
  readonly refetch: () => unknown;
}

function Retry({ query, label }: { query: QueryLike<unknown>; label: string }) {
  return (
    <button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>
      {query.isFetching ? "Trying again…" : `Try loading ${label} again`}
    </button>
  );
}

/**
 * The reason plus a Retry, sized to sit inside a banner that already explains the consequence.
 * Used where a *secondary* query fails and the screen is still worth showing.
 */
export function FailureNote({ query, label }: { query: QueryLike<unknown>; label: string }) {
  const failure = describeFailure(query.error, label);
  return (
    <span className="small">
      {failure.headline} <span className="muted">{failure.detail}</span>{" "}
      <button
        type="button"
        className="small"
        disabled={query.isFetching}
        onClick={() => void query.refetch()}
      >
        {query.isFetching ? "Trying…" : "Try again"}
      </button>
    </span>
  );
}

/** A stated wait. Never an empty box, never an unlabelled spinner. */
function Loading({ label, rows }: { label: string; rows: number }) {
  return (
    <div className="skel" role="status" aria-busy="true" aria-live="polite">
      <div className="muted small">Loading {label}…</div>
      {Array.from({ length: Math.max(1, rows) }, (_, i) => (
        <i key={i} />
      ))}
    </div>
  );
}

interface Props<T> {
  query: QueryLike<T>;
  /** Names the thing being loaded, inside a sentence: "Loading the findings…". Lower case. */
  label: string;
  /** Rows in the loading skeleton. Two or three for a card, more for a long table. */
  skeletonRows?: number;
  /** Is the loaded data empty? Defaults to "an empty array, or null". */
  isEmpty?: (data: T) => boolean;
  /** What that emptiness MEANS here. Supply an <EmptyState>; "No data" is never good enough. */
  empty?: ReactNode;
  /** Rendered only when there is data and it is not empty. Omit to use this purely as a gate. */
  children?: (data: T) => ReactNode;
}

function defaultIsEmpty(data: unknown): boolean {
  if (Array.isArray(data)) return data.length === 0;
  return data === null;
}

export default function QueryBoundary<T>({
  query,
  label,
  skeletonRows = 3,
  isEmpty,
  empty,
  children,
}: Props<T>) {
  const { data } = query;

  // Failed outright, with nothing cached to fall back on.
  if (query.isError && data === undefined) {
    const failure = describeFailure(query.error, label);
    return (
      <div className="empty" role="alert">
        <strong className="e">{failure.headline}</strong>
        <div className="small state-detail">{failure.detail}</div>
        <div className="small state-detail muted">
          This is a problem loading the page, not a result. It does not mean there is nothing wrong.
        </div>
        <div className="controls">
          <Retry query={query} label={label} />
        </div>
      </div>
    );
  }

  if (data === undefined) {
    // react-query pauses instead of failing when the browser reports itself offline, so this
    // would otherwise sit at "Loading…" until the network came back.
    if (query.fetchStatus === "paused") {
      return (
        <div className="empty" role="alert">
          <strong className="e">Waiting for a connection.</strong>
          <div className="small state-detail">
            This browser is offline, so {label} could not be requested. It will load once the
            connection is back.
          </div>
          <div className="controls">
            <Retry query={query} label={label} />
          </div>
        </div>
      );
    }
    // A disabled query never fetches and never resolves. Say so rather than spin forever.
    if (query.isPending && query.fetchStatus === "idle") {
      return (
        <EmptyState
          title="Nothing has been requested yet."
          detail={`Choose what to look at and ${label} will load.`}
        />
      );
    }
    return <Loading label={label} rows={skeletonRows} />;
  }

  const emptyNow = isEmpty !== undefined ? isEmpty(data) : defaultIsEmpty(data);

  return (
    <>
      {/* Kept data plus a failed refresh: show it, but never let it pass as current. */}
      {query.isError ? (
        <div className="banner small qb-stale">
          <strong>This did not refresh.</strong> You are looking at the copy this browser loaded
          earlier, which may be out of date. <FailureNote query={query} label={label} />
        </div>
      ) : null}
      {emptyNow
        ? (empty ?? <EmptyState title={`There is nothing to show for ${label}.`} />)
        : children?.(data)}
    </>
  );
}
