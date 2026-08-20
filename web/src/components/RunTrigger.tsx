import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, fmtDate, type Run } from "../lib/api";
import Elapsed from "./Elapsed";
import { STOP_REQUESTED_NOTE } from "./RunStatus";

/**
 * "Run audit now" — the only place in the product that starts a crawl, and now the only place that
 * stops one.
 *
 * Five things here are load-bearing:
 *
 * 1. **A run takes HOURS.** The worker runs at concurrency 1 (server/worker.py), so a click does
 *    not start an audit — it queues one behind everything already in flight. The button says so
 *    before it is pressed, not after.
 * 2. **409 is a normal answer, not a fault.** The API refuses a second run for a brand that already
 *    has one queued or running, and the honest thing to show is "already running", never a generic
 *    red error that makes a correct refusal look like a broken product.
 * 3. **The confirm step is deliberate.** A mis-click costs a multi-hour crawl of a client's live
 *    origin and pushes every other brand back in the queue, so the cost — including the real queue
 *    depth — is stated before the second click.
 * 4. **Some brands cannot be audited in full.** MHD's origin starts 503ing under load, so its
 *    crawler is pinned to two requests at a time and a full census would take about 54 hours,
 *    blocking every other brand for the duration. Those brands run capped, and the run is published
 *    as a labelled partial sample. Somebody pressing this button has to know that BEFORE they press
 *    it — a sample presented as an audit is the exact failure this product exists to prevent.
 * 5. **Stopping is a request, not an event.** A queued run can genuinely be pulled from the queue.
 *    A crawl already under way cannot be halted mid-page, so the server records the request and the
 *    worker acts on it when it next stops. This component reports whichever of those two actually
 *    happened, and never the wrong one.
 */

/** How often to re-poll while anything is in flight. A crawl is hours long; this is a heartbeat. */
export const RUN_POLL_MS = 10_000;

/** RR is ~8,000 pages / ~6 hours. That number is what people need to hear before they commit. */
export const HOURS_WARNING =
  "An audit crawls the whole website and takes hours — RR is about six, and MHD is throttled so " +
  "hard it is only ever a labelled partial sample. Only one audit runs at a time, so a new one " +
  "waits for whatever is already going.";

interface Props {
  brandCode: string;
  /** This brand's own queued/running run, if it has one. Null means the brand is idle. */
  inFlight: Run | null;
  /** Runs already queued or running for OTHER brands — this one starts only after them. */
  queueAhead: number;
  /**
   * `Brand.default_sample_size` — the page cap this brand's runs stop at, or null for a full site.
   * Display only: the server resolves the cap itself when the run is created (see api.startRun),
   * so a stale copy in this browser can describe the run wrongly but can never cause a wrong run.
   */
  defaultSampleSize?: number | null;
  /** Dashboard cards have no room for the standing hint; the confirm step still carries the cost. */
  compact?: boolean;
}

