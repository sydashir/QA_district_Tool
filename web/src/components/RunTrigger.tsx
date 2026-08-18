import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, fmtDate, type Run } from "../lib/api";
import Elapsed from "./Elapsed";

/**
 * "Run audit now" — the only place in the product that starts a crawl.
 *
 * Three things here are load-bearing:
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
  /** Dashboard cards have no room for the standing hint; the confirm step still carries the cost. */
  compact?: boolean;
}

export default function RunTrigger({ brandCode, inFlight, queueAhead, compact = false }: Props) {
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);

  const start = useMutation({
    mutationFn: () => api.startRun(brandCode),
    onSettled: async () => {
      setConfirming(false);
      // Invalidate after a failure too: a 409 means somebody else already started this brand, so
      // the list in front of this user is the thing that is out of date.
      // Prefix keys — Runs uses ["runs", brand], Dashboard uses ["runs", 200]; both match ["runs"].
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["runs"] }),
        qc.invalidateQueries({ queryKey: ["brands"] }),
      ]);
    },
  });

  const { isSuccess, reset } = start;
  useEffect(() => {
    // Once the queued run is actually visible, the row itself states the truth and the "queued it"
    // note is redundant. Clearing it here also stops it reappearing HOURS later, when the run
    // finishes, inFlight goes back to null and this branch would otherwise render again.
    if (inFlight !== null && isSuccess) reset();
  }, [inFlight, isSuccess, reset]);

  const error = start.error;
  const conflict = error instanceof ApiError && error.status === 409;
  const detail =
    error instanceof ApiError ? error.detail : error instanceof Error ? error.message : "";
  const hasMessage = start.isPending || start.isSuccess || error !== null;

  const queueNote =
    queueAhead > 0
      ? `${queueAhead} other audit${queueAhead === 1 ? " is" : "s are"} already queued or running, so this one starts only after ${queueAhead === 1 ? "it finishes" : "they finish"}.`
      : "Nothing else is queued, so it should begin within a minute or two — and then run for hours.";

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
              Run audit now
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
          </>
        ) : confirming ? (
          <>
            <span className="small" style={{ flexBasis: "100%" }}>
              Start a full audit of <strong>{brandCode}</strong>? {queueNote} It cannot be stopped
              from here once it starts.
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
            <button type="button" onClick={() => setConfirming(true)} disabled={start.isPending}>
              Run audit now
            </button>
            {!compact ? (
              <span className="small muted">
                Takes hours. Queues behind anything already running.
              </span>
            ) : null}
          </>
        )}
      </div>

      {/* Always mounted, so the region exists before its text does — a live region inserted at the
          same moment as its content is frequently never announced. */}
      <div aria-live="polite" className="small" style={{ marginTop: hasMessage ? 6 : 0 }}>
        {start.isPending ? <span className="muted">Queueing {brandCode}…</span> : null}

        {start.isSuccess ? (
          <span style={{ color: "var(--ok)" }}>
            Queued {brandCode}. It will run for hours — you can leave this page, the run history
            updates itself.
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
      </div>
    </div>
  );
}
