/**
 * "What changed" — the morning screen. Shows the difference between a brand's latest completed
 * run and the run before it: problems that appeared, problems that went away, and pages that are
 * new to the site.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api, fmtDate, SEVERITY_ORDER } from "../lib/api";
import type { ChangeRow, Run, Severity } from "../lib/api";

/** The API caps each list at 200 rows; say so rather than quietly truncating. */
const ROW_CAP = 200;

const SEV_CLASS: Record<Severity, string> = { error: "e", warning: "w", info: "i" };

/** Errors first, then the faults that hit the most pages. */
function sortRows(rows: ChangeRow[]): ChangeRow[] {
  return [...rows].sort(
    (a, b) =>
      SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity) ||
      b.page_count - a.page_count,
  );
}

function ChangeTable({ rows, pagesHeader }: { rows: ChangeRow[]; pagesHeader: string }) {
  return (
    <div className="panel">
      <table>
        <thead>
          <tr>
            <th style={{ width: 90 }}>Severity</th>
            <th style={{ width: 190 }}>Kind of problem</th>
            <th>What it is</th>
            <th style={{ width: 150 }}>{pagesHeader}</th>
          </tr>
        </thead>
        <tbody>
          {sortRows(rows).map((r) => (
            <tr key={r.fingerprint_hash}>
              <td>
                <span className={`badge ${SEV_CLASS[r.severity]}`}>{r.severity}</span>
              </td>
              <td>
                <span className="chip">{r.check_label}</span>
              </td>
              <td>
                <Link to={`/findings/${r.fingerprint_hash}`}>{r.issue}</Link>
                <div className="url">{r.url}</div>
              </td>
              <td>
                {r.page_count > 1 ? (
                  <>
                    <div>
                      on <strong>{r.page_count.toLocaleString()}</strong> pages
                    </div>
                    <div className="muted small">same fault repeated — likely one fix</div>
                  </>
                ) : (
                  <span className="muted">on 1 page</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Section({
  title,
  blurb,
  rows,
  emptyText,
  pagesHeader,
}: {
  title: string;
  blurb: string;
  rows: ChangeRow[];
  emptyText: string;
  pagesHeader: string;
}) {
  return (
    <>
      <h2>
        {title} ({rows.length.toLocaleString()})
      </h2>
      <p className="muted small">{blurb}</p>
      {rows.length === 0 ? (
        <div className="panel">
          <div className="empty">{emptyText}</div>
        </div>
      ) : (
        <>
          <ChangeTable rows={rows} pagesHeader={pagesHeader} />
          {rows.length >= ROW_CAP && (
            <p className="muted small">
              Showing the first {ROW_CAP} only — there may be more in this run.
            </p>
          )}
        </>
      )}
    </>
  );
}

/** Plain-English reason a run produced no comparison. */
function runStatusNote(run: Run): string {
  if (run.status === "refused")
    return "This brand COULD NOT be audited — the site was unreachable or gave us no list of pages. Nothing was checked, so this is not a clean bill of health.";
  if (run.status === "ok") return "This run finished normally.";
  return `This run ended with the status "${run.status}", so its results may be incomplete.`;
}

export default function Changes() {
  const [params, setParams] = useSearchParams();

  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: api.brands });
  const brands = brandsQ.data ?? [];
  const brand = params.get("brand") ?? brands[0]?.code ?? "";

  const changesQ = useQuery({
    queryKey: ["changes", brand],
    queryFn: () => api.changes(brand),
    enabled: brand !== "",
  });
  // Only used to describe the run (partial sample / a newer failed attempt). Never blocks the page.
  const runsQ = useQuery({
    queryKey: ["runs", brand, 5],
    queryFn: () => api.runs({ brand, limit: 5 }),
    enabled: brand !== "",
  });

  function pickBrand(code: string) {
    const next = new URLSearchParams(params);
    next.set("brand", code);
    setParams(next);
  }

  const selector = (
    <div className="controls">
      <label className="muted small" htmlFor="brand">
        Brand
      </label>
      <select
        id="brand"
        value={brand}
        disabled={brands.length === 0}
        onChange={(e) => pickBrand(e.target.value)}
      >
        {brands.map((b) => (
          <option key={b.code} value={b.code}>
            {b.code} — {b.name}
          </option>
        ))}
      </select>
    </div>
  );

  if (brandsQ.isPending) {
    return (
      <>
        <h2>What changed</h2>
        <div className="panel">
          <div className="empty">Loading brands…</div>
        </div>
      </>
    );
  }

  if (brandsQ.isError) {
    return (
      <>
        <h2>What changed</h2>
        <div className="panel">
          <div className="empty">
            Could not load the brand list: {(brandsQ.error as Error).message}
          </div>
        </div>
      </>
    );
  }

  if (brands.length === 0) {
    return (
      <>
        <h2>What changed</h2>
        <div className="panel">
          <div className="empty">No brands are set up yet, so there is nothing to compare.</div>
        </div>
      </>
    );
  }

  const runs = runsQ.data ?? [];
  const latestAttempt: Run | undefined = runs[0];
  const data = changesQ.data;
  const comparedRun: Run | undefined = runs.find((r) => r.id === data?.run_id);

  return (
    <>
      <h2>What changed</h2>
      <p className="muted small">
        Everything below is the difference between this brand's most recent completed audit and the
        one before it — not the full list of open problems.
      </p>

      {selector}

      {/* A newer attempt that did not complete must never be hidden behind older, cleaner numbers. */}
      {latestAttempt && latestAttempt.status !== "ok" && (
        <div className="banner">
          <strong className="e">
            The most recent attempt for {brand} ({fmtDate(latestAttempt.started_at)}) did not
            complete — status "{latestAttempt.status}".
          </strong>{" "}
          {runStatusNote(latestAttempt)}
          {latestAttempt.error_text ? ` (${latestAttempt.error_text})` : ""}
          {data?.run_id
            ? ` The comparison below is from the last run that did finish, #${data.run_id}, so it may be out of date.`
            : ""}
        </div>
      )}

      {changesQ.isPending && (
        <div className="panel">
          <div className="empty">Loading changes for {brand}…</div>
        </div>
      )}

      {changesQ.isError && (
        <div className="panel">
          <div className="empty">
            Could not load changes for {brand}: {(changesQ.error as Error).message}
          </div>
        </div>
      )}

      {data && data.run_id === null && (
        <div className="panel">
          <div className="empty">
            {brand} has no completed audit yet, so there is nothing to compare against. This is not
            "audited and clean" — the site has not been successfully checked at all.
            {latestAttempt ? ` Last attempt: ${fmtDate(latestAttempt.started_at)} — ${runStatusNote(latestAttempt)}` : ""}
          </div>
        </div>
      )}

      {data && data.run_id !== null && (
        <>
          <div className="panel card">
            <div className="code">
              {brand} — comparing run #{data.run_id} against the previous run of the same brand
            </div>
            <div className="sub">
              {comparedRun
                ? `Started ${fmtDate(comparedRun.started_at)} · ${comparedRun.pages_audited.toLocaleString()} pages checked`
                : "Run details unavailable."}
            </div>
            <div className="sevrow">
              <div>
                <div className="n e">{data.new.length.toLocaleString()}</div>
                <div className="l">New problems</div>
              </div>
              <div>
                <div className="n">{data.resolved.length.toLocaleString()}</div>
                <div className="l">Fixed</div>
              </div>
              <div>
                <div className="n i">{data.new_pages.length.toLocaleString()}</div>
                <div className="l">New pages</div>
              </div>
            </div>
          </div>

          {comparedRun?.partial_sample && (
            <div className="banner">
              <strong>Partial sample.</strong> This run did not cover the whole site, so anything on
              a page it never reached is missing from these lists. Treat the counts as a floor, not a
              total.
            </div>
          )}

          <Section
            title="New problems"
            blurb="These appeared in the latest run and were not there before. Errors first, then whatever affects the most pages — a high page count is usually one shared template fault, so it is one fix, not one per page."
            rows={data.new}
            emptyText="No new problems in the latest run. Nothing broke since the previous audit."
            pagesHeader="Pages affected"
          />

          <Section
            title="Fixed since last run"
            blurb="These were reported before and are gone now — no action needed, this is the work paying off."
            rows={data.resolved}
            emptyText="Nothing has dropped off the list since the previous run yet."
            pagesHeader="Pages it had affected"
          />

          <h2>New pages discovered ({data.new_pages.length.toLocaleString()})</h2>
          <p className="muted small">
            Pages seen for the first time in this run. Worth a look even when they are not flagged: a
            brand-new page with a dead button or a wrong phone number does more damage than an old
            one, because nobody has ever eyeballed it.
          </p>
          {data.new_pages.length === 0 ? (
            <div className="panel">
              <div className="empty">No new pages appeared in this run.</div>
            </div>
          ) : (
            <>
              <div className="panel">
                <table>
                  <thead>
                    <tr>
                      <th>Page (opens on the live site)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.new_pages.map((u) => (
                      <tr key={u}>
                        <td>
                          <a href={u} target="_blank" rel="noreferrer">
                            {u}
                          </a>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.new_pages.length >= ROW_CAP && (
                <p className="muted small">
                  Showing the first {ROW_CAP} only — there may be more in this run.
                </p>
              )}
            </>
          )}
        </>
      )}
    </>
  );
}
