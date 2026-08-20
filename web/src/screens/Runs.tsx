import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { anyInFlight, api, fmtDate, isInFlight, type Brand, type Run } from "../lib/api";
import RunTrigger, { HOURS_WARNING, RUN_POLL_MS } from "../components/RunTrigger";
import Elapsed from "../components/Elapsed";
import RunStatus, { isNoResult, runStatusMeta } from "../components/RunStatus";
import DataCaveat, { enumerationMeta } from "../components/DataCaveat";
import QueryBoundary, { FailureNote } from "../components/QueryBoundary";
import EmptyState from "../components/EmptyState";

/** Every status word on this screen comes from RunStatus, so it matches the other screens exactly. */
const REFUSED = runStatusMeta("refused");

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
  // Only an "ok" run produced numbers. A refused, failed or still-running row has zeros in the
  // database, and printing "0 errors" against a run that never checked a page is the single most
  // dangerous thing this table could do — so those cells show a dash and say why on hover.
  const noResult = isNoResult(run.status);
  const meta = runStatusMeta(run.status);
  const noResultTitle = `${meta.explain} The counts you can see elsewhere are from the previous run.`;
  const found = enumerationMeta(run.enumeration_method);

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
        <RunStatus
          status={run.status}
          errorText={run.error_text}
          cancelRequested={run.cancel_requested}
        />
      </td>

      <td>
        {noResult ? (
          <NoResult title={`${meta.explain} No page count was recorded.`} />
        ) : (
          num(run.pages_audited)
        )}
      </td>

      <td className={noResult || run.open_error === 0 ? undefined : "e"}>
        {noResult ? <NoResult title={noResultTitle} /> : num(run.open_error)}
      </td>

      <td>{noResult ? <NoResult title={noResultTitle} /> : num(run.new_count)}</td>

      <td className="small">
        <span className={run.enumeration_method ? "chip" : "muted"} title={found.title}>
          {run.enumeration_method ? found.label : "—"}
        </span>
      </td>

      <td>
        {/* The status in full words, with the server's own reason underneath. This is the column
            somebody reads when a row does not say "Checked". */}
        {noResult ? (
          <RunStatus
            status={run.status}
            errorText={run.error_text}
            cancelRequested={run.cancel_requested}
            variant="text"
            withExplanation
          />
        ) : null}

        {/* What this run did NOT cover, in the same words the dashboard and findings screens use. */}
        <DataCaveat latest={run} kinds={["partial", "fixed_list"]} variant="inline" />

        {!run.history_written ? (
          <div className="controls" style={{ margin: "6px 0 0" }}>
            <span
              className="chip"
              title="A sampled run deliberately does not move the comparison baseline, so the next run's new/fixed counts stay meaningful."
            >
              baseline not written
            </span>
          </div>
        ) : null}

        {run.status === "running" ? (
          <div className="small muted">Running for <Elapsed since={run.started_at} /> so far.</div>
        ) : null}

        {run.status === "queued" ? (
          <div className="small muted">
            Waiting <Elapsed since={run.started_at} /> for a free worker.
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
  // Counted apart from the two above, and shown without the red treatment, because nothing went
  // wrong here — somebody chose to stop it, usually to get a slow brand out of the single worker's
  // way. It still has to be called out, though: the brand was left part-checked either way.
  const stoppedCount = runs.filter((r) => r.status === "cancelled").length;

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
        them, and records what it found. Each row below is one of those passes, newest first. Only a
        run marked <strong>{runStatusMeta("ok").label}</strong> produced numbers; every other row
        shows a dash rather than a count, because it recorded no result at all.{" "}
        <strong>{REFUSED.label}</strong> means the auditor never got in — the site was unreachable,
        or gave no list of its pages — so not one page was looked at.{" "}
        <strong>{runStatusMeta("failed").label}</strong> means the run stopped part-way.{" "}
        <strong>{runStatusMeta("cancelled").label}</strong> means somebody stopped it on purpose, so
        the pages it had not reached were never looked at. None of the three is a clean result: read
        them as &ldquo;we do not know&rdquo;.
      </p>

      {refusedCount > 0 || failedCount > 0 ? (
        <div className="banner bad">
          <strong>
            {refusedCount > 0
              ? `${refusedCount} run${refusedCount === 1 ? "" : "s"} could not be checked`
              : null}
            {refusedCount > 0 && failedCount > 0 ? " and " : null}
            {failedCount > 0
              ? `${failedCount} run${failedCount === 1 ? "" : "s"} did not finish`
              : null}
          </strong>{" "}
          <span className="small">
            in this list. Those brands were not checked on those runs. Do not read them as
            &ldquo;nothing wrong&rdquo; — read them as &ldquo;we do not know&rdquo;.
          </span>
        </div>
      ) : null}

      {stoppedCount > 0 ? (
        <div className="banner">
          <strong>
            {stoppedCount === 1
              ? "1 run was stopped before it finished"
              : `${stoppedCount} runs were stopped before they finished`}
          </strong>{" "}
          <span className="small">
            in this list. Nothing went wrong — somebody stopped{" "}
            {stoppedCount === 1 ? "it" : "them"} on purpose — but{" "}
            {stoppedCount === 1 ? "that site was" : "those sites were"} left only partly checked, so
            the pages the run never reached were never looked at. Previous findings are unchanged.
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
            The brand list could not be loaded, so the runs below are unfiltered and show their
            brand codes only. <FailureNote query={brandsQuery} label="the brand list" />
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
        <QueryBoundary
          query={brandsQuery}
          label="the brand list"
          skeletonRows={3}
          isEmpty={() => startable.length === 0}
          empty={
            <EmptyState
              tone="warning"
              title={
                brand
                  ? `${brand} is not a configured brand, so it cannot be audited.`
                  : "No brands are configured yet."
              }
              detail={
                brand
                  ? "Clear the brand filter to see the brands that can be started."
                  : "Nothing is set up to audit, so no site is being checked at all."
              }
              action={brand ? <button onClick={() => onBrandChange("")}>Clear filter</button> : undefined}
            />
          }
        >
          {() => (
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
                      {/* The brand's own page cap comes straight from /api/brands, so the button
                          can say "sample" before it is pressed rather than after. The server still
                          resolves the real cap when the run is created — this copy is wording only. */}
                      <RunTrigger
                        brandCode={b.code}
                        inFlight={mine}
                        queueAhead={inFlightByBrand.size - (mine ? 1 : 0)}
                        defaultSampleSize={b.default_sample_size}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          )}
        </QueryBoundary>
      </div>

      <h3>History</h3>

      <div className="panel">
        <QueryBoundary
          query={runsQuery}
          label="the run history"
          skeletonRows={5}
          isEmpty={() => runs.length === 0}
          empty={
            <EmptyState
              tone="warning"
              title={
                brand
                  ? `${brandNames.get(brand) ?? brand} has never been audited.`
                  : "No brand has ever been audited."
              }
              detail={
                brand
                  ? "Not one run has ever been recorded for this brand. Nothing on the site has been checked, which is not the same as the site being clean."
                  : "No run of any kind has been recorded. Nothing schedules these runs, so a brand is only ever audited when somebody starts it above."
              }
              action={brand ? <button onClick={() => onBrandChange("")}>Clear filter</button> : undefined}
            />
          }
        >
          {() => (
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
        </QueryBoundary>
      </div>

      {runs.length > 0 ? (
        <p className="small muted" style={{ marginTop: 10 }}>
          Showing the {runs.length} most recent run{runs.length === 1 ? "" : "s"} (up to 100).
        </p>
      ) : null}
    </div>
  );
}
