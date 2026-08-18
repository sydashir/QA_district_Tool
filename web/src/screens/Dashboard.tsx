import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { anyInFlight, api, fmtDate, isInFlight } from "../lib/api";
import type { Brand, Run } from "../lib/api";
import RunTrigger, { RUN_POLL_MS } from "../components/RunTrigger";
import Elapsed from "../components/Elapsed";

/**
 * Landing screen — "is anything on fire?".
 *
 * One card per brand, worst first. Two things here are load-bearing and easy to get wrong:
 *
 * 1. `/api/brands` derives last_run_* from the newest **ok** run only (server/api.py::_latest_run_ids
 *    filters `Run.status == "ok"`), so `last_run_status` is always "ok" or null. A brand whose most
 *    recent attempt was REFUSED still reports the previous successful run here. We therefore take the
 *    true newest run of any status from /api/runs and show that instead — otherwise a site that could
 *    not be audited at all would read as "audited, clean".
 * 2. Because of the same filter, the severity counts on a refused/failed brand are *stale* — they came
 *    from the earlier successful run. We say so on the card rather than presenting them as current.
 */

const HISTORY_BARS = 14;

/** Statuses in server/models.py: queued | running | ok | failed | refused. */
const STATUS_LABEL: Record<string, string> = {
  ok: "Audited",
  refused: "Could not be audited",
  failed: "Run failed",
  running: "Running now",
  queued: "Waiting to start",
};

const STATUS_EXPLAIN: Record<string, string> = {
  refused: "The site could not be reached, or gave us no list of its pages. Nothing was checked.",
  failed: "The audit stopped with an error before it finished. Nothing was checked.",
};

function statusLabel(status: string | null): string {
  if (!status) return "Never run";
  return STATUS_LABEL[status] ?? status;
}

function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** The whole card is a link; keep it reading as a card, not as blue link text. */
const cardLink = { display: "block", color: "var(--ink)", textDecoration: "none" } as const;

interface BrandView {
  brand: Brand;
  /** Newest run that reached a verdict (ok/failed/refused). This is what the counts reflect. */
  latest: Run | null;
  /** A queued or running audit for this brand. It has produced nothing yet. */
  inFlight: Run | null;
  /** Up to HISTORY_BARS settled runs, oldest -> newest. */
  history: Run[];
  /** True latest run is not "ok", so the counts shown came from an earlier successful run. */
  stale: boolean;
  /** No successful audit has ever completed — the zeros are not findings. */
  neverAudited: boolean;
}

function buildViews(brands: Brand[], runs: Run[] | undefined): BrandView[] {
  const views = brands.map((brand): BrandView => {
    // /api/runs is ordered started_at DESC, so the first match is the newest run.
    const mine = (runs ?? []).filter((r) => r.brand === brand.code);
    // A queued/running run has NO results yet, so it cannot stand as the brand's latest outcome:
    // otherwise a brand whose last completed attempt was refused would stop reporting that the
    // moment somebody kicked off a new audit, and the refusal — the thing we must never bury —
    // would vanish for the six hours the new crawl takes. Judge on the newest SETTLED run and
    // report the in-flight one alongside it.
    const inFlight = mine.find(isInFlight) ?? null;
    const settled = mine.filter((r) => !isInFlight(r));
    const latest = settled.length > 0 ? settled[0] : null;
    const effectiveStatus = latest ? latest.status : brand.last_run_status;
    return {
      brand,
      latest,
      inFlight,
      history: settled.slice(0, HISTORY_BARS).reverse(),
      stale: effectiveStatus !== null && effectiveStatus !== "ok" && brand.last_run_at !== null,
      neverAudited: brand.last_run_at === null,
    };
  });
  return views.sort(
    (a, b) =>
      b.brand.untriaged_error - a.brand.untriaged_error ||
      b.brand.open_error - a.brand.open_error ||
      a.brand.code.localeCompare(b.brand.code),
  );
}

