/**
 * Findings — the working screen. The QA team lives here.
 *
 * Every filter is held in the URL (useSearchParams) so a row of work is shareable by link.
 * Paging is server-side: the corpus is ~35k open rows across nine brands and must never be
 * fetched whole. Triage is edited inline, in place, with a per-row saved/failed indicator —
 * if the team can't see that a change stuck, they will make it twice.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { anyInFlight, api, fmtDate, fpHash, isInFlight, SEVERITY_ORDER, humanisePhones } from "../lib/api";
import type { Finding, Run, Severity, TriageState } from "../lib/api";
import { RUN_POLL_MS } from "../components/RunTrigger";
import RunStatus, { runStatusLabel, runStatusMeta } from "../components/RunStatus";
import DataCaveat from "../components/DataCaveat";
import Explain from "../components/Explain";
import QueryBoundary, { FailureNote } from "../components/QueryBoundary";
import EmptyState from "../components/EmptyState";

const SEV_CLASS: Record<Severity, string> = { error: "e", warning: "w", info: "i" };
const SEV_LABEL: Record<Severity, string> = { error: "Error", warning: "Warning", info: "Info" };
const SEV_FILTER_LABEL: Record<Severity, string> = {
  error: "Errors only",
  warning: "Warnings only",
  info: "Info only",
};

const TRIAGE_STATES: TriageState[] = ["open", "acknowledged", "wontfix", "fixed"];
const TRIAGE_LABEL: Record<TriageState, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  wontfix: "Won't fix",
  fixed: "Fixed",
};

const PER_PAGE_CHOICES = [25, 50, 100, 200];
const DEFAULT_PER_PAGE = 50;

/** Per-row feedback for the inline triage control. `idle` keeps the chosen value showing
 *  after the confirmation text has faded, so the select never flickers back. */
type RowSave =
  | { kind: "saving"; value: TriageState }
  | { kind: "saved"; value: TriageState }
  | { kind: "idle"; value: TriageState }
  | { kind: "failed"; value: TriageState; message: string };

