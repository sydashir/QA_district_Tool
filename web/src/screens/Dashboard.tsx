import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../lib/api";
import type { Brand, Run } from "../lib/api";

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
  /** Newest run of ANY status, or null when run history is unavailable. */
  latest: Run | null;
  /** Up to HISTORY_BARS runs, oldest -> newest. */
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
    const latest = mine.length > 0 ? mine[0] : null;
    const effectiveStatus = latest ? latest.status : brand.last_run_status;
    return {
      brand,
      latest,
      history: mine.slice(0, HISTORY_BARS).reverse(),
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

function BrandCard({ view }: { view: BrandView }) {
  const { brand, latest, history, stale, neverAudited } = view;
  const status = latest ? latest.status : brand.last_run_status;
  const explain = status ? STATUS_EXPLAIN[status] : undefined;
  const blocked = status !== null && status !== "ok";

  return (
    <Link to={`/findings?brand=${encodeURIComponent(brand.code)}`} className="panel card" style={cardLink}>
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
  );
}

export default function Dashboard() {
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: api.brands });
  const runsQ = useQuery({ queryKey: ["runs", 200], queryFn: () => api.runs({ limit: 200 }) });

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

      {notOk.length > 0 && (
        <div className="banner small">
          <strong>Not audited in the most recent run:</strong>{" "}
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
          <BrandCard key={v.brand.code} view={v} />
        ))}
      </div>
    </div>
  );
}