/** A run that did not complete is not "zero errors" — dim it so it reads as a gap. */
function Sparkline({ history }: { history: Run[] }) {
  const peak = Math.max(1, ...history.map((r) => r.open_error));
  return (
    <div className="spark" title="Errors per run, oldest to newest">
      {history.map((run) => {
        const ok = run.status === "ok";
        return (
          <i
            key={run.id}
            style={{
              height: `${ok ? Math.max(4, (run.open_error / peak) * 100) : 100}%`,
              opacity: ok ? undefined : 0.15,
            }}
            title={
              ok
                ? `${fmtDate(run.started_at)} — ${run.open_error} errors`
                : `${fmtDate(run.started_at)} — ${statusLabel(run.status).toLowerCase()}, no results`
            }
          />
        );
      })}
    </div>
  );
}

function BrandCard({ view, queueAhead }: { view: BrandView; queueAhead: number }) {
  const { brand, latest, inFlight, history, stale, neverAudited } = view;
  const status = latest ? latest.status : brand.last_run_status;
  const explain = status ? STATUS_EXPLAIN[status] : undefined;
  const blocked = status !== null && status !== "ok";

  return (
    // The card is a div rather than a link so the trigger can be a real <button>: a button nested
    // inside an anchor is invalid HTML, and a click on it would navigate instead of starting a run.
    <div className="panel card">
      <Link to={`/findings?brand=${encodeURIComponent(brand.code)}`} style={cardLink}>
        <div className="code">{brand.code}</div>
        <div className="sub">{brand.name}</div>

        {/* Warnings go ABOVE the numbers: a brand that could not be audited must never be read
            as a brand that was audited and came back clean. */}
        {blocked && (
          <div className="banner small">
            <strong className="e">{statusLabel(status)}</strong>
            {explain ? ` ${explain}` : ""}
            {stale && " The counts below are from the last successful audit and may be out of date."}
          </div>
        )}
        {!blocked && neverAudited && (
          <div className="banner small">
            <strong className="e">Never audited.</strong> This brand has no completed run, so the zeros
            below mean “not checked yet”, not “nothing wrong”.
          </div>
        )}

        {/* An audit in progress changes nothing until it finishes — say so, or the numbers below
            look like they are already moving. */}
        {inFlight && (
          <div className="banner small">
            {inFlight.status === "running" ? (
              <>
                <strong>Audit running now</strong> — <Elapsed since={inFlight.started_at} /> so far.
                A full crawl takes hours.{" "}
                {neverAudited
                  ? "There are no earlier results, so the zeros below still mean “not checked yet”."
                  : "The counts below are from the last completed audit and do not move until it finishes."}
              </>
            ) : (
              <>
                <strong>Audit queued</strong> — waiting <Elapsed since={inFlight.started_at} /> for a
                free worker. Nothing has been checked yet.
              </>
            )}
          </div>
        )}

        <div className="sevrow">
          <div>
            <div className="n e">{brand.open_error}</div>
            <div className="l">Errors</div>
          </div>
          <div>
            <div className="n w">{brand.open_warning}</div>
            <div className="l">Warnings</div>
          </div>
          <div>
            <div className="n i">{brand.open_info}</div>
            <div className="l">Info</div>
          </div>
        </div>

        <div style={{ marginTop: 10 }}>
          <span
            className={brand.untriaged_error > 0 ? "e" : "muted"}
            style={{ fontSize: 18, fontWeight: 680 }}
          >
            {brand.untriaged_error}
          </span>{" "}
          <span className="small muted">
            {brand.untriaged_error === 1 ? "error nobody has looked at yet" : "errors nobody has looked at yet"}
          </span>
        </div>

        <div className="small muted" style={{ marginTop: 8 }}>
          {stale ? "Last successful audit: " : "Last run: "}
          {fmtDate(brand.last_run_at)}
          {" · "}
          <span className={blocked ? "e" : undefined}>{statusLabel(status)}</span>
        </div>

        {history.length > 0 && <Sparkline history={history} />}

        {(!brand.scheduled || brand.enumeration_mode === "urls_file" || latest?.partial_sample) && (
          <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
            {!brand.scheduled && (
              <span className="chip" title="Nothing runs this brand automatically. It is only audited when someone starts a run by hand.">
                not scheduled
              </span>
            )}
            {brand.enumeration_mode === "urls_file" && (
              <span className="chip" title="Pages come from a fixed list kept by hand, not from the site itself. Pages added to the site later are never discovered, so they are never audited.">
                fixed page list
              </span>
            )}
            {latest?.partial_sample && (
              <span className="chip" title="This run covered only part of the site, so these counts are a sample and not the full picture.">
                partial sample
              </span>
            )}
          </div>
        )}
      </Link>

      <RunTrigger brandCode={brand.code} inFlight={inFlight} queueAhead={queueAhead} compact />
    </div>
  );
}

