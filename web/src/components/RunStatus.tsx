/**
 * RunStatus — the ONE place a run status becomes words.
 *
 * Every screen renders the same five statuses (server/models.py: queued | running | ok | failed |
 * refused). Before this component each screen spelled them differently — the Runs table said
 * "Refused", the dashboard said "Could not be audited" — and the wording of a degraded state is
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

/** Tooltip text: the explanation, plus the run's own reason when the server gave one. */
export function runStatusTitle(
  status: string | null | undefined,
  errorText?: string | null,
): string {
  const meta = runStatusMeta(status);
  const reason = errorText?.trim();
  const base = `${meta.label} — ${meta.explain}`;
  return reason ? `${base} Reported reason: ${reason}` : base;
}

interface Props {
  status: string | null | undefined;
  /** The run's own `error_text`. Carried into the tooltip, and shown for failed/refused runs. */
  errorText?: string | null;
  /** `badge` for a status column; `text` to sit inside a sentence. */
  variant?: "badge" | "text";
  /** Show the explanation as visible text, not only on hover. Use where the status is the point. */
  withExplanation?: boolean;
  className?: string;
}

export default function RunStatus({
  status,
  errorText,
  variant = "badge",
  withExplanation = false,
  className,
}: Props) {
  const meta = runStatusMeta(status);
  const title = runStatusTitle(status, errorText);
  const reason = errorText?.trim() ? errorText.trim() : null;
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

  if (!withExplanation) return label;

  return (
    <div>
      {label}
      <div className={meta.tone === "bad" ? "small" : "small muted"} style={{ marginTop: 4 }}>
        {meta.explain}
      </div>
      {/* The server's own words, kept verbatim — it usually names the thing that went wrong. */}
      {reason !== null && meta.tone === "bad" ? (
        <div className="snippet" style={{ marginTop: 6 }}>
          {reason}
        </div>
      ) : null}
    </div>
  );
}