/** Drop the scheme and leading www so the path — the part that identifies the page — reads first. */
function shortUrl(u: string): string {
  return u.replace(/^https?:\/\//, "").replace(/^www\./, "");
}

export default function Findings() {
  const [params, setParams] = useSearchParams();
  const qc = useQueryClient();

  const brand = params.get("brand") ?? "";
  const check = params.get("check") ?? "";
  const severity = params.get("severity") ?? "";
  const triageFilter = params.get("state") ?? "";
  const q = params.get("q") ?? "";
  const page = Math.max(1, Number(params.get("page") ?? "1") || 1);
  const perPageRaw = Number(params.get("per_page") ?? "") || DEFAULT_PER_PAGE;
  const perPage = PER_PAGE_CHOICES.includes(perPageRaw) ? perPageRaw : DEFAULT_PER_PAGE;

  const anyFilter = Boolean(brand || check || severity || triageFilter || q);

  /* Functional updater: a debounced search write must not clobber a filter changed meanwhile. */
  const setFilter = useCallback(
    (key: string, value: string) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value) next.set(key, value);
          else next.delete(key);
          if (key !== "page") next.delete("page"); // a changed filter always returns to page 1
          return next;
        },
        { replace: key === "q" },
      );
    },
    [setParams],
  );

  // ---- search box: debounced 300ms so typing doesn't fire a query per keystroke -------------
  const [qInput, setQInput] = useState(q);
  useEffect(() => {
    const t = setTimeout(() => {
      if (qInput !== q) setFilter("q", qInput);
    }, 300);
    return () => clearTimeout(t);
  }, [qInput, q, setFilter]);

  function clearFilters() {
    setQInput("");
    const next = new URLSearchParams();
    if (perPage !== DEFAULT_PER_PAGE) next.set("per_page", String(perPage));
    setParams(next);
  }

  // ---- data --------------------------------------------------------------------------------
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: api.brands });
  const checksQ = useQuery({ queryKey: ["checks"], queryFn: api.checks });
  /**
   * The fleet's run history, not just the selected brand's.
   *
   * Two reasons it has to be the whole fleet. First, `/api/brands` derives `last_run_status` from
   * the newest **ok** run only (server/api.py::_latest_run_ids), so a brand whose latest attempt
   * was refused still reports "ok" there — the truth about the newest attempt only exists in
   * /api/runs. Second, with no brand selected this screen mixes every brand together, and it has
   * to be able to name the ones whose rows are left over from an older audit.
   *
   * Same key and limit as the dashboard, so the two screens share one cached copy.
   */
  const runsQ = useQuery({
    queryKey: ["runs", 200],
    queryFn: () => api.runs({ limit: 200 }),
    refetchInterval: (query) => (anyInFlight(query.state.data) ? RUN_POLL_MS : false),
  });

  const findingsQ = useQuery({
    queryKey: ["findings", { brand, check, severity, triageFilter, q, page, perPage }],
    queryFn: () =>
      api.findings({
        brand: brand || undefined,
        check: check || undefined,
        severity: severity || undefined,
        state: triageFilter || undefined,
        q: q || undefined,
        page,
        per_page: perPage,
      }),
    placeholderData: keepPreviousData,
  });

  const total = findingsQ.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / perPage));

  /* The API orders errors first; this stable re-sort is a no-op then, and a safeguard if not.
     It only ever reorders the rows already on this page — global order stays the server's. */
  const rows: Finding[] = useMemo(() => {
    const items = findingsQ.data?.items ?? [];
    return [...items].sort(
      (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
    );
  }, [findingsQ.data]);

  // ---- fingerprint -> sha256 hash (needed for both the detail link and the triage PATCH) ----
  const [hashes, setHashes] = useState<Record<string, string>>({});
  useEffect(() => {
    if (rows.length === 0) return;
    let cancelled = false;
    void (async () => {
      const pairs = await Promise.all(
        rows.map(async (f) => [f.fingerprint, await fpHash(f.fingerprint)] as const),
      );
      if (!cancelled) setHashes((prev) => ({ ...prev, ...Object.fromEntries(pairs) }));
    })();
    return () => {
      cancelled = true;
    };
  }, [rows]);

  // ---- inline triage -----------------------------------------------------------------------
  const [saves, setSaves] = useState<Record<string, RowSave>>({});
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const t of Object.values(pending)) clearTimeout(t);
    };
  }, []);

  const triage = useMutation({
    mutationFn: (v: { fingerprint: string; hash: string; state: TriageState }) =>
      api.setTriage(v.hash, { state: v.state }),
    onMutate: (v) => {
      clearTimeout(timers.current[v.fingerprint]);
      setSaves((s) => ({ ...s, [v.fingerprint]: { kind: "saving", value: v.state } }));
    },
    onSuccess: async (_data, v) => {
      setSaves((s) => ({ ...s, [v.fingerprint]: { kind: "saved", value: v.state } }));
      // Fade the "Saved" confirmation after a beat, but keep the chosen value on the control.
      timers.current[v.fingerprint] = setTimeout(() => {
        setSaves((s) => {
          const cur = s[v.fingerprint];
          if (!cur || cur.kind !== "saved") return s;
          return { ...s, [v.fingerprint]: { kind: "idle", value: cur.value } };
        });
      }, 2500);
      await qc.invalidateQueries({ queryKey: ["findings"] });
      await qc.invalidateQueries({ queryKey: ["brands"] });
    },
    onError: (err: Error, v) => {
      setSaves((s) => ({
        ...s,
        [v.fingerprint]: { kind: "failed", value: v.state, message: err.message },
      }));
    },
  });

  // ---- run health: a run that produced nothing must never read as "audited, found nothing" ---
  const selectedBrand = brandsQ.data?.find((b) => b.code === brand);

  /* /api/runs is ordered started_at DESC. A queued/running run has produced nothing yet, so the
     newest SETTLED run is the one these findings came from — and it is the one whose status
     decides whether they are current. */
  const brandRuns = useMemo(
    () => (brand === "" ? [] : (runsQ.data ?? []).filter((r) => r.brand === brand)),
    [brand, runsQ.data],
  );
  const latestRun: Run | undefined = brandRuns.find((r) => !isInFlight(r));
  const inFlightRun: Run | null = brandRuns.find(isInFlight) ?? null;

  /** The latest attempt produced no result (refused, did not finish, or an unknown status). */
  const notChecked = latestRun !== undefined && latestRun.status !== "ok";
  const partial = latestRun?.partial_sample === true;

  /* Brands whose newest settled run is not "ok". Deliberately NOT read from Brand.last_run_status:
     that field only ever reports the last successful run, so a refusal is invisible in it. */
  const degradedBrands = useMemo(() => {
    if (brand !== "" || runsQ.data === undefined) return [];
    const newest = new Map<string, Run>();
    for (const r of runsQ.data) if (!isInFlight(r) && !newest.has(r.brand)) newest.set(r.brand, r);
    return [...newest.values()].filter((r) => r.status !== "ok");
  }, [brand, runsQ.data]);

  const brandList = brandsQ.data ?? [];
  const neverAudited = brandList.filter((b) => b.last_run_at === null);
  /** A brand chosen with nothing else narrowing it — so zero rows is a statement about that brand. */
  const onlyBrandFilter =
    brand !== "" && check === "" && severity === "" && triageFilter === "" && q === "";

  /**
   * What zero rows MEAN here. "We checked and found nothing" and "we never checked" draw the same
   * empty table, so this settles which one it is from the brand's last run before saying a word.
   */
  function findingsEmpty() {
    const clear = <button onClick={clearFilters}>Clear filters</button>;

    if (brand !== "" && notChecked && latestRun !== undefined) {
      return (
        <EmptyState
          tone="warning"
          title={`Nothing to show — ${selectedBrand?.name ?? brand}: ${runStatusLabel(latestRun.status).toLowerCase()}.`}
          detail={`${runStatusMeta(latestRun.status).explain} An empty list here means we do not know — not that the site is clean.`}
          action={anyFilter ? clear : undefined}
        />
      );
    }

    if (brand !== "" && selectedBrand !== undefined && selectedBrand.last_run_at === null) {
      return (
        <EmptyState
          tone="warning"
          title={`${selectedBrand.name} has never been audited.`}
          detail="No run has ever completed for this brand, so nothing on the site has been checked. Start one from the Runs screen — a full audit takes hours."
          action={anyFilter ? clear : undefined}
        />
      );
    }

    if (onlyBrandFilter) {
      const audited =
        selectedBrand?.last_run_at != null
          ? `Its last completed audit (${fmtDate(selectedBrand.last_run_at)}) left nothing open.`
          : "Its last completed audit left nothing open.";
      return (
        <EmptyState
          tone={partial ? "warning" : "neutral"}
          title={`Nothing is open for ${selectedBrand?.name ?? brand}.`}
          detail={
            partial
              ? `${audited} That run covered only part of the site, though, so pages it never reached were not checked at all.`
              : `${audited} Findings you have marked fixed or won't-fix are hidden unless you filter for them.`
          }
          action={clear}
        />
      );
    }

    if (anyFilter) {
      return (
        <EmptyState
          title="No findings match these filters."
          detail="Nothing has been ruled out — these filters simply match nothing. Widen the search, or clear them to see everything."
          action={clear}
        />
      );
    }

    // No filters at all: the emptiness is a statement about the whole product, so it has to be
    // clear whether the sites are clean or whether nobody has ever looked at them.
    if (brandList.length > 0 && neverAudited.length === brandList.length) {
      return (
        <EmptyState
          tone="warning"
          title="No site has been audited yet."
          detail="Not one brand has a completed run, so this empty list means “nothing has been checked”, not “nothing is wrong”. Start an audit from the Runs screen."
        />
      );
    }

    // Brands nobody knows anything about: never audited, or their newest settled run produced no
    // result. They contribute no rows either way, so an all-clear must name them.
    const unknown = [
      ...new Set([...neverAudited.map((b) => b.code), ...degradedBrands.map((r) => r.brand)]),
    ].sort();

    return (
      <EmptyState
        tone={unknown.length > 0 ? "warning" : "neutral"}
        title="No findings are open."
        detail={
          unknown.length > 0
            ? `Every brand that was audited came back with nothing outstanding — but ${unknown.join(", ")} ${unknown.length === 1 ? "was" : "were"} not audited at all, so ${unknown.length === 1 ? "it contributes" : "they contribute"} nothing here either way.`
            : "Every brand's last completed audit came back with nothing outstanding. Findings marked fixed or won't-fix are hidden unless you filter for them."
        }
      />
    );
  }

  return (
    <div className="wrap">
      <h2>Findings</h2>

      <div className="controls">
        <select
          value={brand}
          onChange={(e) => setFilter("brand", e.target.value)}
          aria-label="Brand"
        >
          <option value="">All brands</option>
          {(brandsQ.data ?? []).map((b) => (
            <option key={b.code} value={b.code}>
              {b.name} ({b.code})
            </option>
          ))}
        </select>

        <select
          value={check}
          onChange={(e) => setFilter("check", e.target.value)}
          aria-label="Type of issue"
        >
          <option value="">All types of issue</option>
          {(checksQ.data ?? []).map((c) => (
            <option key={c.check} value={c.check}>
              {c.label} ({c.count.toLocaleString()})
            </option>
          ))}
        </select>

        <select
          value={severity}
          onChange={(e) => setFilter("severity", e.target.value)}
          aria-label="Severity"
        >
          <option value="">Any severity</option>
          {SEVERITY_ORDER.map((s) => (
            <option key={s} value={s}>
              {SEV_FILTER_LABEL[s]}
            </option>
          ))}
        </select>

        <select
          value={triageFilter}
          onChange={(e) => setFilter("state", e.target.value)}
          aria-label="Triage state"
        >
          <option value="">Any triage state</option>
          {TRIAGE_STATES.map((s) => (
            <option key={s} value={s}>
              {TRIAGE_LABEL[s]}
            </option>
          ))}
        </select>

        <input
          type="search"
          value={qInput}
          placeholder="Search the issue text or page URL…"
          onChange={(e) => setQInput(e.target.value)}
          aria-label="Search findings"
        />

        {anyFilter && <button onClick={clearFilters}>Clear filters</button>}
      </div>

      {brandsQ.isError && (
        <div className="banner">
          The brand filter could not be loaded, so you cannot narrow this list to one brand — and any
          warning that a brand was refused or never audited is missing too. The findings below are
          unaffected. <FailureNote query={brandsQ} label="the brand list" />
        </div>
      )}

      {/* Whose findings these are, and how the audit that produced them ended. */}
      {brand !== "" && latestRun !== undefined && (
        <div className="small muted" style={{ marginTop: 10 }}>
          {selectedBrand?.name ?? brand} · audit of {fmtDate(latestRun.started_at)} ·{" "}
          <RunStatus status={latestRun.status} errorText={latestRun.error_text} variant="text" />
        </div>
      )}

      {/* Caveats sit above the count and the table, in the same words as the dashboard cards. */}
      {brand !== "" && (
        <DataCaveat
          latest={runsQ.data === undefined ? undefined : (latestRun ?? null)}
          inFlight={inFlightRun}
          enumerationMode={selectedBrand?.enumeration_mode}
        />
      )}

      {/* The reason, in the server's own words, when the latest attempt produced nothing. */}
      {brand !== "" && notChecked && latestRun !== undefined && latestRun.error_text !== null && (
        <div className="small muted" style={{ margin: "-6px 0 12px" }}>
          Reported reason: {latestRun.error_text}
        </div>
      )}

      {/* Unfiltered, the list silently omits whatever the degraded brands would have contributed. */}
      {brand === "" && degradedBrands.length > 0 && (
        <div className="banner bad">
          <strong className="e">
            {degradedBrands.length === 1
              ? "1 brand is represented by an older audit:"
              : `${degradedBrands.length} brands are represented by an older audit:`}
          </strong>{" "}
          {degradedBrands.map((r) => `${r.brand} (${runStatusLabel(r.status).toLowerCase()})`).join(", ")}
          . Their most recent attempt produced no results, so their rows below are left over from
          the last audit that finished — and anything that has gone wrong on those sites since is
          not here at all.
        </div>
      )}

      <div className="small muted" style={{ margin: "10px 0" }}>
        {findingsQ.isError ? (
          "—"
        ) : findingsQ.isLoading ? (
          "Counting findings…"
        ) : (
          <>
            <strong>{total.toLocaleString()}</strong> {total === 1 ? "finding" : "findings"}
            <Explain term="finding" />
            {anyFilter ? " match these filters" : " in total"}
            {findingsQ.isFetching ? " · refreshing…" : ""}
          </>
        )}
      </div>

      <div className="panel" style={{ overflowX: "auto" }}>
        <QueryBoundary
          query={findingsQ}
          label="the findings"
          skeletonRows={6}
          isEmpty={() => rows.length === 0}
          empty={findingsEmpty()}
        >
          {() => (
          /* `stack-sm` + the `data-label` on every cell: below 900px this six-column table
             becomes one stacked card per finding, with each column name printed above its value.
             See the narrow-viewport block in styles.css. */
          <table className="stack-sm">
            <thead>
              <tr>
                <th style={{ width: 100 }}>
                  Severity
                  <Explain term="severity" />
                </th>
                <th style={{ width: 150 }}>Type of issue</th>
                <th>Issue</th>
                <th style={{ width: 340 }}>Where</th>
                <th style={{ width: 130 }}>First seen</th>
                <th style={{ width: 190 }}>
                  Triage
                  <Explain term="triage" />
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => {
                const hash = hashes[f.fingerprint];
                const save = saves[f.fingerprint];
                const value = save?.value ?? f.triage_state;
                return (
                  <tr key={f.id}>
                    <td data-label="Severity">
                      <span className={`badge ${SEV_CLASS[f.severity]}`}>
                        {SEV_LABEL[f.severity]}
                      </span>
                    </td>
                    <td data-label="Type of issue">
                      <span className="chip">{f.check_label}</span>
                      {!brand && (
                        <div className="small muted" style={{ marginTop: 4 }}>
                          {f.brand}
                        </div>
                      )}
                    </td>
                    <td data-label="Issue">
                      {hash ? (
                        <Link to={`/findings/${hash}`}>
                          <strong>{humanisePhones(f.issue)}</strong>
                        </Link>
                      ) : (
                        <strong>{f.issue}</strong>
                      )}
                      {f.location && (
                        <div className="small muted" style={{ marginTop: 2 }}>
                          {f.location}
                        </div>
                      )}
                    </td>
                    <td data-label="Where">
                      {f.page_count > 1 && (
                        <div>
                          <strong>on {f.page_count.toLocaleString()} pages</strong>
                          <Explain term="pageCount" />
                          <div className="small muted">one fix, repeated — for example:</div>
                        </div>
                      )}
                      <a
                        className="url"
                        href={f.url}
                        target="_blank"
                        rel="noreferrer"
                        title={f.url}
                        style={{
                          display: "block",
                          maxWidth: 320,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {shortUrl(f.url)}
                      </a>
                    </td>
                    <td className="small muted" style={{ whiteSpace: "nowrap" }} data-label="First seen">
                      {fmtDate(f.first_seen)}
                    </td>
                    <td data-label="Triage">
                      {/* The issue text alone does not identify the row — the same fault appears on
                          many pages — so the accessible name carries the page as well. */}
                      <select
                        value={value}
                        disabled={!hash || save?.kind === "saving"}
                        aria-label={`Triage state for: ${f.issue} — on ${shortUrl(f.url)}`}
                        onChange={(e) => {
                          if (!hash) return;
                          triage.mutate({
                            fingerprint: f.fingerprint,
                            hash,
                            state: e.target.value as TriageState,
                          });
                        }}
                      >
                        {TRIAGE_STATES.map((s) => (
                          <option key={s} value={s}>
                            {TRIAGE_LABEL[s]}
                          </option>
                        ))}
                      </select>
                      {!hash && <div className="small muted">preparing…</div>}
                      {save?.kind === "saving" && <div className="small muted">Saving…</div>}
                      {save?.kind === "saved" && (
                        <div className="small muted">Saved to {TRIAGE_LABEL[save.value]}</div>
                      )}
                      {save?.kind === "failed" && (
                        <div className="small e">
                          Not saved — {save.message}.{" "}
                          <button
                            className="small"
                            onClick={() =>
                              hash &&
                              triage.mutate({
                                fingerprint: f.fingerprint,
                                hash,
                                state: save.value,
                              })
                            }
                          >
                            Retry
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          )}
        </QueryBoundary>
      </div>

      {!findingsQ.isError && total > 0 && (
        <div className="pager">
          <span className="small muted">
            Showing {((page - 1) * perPage + 1).toLocaleString()}–
            {Math.min(page * perPage, total).toLocaleString()} of {total.toLocaleString()}
          </span>
          <select
            value={perPage}
            onChange={(e) => setFilter("per_page", e.target.value)}
            aria-label="Rows per page"
          >
            {PER_PAGE_CHOICES.map((n) => (
              <option key={n} value={n}>
                {n} per page
              </option>
            ))}
          </select>
          <button disabled={page <= 1} onClick={() => setFilter("page", String(page - 1))}>
            Prev
          </button>
          <span className="small muted">
            page {page.toLocaleString()} of {pageCount.toLocaleString()}
          </span>
          <button
            disabled={page >= pageCount}
            onClick={() => setFilter("page", String(page + 1))}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