export default function Dashboard() {
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: api.brands });
  const runsQ = useQuery({
    queryKey: ["runs", 200],
    queryFn: () => api.runs({ limit: 200 }),
    // Poll only while an audit is actually in flight. A crawl lasts hours, so an idle dashboard
    // re-fetching forever buys nothing — but while one is running the cards have to move on their
    // own, or a six-hour job looks like a hung one.
    refetchInterval: (query) => (anyInFlight(query.state.data) ? RUN_POLL_MS : false),
  });

  if (brandsQ.isLoading) {
    return (
      <div className="wrap">
        <h2>Overview</h2>
        <div className="empty">Loading brands…</div>
      </div>
    );
  }

  if (brandsQ.isError) {
    return (
      <div className="wrap">
        <h2>Overview</h2>
        <div className="panel card">
          <div className="badge e">Could not load brands</div>
          <div className="small muted" style={{ marginTop: 6 }}>{errMessage(brandsQ.error)}</div>
          <div className="controls">
            <button onClick={() => void brandsQ.refetch()}>Try again</button>
          </div>
        </div>
      </div>
    );
  }

  const brands = brandsQ.data ?? [];

  if (brands.length === 0) {
    return (
      <div className="wrap">
        <h2>Overview</h2>
        <div className="empty">
          No brands are set up yet. Once a brand is configured and audited at least once, it will
          appear here.
        </div>
      </div>
    );
  }

  const views = buildViews(brands, runsQ.data);
  const totalErrors = brands.reduce((n, b) => n + b.open_error, 0);
  const totalUntriaged = brands.reduce((n, b) => n + b.untriaged_error, 0);
  const notOk = views.filter((v) => {
    const status = v.latest ? v.latest.status : v.brand.last_run_status;
    return status === null || status !== "ok";
  });
  // The worker runs one audit at a time (concurrency 1), so every in-flight run anywhere is
  // something a newly started brand has to wait behind.
  const inFlight = views.filter((v) => v.inFlight !== null);

  return (
    <div className="wrap">
      <h2>Overview</h2>

      <div className="panel card">
        <div className="sevrow">
          <div>
            <div className="n e">{totalErrors}</div>
            <div className="l">Open errors, all brands</div>
          </div>
          <div>
            <div className={totalUntriaged > 0 ? "n e" : "n"}>{totalUntriaged}</div>
            <div className="l">Errors not looked at yet</div>
          </div>
          <div>
            <div className={notOk.length > 0 ? "n e" : "n"}>{notOk.length}</div>
            <div className="l">Brands not audited</div>
          </div>
        </div>
      </div>

      {runsQ.isError && (
        <div className="banner small">
          Run history could not be loaded, so the per-run charts are hidden and each brand shows its
          last <em>successful</em> audit. A brand that failed or was refused since then will look
          healthier here than it is. ({errMessage(runsQ.error)})
        </div>
      )}

      {inFlight.length > 0 && (
        <div className="banner small">
          <strong>
            Audit in progress: {inFlight.map((v) => v.brand.code).join(", ")}
          </strong>{" "}
          Only one audit runs at a time and a full crawl takes hours, so nothing on this page
          changes until it finishes. Everything below is from the last completed audit.
        </div>
      )}

      {notOk.length > 0 && (
        <div className="banner small">
          <strong>Not audited in the most recent completed run:</strong>{" "}
          {notOk
            .map((v) => {
              const status = v.latest ? v.latest.status : v.brand.last_run_status;
              return `${v.brand.code} (${statusLabel(status).toLowerCase()})`;
            })
            .join(", ")}
          . These sites were not checked, so their numbers below are either old or empty — not a
          clean bill of health.
        </div>
      )}

      <div className="cards">
        {views.map((v) => (
          <BrandCard
            key={v.brand.code}
            view={v}
            queueAhead={inFlight.length - (v.inFlight !== null ? 1 : 0)}
          />
        ))}
      </div>
    </div>
  );
}
