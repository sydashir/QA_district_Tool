/**
 * RunStatus — the ONE place a run status becomes words.
 *
 * Every screen renders the same six statuses (server/models.py: queued | running | ok | failed |
 * refused | cancelled). Before this component each screen spelled them differently — the Runs table
 * said "Refused", the dashboard said "Could not be audited" — and the wording of a degraded state is
 * exactly the thing that must not drift, because the whole product rests on one promise:
 *
 *   a brand that could not be checked must never look like a brand that was checked and found clean.
 *
 * So the label, the colour and the plain-English explanation live here, once, and the screens ask
 * for them. Nobody on the QA team is a developer, so "refused" is never shown as a bare word —
 * it is "Could not be checked", with the reason attached.
 */

export type RunStatusTone = "ok" | "bad" | "wait" | "unknown";

export interface RunStatusMeta {
  /** Short label, safe in a table cell. */
  label: string;
  tone: RunStatusTone;
  /** Colour class from styles.css. */
  cls: string;
  /** One or two sentences a non-developer can act on. Always says what the counts mean. */
  explain: string;
}

/** A brand with no run at all. Distinct from a run that produced nothing — nobody has tried yet. */
const NEVER_RUN: RunStatusMeta = {
  label: "Never audited",
  tone: "unknown",
  cls: "muted",
  explain:
    "No audit has ever been run here, so there is nothing to report. That is not the same as " +
    "nothing being wrong — it means nobody has looked.",
};

const META: Record<string, RunStatusMeta> = {
  ok: {
    label: "Checked",
    tone: "ok",
    cls: "ok",
    explain: "The audit ran from start to finish, so the counts shown are its results.",
  },
  refused: {
    label: "Could not be checked",
    tone: "bad",
    cls: "e",
    explain:
      "The site could not be reached, or it gave the auditor no list of its pages, so not one page " +
      "was checked. Previous results are unchanged. This is NOT a clean result — it means we do " +
      "not know.",
  },
  failed: {
    label: "Did not finish",
    tone: "bad",
    cls: "e",
    explain:
      "The audit crashed or was interrupted part-way, so it produced no usable result. Previous " +
      "results are unchanged. This is not a clean result either — the site needs auditing again.",
  },
  /**
   * Deliberately its own status rather than a flavour of "failed". Nothing broke — somebody chose
   * to stop it, usually to get a brand out of the way of the queue. But the site was still left
   * part-checked, so it cannot read as a result either: it is the third thing, and the words have
   * to say so.
   */
  cancelled: {
    label: "Stopped before finishing",
    tone: "unknown",
    cls: "w",
    explain:
      "Somebody stopped this audit on purpose before it finished, so this site was only partly " +
      "checked — or not checked at all. Nothing went wrong, and previous results are unchanged, " +
      "but whatever the run had not reached yet was never looked at. Audit the brand again when " +
      "there is time for it.",
  },
  running: {
    label: "In progress",
    tone: "wait",
    cls: "muted",
    explain:
      "This audit is still going, and a full crawl takes hours. Any counts on screen are from the " +
      "last completed audit and will not move until it finishes.",
  },
  queued: {
    label: "Waiting to start",
    tone: "wait",
    cls: "muted",
    explain:
      "This audit is waiting for a free worker — only one runs at a time. Nothing has been checked " +
      "yet, so any counts on screen are from the last completed audit.",
  },
};

/** `null`/`""` means no run at all. An unrecognised status is reported verbatim, never as "fine". */
export function runStatusMeta(status: string | null | undefined): RunStatusMeta {
  if (status === null || status === undefined || status === "") return NEVER_RUN;
  return (
    META[status] ?? {
      label: status,
      tone: "unknown",
      cls: "muted",
      explain:
        `The server reported this run as "${status}", which this screen has no wording for. ` +
        "Treat it as “we do not know”, not as a clean result.",
    }
  );
}

export function runStatusLabel(status: string | null | undefined): string {
  return runStatusMeta(status).label;
}

