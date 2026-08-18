import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { anyInFlight, api, fmtDate, isInFlight, type Brand, type Run } from "../lib/api";
import RunTrigger, { HOURS_WARNING, RUN_POLL_MS } from "../components/RunTrigger";
import Elapsed from "../components/Elapsed";

/** Wording for the one status that must never be mistaken for a clean result. */
const REFUSED_EXPLANATION =
  "could not be audited — the previous results are unchanged; this is NOT a clean result";

interface StatusLook {
  label: string;
  cls: string;
  title: string;
}

function statusLook(status: string): StatusLook {
  switch (status) {
    case "ok":
      return { label: "Completed", cls: "badge", title: "The site was audited from end to end." };
    case "failed":
      return {
        label: "Failed",
        cls: "badge e",
        title: "The run stopped before it finished. Anything below is incomplete.",
      };
    case "refused":
      return { label: "Refused", cls: "badge e", title: `This brand ${REFUSED_EXPLANATION}.` };
    case "running":
      return { label: "Running", cls: "badge muted", title: "Still in progress. Numbers will change." };
    case "queued":
      return { label: "Queued", cls: "badge muted", title: "Waiting to start. Nothing checked yet." };
    default:
      return { label: status, cls: "badge muted", title: `Status reported as "${status}".` };
  }
}

function num(n: number): string {
  return n.toLocaleString();
}

/** For a refused run there are no results at all, so a "0" would read as "all clear". */
function NoResult({ title }: { title: string }) {
  return (
    <span className="muted" title={title}>
      —
    </span>
  );
}

function RunRow({ run, brandName }: { run: Run; brandName: string | undefined }) {
  const look = statusLook(run.status);
  const refused = run.status === "refused";
  const noResultTitle =
    "This run produced no results. The counts you can see elsewhere are from the previous run.";

  return (
    <tr>
      <td>
        <div>
          <strong>{run.brand}</strong>
        </div>
        {brandName ? <div className="small muted">{brandName}</div> : null}
      </td>

      <td className="small">{fmtDate(run.started_at)}</td>

      <td>
        <span className={look.cls} title={look.title}>
          {look.label}
        </span>
      </td>

      <td>
        {refused ? (
          <NoResult title="No pages were reached, so nothing was checked." />
        ) : (
          num(run.pages_audited)
        )}
      </td>

      <td className={refused || run.open_error === 0 ? undefined : "e"}>
        {refused ? <NoResult title={noResultTitle} /> : num(run.open_error)}
      </td>

      <td>{refused ? <NoResult title={noResultTitle} /> : num(run.new_count)}</td>

      <td className="small">
        {run.enumeration_method ? (
          <span className="chip">{run.enumeration_method}</span>
        ) : (
          <span className="muted" title="Not recorded for this run.">
            —
          </span>
        )}
      </td>

      <td>
        {refused ? (
          <div className="e small" title={`This brand ${REFUSED_EXPLANATION}.`}>
            This brand {REFUSED_EXPLANATION}.
          </div>
        ) : null}

        {run.status === "failed" && run.error_text ? (
          <div className="snippet" style={{ marginTop: refused ? 6 : 0 }}>
            {run.error_text}
          </div>
        ) : null}

        {refused && run.error_text ? (
          <div className="snippet" style={{ marginTop: 6 }}>
            {run.error_text}
          </div>
        ) : null}

        {run.partial_sample || !run.history_written ? (
          <div className="controls" style={{ margin: "6px 0 0" }}>
            {run.partial_sample ? (
              <span
                className="chip w"
                title="Only part of the site was checked on this run, so this is not a full picture of the brand."
              >
                partial sample
              </span>
            ) : null}
            {!run.history_written ? (
              <span
                className="chip"
                title="A sampled run deliberately does not move the comparison baseline, so the next run's new/fixed counts stay meaningful."
              >
                baseline not written
              </span>
            ) : null}
          </div>
        ) : null}

        {run.status === "running" ? (
          <div className="small muted">
            Running for <Elapsed since={run.started_at} />. Not finished — these numbers are not
            final, and a full crawl takes hours.
          </div>
        ) : null}

        {run.status === "queued" ? (
          <div className="small muted">
            Waiting <Elapsed since={run.started_at} /> for a free worker. Nothing has been checked
            yet.
          </div>
        ) : null}

        {run.status === "ok" && run.finished_at ? (
          <div className="small muted">Finished {fmtDate(run.finished_at)}</div>
        ) : null}
      </td>
    </tr>
  );
}

