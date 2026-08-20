/**
 * DataCaveat — the banner that goes ABOVE a set of counts whenever those counts cannot be taken
 * at face value.
 *
 * The engine is scrupulous about degraded states: it records a refusal as a refusal, labels a
 * partial crawl as partial, and knows when a brand is enumerated from a hand-written list. None of
 * that means anything unless the screen says it next to the numbers, so this component turns those
 * three facts into sentences a QA analyst can act on:
 *
 *   1. the latest run did not produce results (refused / did not finish / still running)
 *      -> the numbers below are LEFT OVER from an earlier audit;
 *   2. `partial_sample` -> the run covered part of the site, so the numbers are a sample — and when
 *      `max_pages` is set, that it was capped on purpose and where it stopped;
 *   3. `enumeration_mode === "urls_file"` -> pages are found from a fixed list kept by hand, so
 *      anything published since that list was written has never been visited.
 *
 * The wording of the status itself comes from RunStatus, so a status is described identically
 * whether it appears as a badge or inside one of these sentences.
 */
import { fmtDate, type Run } from "../lib/api";
import { runStatusMeta, STOP_REQUESTED_NOTE } from "./RunStatus";

export type CaveatKind = "never" | "stale" | "in_flight" | "partial" | "fixed_list";

export interface Caveat {
  kind: CaveatKind;
  /** Bolded first clause — the thing to read if nothing else is read. */
  headline: string;
  /** What it means for the numbers below. */
  body: string;
}

export interface CaveatInput {
  /**
   * The newest run that reached a verdict (ok | failed | refused) — the run the counts came from.
   *
   * `null` means this brand has never had one, so the counts are zeros-because-nobody-looked.
   * `undefined` means we have not loaded the run history and therefore do not know: say nothing
   * rather than accuse a healthy brand of never having been audited.
   */
  latest?: Run | null;
  /** A queued or running run for the same brand. It has produced nothing yet. */
  inFlight?: Run | null;
  /**
   * How pages are discovered. Brands spell it `urls_file` (Brand.enumeration_mode) and runs spell
   * the same thing `urls-file` (Run.enumeration_method); both are accepted.
   */
  enumerationMode?: string | null;
}

/** DBH: its platform publishes no page index, so its page list is maintained by hand. */
export function isFixedPageList(mode: string | null | undefined): boolean {
  return mode !== null && mode !== undefined && mode.replace(/-/g, "_") === "urls_file";
}

/**
 * How pages were found, in words rather than in the server's vocabulary. "urls-file" means
 * nothing to a QA analyst, and it is the difference between "we looked at the whole site" and
 * "we looked at the pages somebody remembered to write down".
 */
export function enumerationMeta(mode: string | null | undefined): { label: string; title: string } {
  if (mode === null || mode === undefined || mode === "") {
    return {
      label: "not recorded",
      title: "This run did not record how it found the pages — usually because it never got that far.",
    };
  }
  if (isFixedPageList(mode)) {
    return {
      label: "fixed page list",
      title:
        "Pages came from a list kept by hand, because this site publishes no index of its own " +
        "pages. Anything added to the site since that list was written was not visited.",
    };
  }
  if (mode.replace(/-/g, "_") === "sitemap") {
    return {
      label: "the site's own page index",
      title: "Pages came from the site's own sitemap, so newly published pages are picked up.",
    };
  }
  return { label: mode, title: `The server recorded this run's page source as "${mode}".` };
}