/**
 * True when the run produced nothing anyone may read as a result. Only "ok" is a result — an
 * in-flight, failed, refused or unknown run must never have its zeros shown as findings.
 */
export function isNoResult(status: string | null | undefined): boolean {
  return runStatusMeta(status).tone !== "ok";
}

/**
 * A stop was ASKED FOR and has not happened yet.
 *
 * The crawl cannot be torn down mid-flight — the worker finishes what it is doing and only then
 * records the run as stopped — so this wording exists to stop the UI claiming a stop that has not
 * occurred. Somebody who is told "stopped" and then watches the elapsed timer keep climbing stops
 * believing anything else the product says.
 */
export const STOP_REQUESTED_NOTE =
  "A stop has been requested, but this audit has not stopped yet — a crawl cannot be halted " +
  "mid-page. It keeps going until the worker next comes to a stop, and only then is it recorded " +
  "as “Stopped before finishing”. Whatever it has already checked is kept.";

/** True while a stop has been asked for but the run is still going. */
function stopPending(status: string | null | undefined, cancelRequested?: boolean): boolean {
  return cancelRequested === true && (status === "running" || status === "queued");
}

/** Tooltip text: the explanation, plus the run's own reason when the server gave one. */
export function runStatusTitle(
  status: string | null | undefined,
  errorText?: string | null,
  cancelRequested?: boolean,
): string {
  const meta = runStatusMeta(status);
  const reason = errorText?.trim();
  const base = `${meta.label} — ${meta.explain}`;
  const withStop = stopPending(status, cancelRequested) ? `${base} ${STOP_REQUESTED_NOTE}` : base;
  return reason ? `${withStop} Reported reason: ${reason}` : withStop;
}

interface Props {
  status: string | null | undefined;
  /** The run's own `error_text`. Carried into the tooltip, and shown for failed/refused runs. */
  errorText?: string | null;
  /** The run's own `cancel_requested`. Only means anything while the run is still in flight. */
  cancelRequested?: boolean;
  /** `badge` for a status column; `text` to sit inside a sentence. */
  variant?: "badge" | "text";
  /** Show the explanation as visible text, not only on hover. Use where the status is the point. */
  withExplanation?: boolean;
  className?: string;
}

export default function RunStatus({
  status,
  errorText,
  cancelRequested,
  variant = "badge",
  withExplanation = false,
  className,
}: Props) {
  const meta = runStatusMeta(status);
  const title = runStatusTitle(status, errorText, cancelRequested);
  const reason = errorText?.trim() ? errorText.trim() : null;
  const stopping = stopPending(status, cancelRequested);
  const cls = [variant === "badge" ? `badge ${meta.cls}` : meta.cls, className]
    .filter((c): c is string => Boolean(c))
    .join(" ");

  const label =
    variant === "badge" ? (
      <span className={cls} title={title}>
        {meta.label}
      </span>
    ) : (
      <strong className={cls} title={title}>
        {meta.label}
      </strong>
    );

  // The badge still says "In progress", because it still is. The chip is the only honest way to
  // show a pending stop without overwriting a status that has not changed yet.
  const withChip = stopping ? (
    <>
      {label}{" "}
      <span className="chip" title={STOP_REQUESTED_NOTE}>
        stop requested
      </span>
    </>
  ) : (
    label
  );

  if (!withExplanation) return withChip;

  return (
    <div>
      {withChip}
      <div className={meta.tone === "bad" ? "small" : "small muted"} style={{ marginTop: 4 }}>
        {meta.explain}
      </div>
      {stopping ? (
        <div className="small" style={{ marginTop: 4 }}>
          {STOP_REQUESTED_NOTE}
        </div>
      ) : null}
      {/* The server's own words, kept verbatim. On a failure it names what went wrong; on a
          cancelled run it says who stopped it and when, which is just as much the point. */}
      {reason !== null && meta.tone !== "ok" && meta.tone !== "wait" ? (
        <div className="snippet" style={{ marginTop: 6 }}>
          {reason}
        </div>
      ) : null}
    </div>
  );
}
