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
import { api, fmtDate, fpHash, SEVERITY_ORDER } from "../lib/api";
import type { Finding, Severity, TriageState } from "../lib/api";

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
  const runsQ = useQuery({
    queryKey: ["runs", brand],
    queryFn: () => api.runs({ brand, limit: 1 }),
    enabled: brand !== "",
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

  // ---- run-health banners: a refused run must never read as "audited, found nothing" --------
  const selectedBrand = brandsQ.data?.find((b) => b.code === brand);
  const latestRun = runsQ.data?.[0];
  const refused = latestRun?.status === "refused" || selectedBrand?.last_run_status === "refused";
  const partial = latestRun?.partial_sample === true;
  const refusedBrands = (brandsQ.data ?? []).filter((b) => b.last_run_status === "refused");

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
          Could not load the brand list: {(brandsQ.error as Error).message}. The brand filter is
          unavailable, but the findings below are unaffected.
        </div>
      )}

      {refused && (
        <div className="banner">
          <strong>
            {selectedBrand?.name ?? brand} could not be audited — the last run was refused.
          </strong>{" "}
          The site was unreachable or gave no page index, so no pages were checked. Anything listed
          below is left over from an earlier run; an empty list here does <em>not</em> mean the site
          is clean.
          {latestRun?.error_text ? (
            <div className="small muted" style={{ marginTop: 4 }}>
              Reported reason: {latestRun.error_text}
            </div>
          ) : null}
        </div>
      )}

      {partial && !refused && (
        <div className="banner">
          <strong>Partial sample.</strong> The last run covered{" "}
          {latestRun?.pages_audited.toLocaleString()} pages, not the whole site. Findings below come
          from that sample only — pages outside it have not been checked.
        </div>
      )}

      {!brand && refusedBrands.length > 0 && (
        <div className="banner">
          <strong>
            {refusedBrands.length === 1 ? "1 brand is" : `${refusedBrands.length} brands are`} not
            represented below:
          </strong>{" "}
          {refusedBrands.map((b) => b.name).join(", ")}. Their last run was refused (could not be
          audited), so they contribute no findings here — that is not the same as being clean.
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
            {anyFilter ? " match these filters" : " in total"}
            {findingsQ.isFetching ? " · refreshing…" : ""}
          </>
        )}
      </div>

      <div className="panel" style={{ overflowX: "auto" }}>
        {findingsQ.isError ? (
          <div className="empty">
            <strong>Could not load findings.</strong>
            <div className="small" style={{ marginTop: 6 }}>
              {(findingsQ.error as Error).message}
            </div>
            <div style={{ marginTop: 12 }}>
              <button onClick={() => void findingsQ.refetch()}>Try again</button>
            </div>
          </div>
        ) : findingsQ.isLoading ? (
          <div className="empty">Loading findings…</div>
        ) : rows.length === 0 ? (
          <div className="empty">
            {refused ? (
              <>
                <strong>Nothing to show — this brand was not audited.</strong>
                <div className="small" style={{ marginTop: 6 }}>
                  The last run was refused, so no pages were checked. This is not a clean result.
                </div>
              </>
            ) : (
              <>
                <strong>No findings match these filters.</strong>
                <div className="small" style={{ marginTop: 6 }}>
                  {anyFilter
                    ? "Try a broader search, or clear the filters to see everything."
                    : "Nothing has been reported yet — check that a run has completed for these brands."}
                </div>
                {anyFilter && (
                  <div style={{ marginTop: 12 }}>
                    <button onClick={clearFilters}>Clear filters</button>
                  </div>
                )}
              </>
            )}
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{ width: 80 }}>Severity</th>
                <th style={{ width: 150 }}>Type of issue</th>
                <th>Issue</th>
                <th style={{ width: 340 }}>Where</th>
                <th style={{ width: 130 }}>First seen</th>
                <th style={{ width: 190 }}>Triage</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => {
                const hash = hashes[f.fingerprint];
                const save = saves[f.fingerprint];
                const value = save?.value ?? f.triage_state;
                return (
                  <tr key={f.id}>
                    <td>
                      <span className={`badge ${SEV_CLASS[f.severity]}`}>
                        {SEV_LABEL[f.severity]}
                      </span>
                    </td>
                    <td>
                      <span className="chip">{f.check_label}</span>
                      {!brand && (
                        <div className="small muted" style={{ marginTop: 4 }}>
                          {f.brand}
                        </div>
                      )}
                    </td>
                    <td>
                      {hash ? (
                        <Link to={`/findings/${hash}`}>
                          <strong>{f.issue}</strong>
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
                    <td>
                      {f.page_count > 1 && (
                        <div>
                          <strong>on {f.page_count.toLocaleString()} pages</strong>
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
                    <td className="small muted" style={{ whiteSpace: "nowrap" }}>
                      {fmtDate(f.first_seen)}
                    </td>
                    <td>
                      <select
                        value={value}
                        disabled={!hash || save?.kind === "saving"}
                        aria-label={`Triage state for: ${f.issue}`}
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