export function dataCaveats(input: CaveatInput): Caveat[] {
  const { latest, inFlight = null, enumerationMode = null } = input;
  const out: Caveat[] = [];

  if (latest === null) {
    out.push({
      kind: "never",
      headline: "Never audited.",
      body:
        "No audit has ever finished for this site, so the numbers here mean “not checked yet”, " +
        "not “nothing wrong”.",
    });
  } else if (latest !== undefined && latest.status !== "ok") {
    const meta = runStatusMeta(latest.status);
    out.push({
      kind: "stale",
      headline: "These numbers are from an earlier audit.",
      body:
        `The most recent attempt (${fmtDate(latest.started_at)}) came back “${meta.label}”. ` +
        `${meta.explain} Nothing here has been re-checked since the last audit that did finish.`,
    });
  }

  if (inFlight !== null) {
    const meta = runStatusMeta(inFlight.status);
    out.push({
      kind: "in_flight",
      headline:
        inFlight.status === "running" ? "An audit is running now." : "An audit is waiting to start.",
      // A stop that has been asked for has NOT happened: the crawl keeps going until the worker
      // next comes to a stop. Saying "running now" and nothing else would hide a pending stop;
      // saying "stopped" would be a claim the elapsed timer next to it visibly contradicts.
      body: inFlight.cancel_requested ? `${meta.explain} ${STOP_REQUESTED_NOTE}` : meta.explain,
    });
  }

  // Only a settled run's coverage is a fact about these numbers; an in-flight run has no coverage yet.
  if (latest !== undefined && latest !== null && latest.partial_sample) {
    const n = latest.pages_audited;
    // `max_pages` set means the run was SHORT ON PURPOSE — it was told to stop at N. Without it,
    // a short run and a capped run are indistinguishable from the page count alone, and "we chose
    // to look at 900 pages" is a very different sentence from "we only got through 900 pages".
    const cap = latest.max_pages;
    const capped =
      cap !== null && cap !== undefined
        ? `It was capped at ${cap.toLocaleString()} pages before it started, because this site cannot be crawled in full, so every page past the cap was never visited. `
        : "";
    out.push({
      kind: "partial",
      headline: "Part of the site only.",
      body:
        `This run covered ${n.toLocaleString()} page${n === 1 ? "" : "s"}, not the whole site. ` +
        capped +
        "Whatever is wrong on the pages it did not reach is not counted here.",
    });
  }

  if (isFixedPageList(enumerationMode) || isFixedPageList(latest?.enumeration_method)) {
    out.push({
      kind: "fixed_list",
      headline: "Crawled from a fixed page list.",
      body:
        "This site publishes no index of its own pages, so the auditor works from a list kept by " +
        "hand. Any page added to the site since that list was written is never visited, and " +
        "therefore never checked.",
    });
  }

  return out;
}

/** Kinds that mean "do not read these numbers as current". They get the red treatment. */
const ALARMING: ReadonlySet<CaveatKind> = new Set<CaveatKind>(["never", "stale"]);

interface Props extends CaveatInput {
  /** `banner` sits above a set of counts; `inline` is for a table cell with no room for a box. */
  variant?: "banner" | "inline";
  /** Restrict to certain caveats, for a place where the others are already said. */
  kinds?: readonly CaveatKind[];
  className?: string;
}

export default function DataCaveat({ variant = "banner", kinds, className, ...input }: Props) {
  const all = dataCaveats(input);
  const items = kinds ? all.filter((c) => kinds.includes(c.kind)) : all;
  if (items.length === 0) return null;

  if (variant === "inline") {
    return (
      <div className={className}>
        {items.map((c) => (
          <div key={c.kind} className="small" style={{ marginTop: 4 }}>
            <strong className={ALARMING.has(c.kind) ? "e" : undefined}>{c.headline}</strong>{" "}
            <span className="muted">{c.body}</span>
          </div>
        ))}
      </div>
    );
  }

  const bad = items.some((c) => ALARMING.has(c.kind));
  return (
    <div className={`banner small${bad ? " bad" : ""}${className ? ` ${className}` : ""}`}>
      {items.map((c, i) => (
        <div key={c.kind} style={{ marginTop: i === 0 ? 0 : 6 }}>
          <strong className={ALARMING.has(c.kind) ? "e" : undefined}>{c.headline}</strong> {c.body}
        </div>
      ))}
    </div>
  );
}