export default function RunTrigger({
  brandCode,
  inFlight,
  queueAhead,
  defaultSampleSize = null,
  compact = false,
}: Props) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [stopConfirming, setStopConfirming] = useState(false);

  // Both queries carry every run's status, so a start and a stop both invalidate the same two.
  const refreshRuns = () =>
    // Prefix keys — Runs uses ["runs", brand], Dashboard uses ["runs", 200]; both match ["runs"].
    Promise.all([
      qc.invalidateQueries({ queryKey: ["runs"] }),
      qc.invalidateQueries({ queryKey: ["brands"] }),
    ]);

  const start = useMutation({
    // No max_pages: the server reads the brand's own cap, so the run uses the cap that is true at
    // the moment it starts rather than the one this browser happened to load.
    mutationFn: () => api.startRun(brandCode),
    onSettled: async () => {
      setConfirming(false);
      // Invalidate after a failure too: a 409 means somebody else already started this brand, so
      // the list in front of this user is the thing that is out of date.
      await refreshRuns();
    },
  });

  const stop = useMutation({
    mutationFn: async () => {
      // Guarded rather than asserted: the run can finish on its own between the render that drew
      // this button and the click that fires it.
      if (inFlight === null) throw new Error("that run has already finished, so there is nothing to stop");
      return api.cancelRun(inFlight.id);
    },
    onSettled: async () => {
      setStopConfirming(false);
      await refreshRuns();
    },
  });

  const { isSuccess, reset } = start;
  useEffect(() => {
    // Once the queued run is actually visible, the row itself states the truth and the "queued it"
    // note is redundant. Clearing it here also stops it reappearing HOURS later, when the run
    // finishes, inFlight goes back to null and this branch would otherwise render again.
    if (inFlight !== null && isSuccess) reset();
  }, [inFlight, isSuccess, reset]);

  const { isSuccess: stopDone, reset: resetStop } = stop;
  useEffect(() => {
    // Mirror of the above, the other way round: once the run is gone from the in-flight list its
    // own row says "Stopped before finishing", and leaving "stop requested" hanging over a button
    // that now offers to start a fresh run would describe a run that no longer exists.
    if (inFlight === null && stopDone) resetStop();
  }, [inFlight, stopDone, resetStop]);

  const error = start.error;
  const conflict = error instanceof ApiError && error.status === 409;
  const detail =
    error instanceof ApiError ? error.detail : error instanceof Error ? error.message : "";
  const hasMessage = start.isPending || start.isSuccess || error !== null;

  const stopError = stop.error;
  const stopGone = stopError instanceof ApiError && stopError.status === 409;
  const stopDetail =
    stopError instanceof ApiError
      ? stopError.detail
      : stopError instanceof Error
        ? stopError.message
        : "";
  const hasStopMessage = stop.isPending || stop.isSuccess || stopError !== null;

  const queueNote =
    queueAhead > 0
      ? `${queueAhead} other audit${queueAhead === 1 ? " is" : "s are"} already queued or running, so this one starts only after ${queueAhead === 1 ? "it finishes" : "they finish"}.`
      : "Nothing else is queued, so it should begin within a minute or two — and then run for hours.";

  // A capped brand never gets a full audit, so the word "audit" on its own would overstate what the
  // button does. Everything below that changes wording keys off this one value.
  const cap = defaultSampleSize !== null && defaultSampleSize !== undefined ? defaultSampleSize : null;
  const capTitle =
    cap !== null
      ? `${brandCode} is audited in samples of ${cap.toLocaleString()} pages, because its server cannot survive a full crawl of the site. The rest of the site is not checked, and the run is labelled a partial sample.`
      : undefined;
  /** The cap the run REALLY started with, straight from the server's answer. */
  const startedCap = start.data?.max_pages ?? null;

  return (
    <div>
      <div className="controls" style={{ margin: compact ? "10px 0 0" : 0 }}>
        {inFlight ? (
          <>
            <button
              type="button"
              disabled
              title={`${brandCode} already has a run ${inFlight.status}. Only one run per brand is allowed at a time.`}
            >
              {cap !== null ? "Run sample audit" : "Run audit now"}
            </button>
            <span className="small">
              {inFlight.status === "running" ? (
                <>
                  <strong>Running now</strong> — <Elapsed since={inFlight.started_at} /> so far.
                  Hours is normal.
                </>
              ) : (
                <>
                  <strong>Queued</strong> since {fmtDate(inFlight.started_at)}, waiting for a free
                  worker.
                </>
              )}
            </span>

            {/* Stopping is the whole reason this control exists: one 54-hour brand at the head of a
                single-worker queue holds up every other brand until somebody can take it out. */}
            {inFlight.cancel_requested ? (
              <span className="small w">{STOP_REQUESTED_NOTE}</span>
            ) : stopConfirming ? (
              <>
                <span className="small" style={{ flexBasis: "100%" }}>
                  Stop the {inFlight.status === "running" ? "running" : "queued"} audit of{" "}
                  <strong>{brandCode}</strong>?{" "}
                  {inFlight.status === "running"
                    ? "It will not stop straight away — a crawl cannot be halted mid-page — and the brand will be left only partly checked, recorded as “Stopped before finishing” rather than as a result."
                    : "It has not started crawling yet, so nothing on the site will have been checked."}{" "}
                  Previous findings are unchanged either way.
                </span>
                <button
                  type="button"
                  onClick={() => stop.mutate()}
                  disabled={stop.isPending}
                  aria-label={`Confirm stopping the audit of ${brandCode}`}
                >
                  {stop.isPending ? "Stopping…" : `Yes, stop ${brandCode}`}
                </button>
                <button
                  type="button"
                  onClick={() => setStopConfirming(false)}
                  disabled={stop.isPending}
                >
                  Keep it running
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setStopConfirming(true)}
                disabled={stop.isPending}
                aria-label={`Stop the audit of ${brandCode}`}
                title={`Stop this audit. ${brandCode} will be left only partly checked, and the run is recorded as stopped rather than as a result.`}
              >
                Stop this audit
              </button>
            )}
          </>
        ) : confirming ? (
          <>
            <span className="small" style={{ flexBasis: "100%" }}>
              {cap !== null ? (
                <>
                  Start a <strong>sample</strong> audit of <strong>{brandCode}</strong>? It stops
                  after {cap.toLocaleString()} pages and is labelled a partial sample — this
                  site&rsquo;s server cannot survive a full crawl, so the rest of the site will not
                  be checked.
                </>
              ) : (
                <>
                  Start a full audit of <strong>{brandCode}</strong>?
                </>
              )}{" "}
              {queueNote} You can stop it from here, but an audit already crawling does not stop
              straight away.
            </span>
            <button
              type="button"
              className="primary"
              onClick={() => start.mutate()}
              disabled={start.isPending}
            >
              {start.isPending ? "Queueing…" : `Yes, queue ${brandCode}`}
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={start.isPending}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => setConfirming(true)}
              disabled={start.isPending}
              title={capTitle}
            >
              {cap !== null ? "Run sample audit" : "Run audit now"}
            </button>
            {/* Shown in compact mode too: a dashboard card is exactly where somebody would otherwise
                assume the button they just pressed audits the whole site. */}
            {cap !== null ? (
              <span className="chip" title={capTitle}>
                sample only — {cap.toLocaleString()} pages
              </span>
            ) : null}
            {!compact ? (
              <span className="small muted">
                Takes hours. Queues behind anything already running.
                {cap !== null
                  ? ` This brand is never audited in full — it stops at ${cap.toLocaleString()} pages and the rest of the site is not checked.`
                  : null}
              </span>
            ) : null}
          </>
        )}
      </div>

      {/* Always mounted, so the region exists before its text does — a live region inserted at the
          same moment as its content is frequently never announced. */}
      <div
        aria-live="polite"
        className="small"
        style={{ marginTop: hasMessage || hasStopMessage ? 6 : 0 }}
      >
        {start.isPending ? <span className="muted">Queueing {brandCode}…</span> : null}

        {start.isSuccess ? (
          <span style={{ color: "var(--ok)" }}>
            Queued {brandCode}
            {/* The server's resolved cap, not the one this browser guessed — if the two disagree,
                the server is right and this is the number that will be audited. */}
            {startedCap !== null
              ? `, capped at ${startedCap.toLocaleString()} pages and labelled a partial sample — the rest of the site will not be checked`
              : ""}
            . It will run for hours — you can leave this page, the run history updates itself.
          </span>
        ) : null}

        {conflict ? (
          <span className="w">
            Already running — {detail}. Nothing new was started; the run in progress is the one to
            watch.
          </span>
        ) : null}

        {error !== null && !conflict ? (
          <span className="e">
            Could not start {brandCode}: {detail}
          </span>
        ) : null}

        {stop.isPending ? <span className="muted">Asking the audit of {brandCode} to stop…</span> : null}

        {/* `took_effect` is the difference between a run that is over and a run that is still
            crawling. Reporting the second as the first would be a claim the user can watch being
            contradicted by the elapsed timer next to it. */}
        {stop.isSuccess && stop.data.took_effect ? (
          <span className="w">
            Stopped {brandCode} before it started crawling, so nothing on the site was checked by
            this run. Previous findings are unchanged.
          </span>
        ) : null}

        {stop.isSuccess && !stop.data.took_effect ? (
          <span className="w">
            {STOP_REQUESTED_NOTE}
            {stop.data.detail ? ` ${stop.data.detail}` : ""}
          </span>
        ) : null}

        {stopGone ? (
          <span className="muted">
            Nothing to stop — {stopDetail}. Check the status of the run itself: it has already
            reached an end, one way or another.
          </span>
        ) : null}

        {stopError !== null && !stopGone ? (
          <span className="e">
            Could not stop {brandCode}: {stopDetail}. The audit is still going.
          </span>
        ) : null}
      </div>
    </div>
  );
}