export default function Runs() {
  const [searchParams, setSearchParams] = useSearchParams();
  const brand = searchParams.get("brand") ?? "";

  const brandsQuery = useQuery<Brand[]>({
    queryKey: ["brands"],
    queryFn: () => api.brands(),
  });

  const runsQuery = useQuery<Run[]>({
    queryKey: ["runs", brand],
    queryFn: () => api.runs({ brand: brand || undefined, limit: 100 }),
    // Poll only while something is actually queued or running. A crawl lasts hours, so an idle
    // screen re-fetching forever buys nothing; the moment a run is in flight, the rows have to
    // move on their own or the page looks frozen.
    refetchInterval: (query) => (anyInFlight(query.state.data) ? RUN_POLL_MS : false),
  });

  // When the table is filtered to one brand it cannot see the other brands' runs — and the queue
  // warning ("this starts after N others") is only truthful if it counts ALL of them. Unfiltered,
  // the list above already IS the fleet view, so this second request is not made.
  const fleetQuery = useQuery<Run[]>({
    queryKey: ["runs", 200],
    queryFn: () => api.runs({ limit: 200 }),
    enabled: brand !== "",
    refetchInterval: (query) => (anyInFlight(query.state.data) ? RUN_POLL_MS : false),
  });

  const brandNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const b of brandsQuery.data ?? []) map.set(b.code, b.name);
    return map;
  }, [brandsQuery.data]);

  const runs = useMemo(() => {
    const rows = [...(runsQuery.data ?? [])];
    rows.sort((a, b) => {
      const d = new Date(b.started_at).getTime() - new Date(a.started_at).getTime();
      return d !== 0 && !isNaN(d) ? d : b.id - a.id;
    });
    return rows;
  }, [runsQuery.data]);

  const refusedCount = runs.filter((r) => r.status === "refused").length;
  const failedCount = runs.filter((r) => r.status === "failed").length;

  // Newest-first in both sources, so the first in-flight run found for a brand is its current one.
  const fleetRuns = useMemo(
    () => (brand !== "" ? (fleetQuery.data ?? []) : runs),
    [brand, fleetQuery.data, runs],
  );

  const inFlightByBrand = useMemo(() => {
    const map = new Map<string, Run>();
    for (const r of fleetRuns) if (isInFlight(r) && !map.has(r.brand)) map.set(r.brand, r);
    return map;
  }, [fleetRuns]);

  const startable = useMemo(() => {
    const all = brandsQuery.data ?? [];
    return brand !== "" ? all.filter((b) => b.code === brand) : all;
  }, [brandsQuery.data, brand]);

  function onBrandChange(next: string) {
    const params = new URLSearchParams(searchParams);
    if (next) params.set("brand", next);
    else params.delete("brand");
    setSearchParams(params);
  }

  return (
    <div className="wrap">
      <h2>Run history</h2>

      <p className="muted small" style={{ maxWidth: 860 }}>
        A run is one pass of the auditor over a brand&rsquo;s website: it visits the pages, checks
        them, and records what it found. Each row below is one of those passes, newest first. A run
        marked <strong>Refused</strong> means the site {REFUSED_EXPLANATION} — the auditor never got
        in, so it never looked at a single page.
      </p>

      {refusedCount > 0 || failedCount > 0 ? (
        <div className="banner">
          <strong>
            {refusedCount > 0
              ? `${refusedCount} run${refusedCount === 1 ? "" : "s"} refused`
              : null}
            {refusedCount > 0 && failedCount > 0 ? " and " : null}
            {failedCount > 0 ? `${failedCount} run${failedCount === 1 ? "" : "s"} failed` : null}
          </strong>{" "}
          <span className="small">
            in this list. Those brands were not checked on those runs. Do not read them as
            &ldquo;nothing wrong&rdquo; — read them as &ldquo;we do not know&rdquo;.
          </span>
        </div>
      ) : null}

      <div className="controls">
        <label htmlFor="brand-filter" className="small muted">
          Brand
        </label>
        <select
          id="brand-filter"
          value={brand}
          onChange={(e) => onBrandChange(e.target.value)}
          disabled={brandsQuery.isPending || brandsQuery.isError}
        >
          <option value="">All brands</option>
          {(brandsQuery.data ?? []).map((b) => (
            <option key={b.code} value={b.code}>
              {b.code} — {b.name}
            </option>
          ))}
        </select>

        {brand ? (
          <button onClick={() => onBrandChange("")}>Clear filter</button>
        ) : null}

        {brandsQuery.isError ? (
          <span className="small e">
            Could not load the brand list ({(brandsQuery.error as Error).message}). The runs below
            are unfiltered.
          </span>
        ) : null}

        {runsQuery.isFetching && !runsQuery.isPending ? (
          <span className="small muted">Refreshing…</span>
        ) : null}
      </div>

      <h3>Start an audit</h3>

      <p className="muted small" style={{ maxWidth: 860 }}>
        {HOURS_WARNING} Nothing schedules these runs, so a brand is only ever audited when someone
        starts it here.
        {brand ? " Clear the brand filter above to start a different brand." : null}
      </p>

      <div className="panel">
        {brandsQuery.isPending ? (
          <div className="empty">Loading brands…</div>
        ) : brandsQuery.isError ? (
          <div className="empty">
            The brand list could not be loaded, so there is nothing to start from here.
          </div>
        ) : startable.length === 0 ? (
          <div className="empty">
            {brand
              ? `${brand} is not a configured brand, so it cannot be audited.`
              : "No brands are configured yet."}
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Brand</th>
                <th>Audit</th>
              </tr>
            </thead>
            <tbody>
              {startable.map((b) => {
                const mine = inFlightByBrand.get(b.code) ?? null;
                return (
                  <tr key={b.code}>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <div>
                        <strong>{b.code}</strong>
                      </div>
                      <div className="small muted">{b.name}</div>
                    </td>
                    <td>
                      <RunTrigger
                        brandCode={b.code}
                        inFlight={mine}
                        queueAhead={inFlightByBrand.size - (mine ? 1 : 0)}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <h3>History</h3>

      <div className="panel">
        {runsQuery.isPending ? (
          <div className="empty">Loading run history…</div>
        ) : runsQuery.isError ? (
          <div className="empty">
            <div className="e">
              <strong>Could not load the run history.</strong>
            </div>
            <div className="small" style={{ marginTop: 6 }}>
              {(runsQuery.error as Error).message}
            </div>
            <div className="controls" style={{ justifyContent: "center" }}>
              <button onClick={() => void runsQuery.refetch()}>Try again</button>
            </div>
          </div>
        ) : runs.length === 0 ? (
          <div className="empty">
            {brand
              ? `No runs recorded for ${brandNames.get(brand) ?? brand} yet. Nothing has audited this brand, which is not the same as this brand being clean.`
              : "No runs recorded yet. Once the auditor has been run against a brand, every pass will be listed here."}
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Brand</th>
                <th>Started</th>
                <th>Status</th>
                <th>Pages checked</th>
                <th>Open errors</th>
                <th>New this run</th>
                <th>How pages were found</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <RunRow key={run.id} run={run} brandName={brandNames.get(run.brand)} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {runs.length > 0 ? (
        <p className="small muted" style={{ marginTop: 10 }}>
          Showing the {runs.length} most recent run{runs.length === 1 ? "" : "s"} (up to 100).
        </p>
      ) : null}
    </div>
  );
}
