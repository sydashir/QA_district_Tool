/**
 * One defect, in full — what it is, what to do about it, everywhere it appears, and its
 * history across runs. The history table is the point: it turns a list of complaints into a
 * defect tracker by showing when a defect appeared and whether it has ever gone away.
 */
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, fmtDate } from "../lib/api";
import type { FindingDetail as FindingDetailData, Severity, TriageState } from "../lib/api";

function sevClass(s: Severity): string {
  return s === "error" ? "e" : s === "warning" ? "w" : "i";
}

/** The run-diff status on a history row, said in plain English. A defect vanishing is NOT
 *  automatically a fix — three of these six mean "we stopped seeing it", not "it got fixed". */
const HISTORY_STATUS: Record<string, string> = {
  new: "First seen in this run",
  persisting: "Still there",
  resolved: "Gone — no longer found on the page",
  rule_changed: "Dropped because we changed the check, not because the site changed",
  page_unsitemapped: "Page left the site index — we stopped auditing it, the defect may still be live",
  page_removed: "The page itself no longer exists",
};

function historyStatusLabel(s: string | null): string {
  if (!s) return "Recorded";
  return HISTORY_STATUS[s] ?? s.replace(/_/g, " ");
}

const TRIAGE_OPTIONS: { value: TriageState; label: string }[] = [
  { value: "open", label: "Open — still needs fixing" },
  { value: "acknowledged", label: "Acknowledged — seen, not fixed yet" },
  { value: "wontfix", label: "Won't fix — intentional, or not a real defect" },
  { value: "fixed", label: "Fixed — next crawl should confirm it" },
];

function asTriageState(v: string): TriageState {
  const found = TRIAGE_OPTIONS.find((o) => o.value === v);
  return found ? found.value : "open";
}

/** Keys already shown elsewhere on this screen — don't repeat them in the details table. */
const SKIP_DETAIL_KEYS = new Set(["class", "sources", "page_count"]);

function DetailValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") return <span className="muted">—</span>;
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  if (typeof value === "object") return <pre className="snippet">{JSON.stringify(value, null, 2)}</pre>;
  return <>{String(value)}</>;
}

const MAX_SOURCES = 20;

export default function FindingDetail() {
  const params = useParams<{ hash: string }>();
  const hash = params.hash ?? "";
  const navigate = useNavigate();
  const qc = useQueryClient();

  const findingQ = useQuery<FindingDetailData>({
    queryKey: ["finding", hash],
    queryFn: () => api.finding(hash),
    enabled: hash !== "",
  });

  const d = findingQ.data;
  const brand = d?.brand ?? "";

  // The brand's run list, so "seen in every run" is a checked fact rather than an assumption —
  // and so a refused run is never mistaken for a run that found nothing.
  const runsQ = useQuery({
    queryKey: ["runs", brand],
    queryFn: () => api.runs({ brand, limit: 200 }),
    enabled: brand !== "",
  });

  // Draft is null while the editor matches the server; that way a successful save just clears it
  // and the fields re-derive from freshly fetched data. No effects, no stale copies.
  const [draft, setDraft] = useState<{ state: TriageState; note: string } | null>(null);
  const savedState: TriageState = d?.triage.state ?? "open";
  const savedNote = d?.triage.note ?? "";
  const stateValue = draft?.state ?? savedState;
  const noteValue = draft?.note ?? savedNote;
  const dirty = draft !== null && (draft.state !== savedState || draft.note !== savedNote);

  const save = useMutation({
    mutationFn: async (body: { state: TriageState; note: string }): Promise<void> => {
      await api.setTriage(hash, { state: body.state, note: body.note === "" ? null : body.note });
    },
    onSuccess: async () => {
      setDraft(null);
      await qc.invalidateQueries({ queryKey: ["finding", hash] });
      await qc.invalidateQueries({ queryKey: ["findings"] });
    },
  });

  if (hash === "") {
    return (
      <div className="wrap">
        <div className="empty">No finding was specified. Open a defect from the findings list to see it here.</div>
      </div>
    );
  }

  if (findingQ.isLoading) {
    return (
      <div className="wrap">
        <div className="empty">Loading this defect…</div>
      </div>
    );
  }

  if (findingQ.isError || !d) {
    const msg = findingQ.error instanceof Error ? findingQ.error.message : "Unknown error";
    return (
      <div className="wrap">
        <h2>Could not load this defect</h2>
        <div className="panel card">
          <div>The dashboard could not fetch this finding, so nothing below is being shown.</div>
          <pre className="snippet" style={{ marginTop: 10 }}>{msg}</pre>
          <div className="controls">
            <button onClick={() => void findingQ.refetch()}>Try again</button>
            <button onClick={() => navigate(-1)}>Go back</button>
          </div>
        </div>
      </div>
    );
  }

  const history = d.history;
  const sources = d.sources ?? [];
  const shownSources = sources.slice(0, MAX_SOURCES);
  const detailEntries = Object.entries(d.details ?? {}).filter(([k]) => !SKIP_DETAIL_KEYS.has(k));

  // --- history summary -------------------------------------------------------------------
  const oldest = history.length > 0 ? history[history.length - 1] : null;
  const runs = runsQ.data ?? [];
  const seenRunIds = new Set(history.map((h) => h.run_id));
  const firstAt = oldest ? new Date(oldest.at).getTime() : 0;
  const since = runs.filter((r) => !isNaN(new Date(r.started_at).getTime())
    && new Date(r.started_at).getTime() >= firstAt);
  const completedSince = since.filter((r) => r.status === "ok");
  const missedRuns = completedSince.filter((r) => !seenRunIds.has(r.id));
  const refusedSince = since.filter((r) => r.status === "refused");
  const partialSince = since.filter((r) => r.partial_sample && r.status === "ok");

  return (
    <div className="wrap">
      <div className="controls">
        <button onClick={() => navigate(-1)}>← Back</button>
      </div>

      <div className="controls" style={{ margin: "4px 0 0" }}>
        <span className={`badge ${sevClass(d.severity)}`}>{d.severity}</span>
        <span className="chip">{d.check_label}</span>
        <span className="chip">{d.brand}</span>
        {d.page_count > 1 && <span className="chip">on {d.page_count.toLocaleString()} pages</span>}
      </div>

      <h2>{d.issue}</h2>

      {d.page_count > 1 && (
        <div className="banner">
          <strong>One fault, {d.page_count.toLocaleString()} pages.</strong>{" "}
          This is the same defect repeated by a shared template or a shared nav/footer element — so it
          is <strong>one fix</strong>, not {d.page_count.toLocaleString()} separate jobs. Fix it once in
          the template and every listed page is corrected.
        </div>
      )}

      <h3>What to do</h3>
      <div className="panel card">
        {d.suggestion
          ? <div>{d.suggestion}</div>
          : <div className="muted">No suggested fix is recorded for this check. Open the page below and
              compare it against the issue described above.</div>}
      </div>

      <h3>Where it is</h3>
      <div className="panel card">
        <div>
          <a href={d.url} target="_blank" rel="noreferrer" className="url">{d.url}</a>
        </div>
        <div className="small muted" style={{ marginTop: 4 }}>Opens the live page in a new tab.</div>

        {d.page_count > 1 && (
          <>
            <div style={{ marginTop: 14 }} className="small">
              {sources.length === 0 ? (
                <span className="muted">
                  The full page list was not stored for this finding — only the example page above.
                </span>
              ) : (
                <>
                  Showing {shownSources.length.toLocaleString()} of {d.page_count.toLocaleString()} affected pages
                  {sources.length < d.page_count && " — the stored list is truncated"}
                  {sources.length > MAX_SOURCES && " — only the first 20 are listed here"}.
                </>
              )}
            </div>
            {shownSources.length > 0 && (
              <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                {shownSources.map((s) => (
                  <li key={s} style={{ marginBottom: 2 }}>
                    <a href={s} target="_blank" rel="noreferrer" className="url">{s}</a>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>

      {d.snippet && (
        <>
          <h3>What the page actually contains</h3>
          <div className="panel card">
            <pre className="snippet">{d.snippet}</pre>
          </div>
        </>
      )}

      <h3>History</h3>
      <div className="panel">
        {history.length === 0 ? (
          <div className="empty">No run history has been recorded for this defect yet.</div>
        ) : (
          <>
            <div className="card" style={{ borderBottom: "1px solid var(--line)" }}>
              {oldest && (
                <div>
                  First seen <strong>{fmtDate(oldest.at)}</strong> (run #{oldest.run_id}), and recorded
                  in <strong>{history.length.toLocaleString()}</strong> run{history.length === 1 ? "" : "s"} since.
                </div>
              )}
              {runsQ.isError ? (
                <div className="small muted" style={{ marginTop: 6 }}>
                  Could not load this brand's run list, so the runs above have not been checked against
                  every run of {d.brand}.
                </div>
              ) : completedSince.length > 0 && (
                <div className="small" style={{ marginTop: 6 }}>
                  {missedRuns.length === 0 ? (
                    <>Seen in <strong>every one</strong> of the {completedSince.length.toLocaleString()} completed
                      runs since {fmtDate(oldest?.at)} — it has never gone away.</>
                  ) : (
                    <>Absent from {missedRuns.length.toLocaleString()} of
                      the {completedSince.length.toLocaleString()} completed runs since {fmtDate(oldest?.at)}.</>
                  )}
                </div>
              )}
              {refusedSince.length > 0 && (
                <div className="small" style={{ marginTop: 6 }}>
                  <strong>{refusedSince.length.toLocaleString()} run{refusedSince.length === 1 ? " was" : "s were"} refused</strong>{" "}
                  in this period — {d.brand} could not be audited at all (the site was unreachable or gave no
                  page index). Those runs found nothing because nothing was checked, so they are no evidence
                  that this defect was fixed.
                </div>
              )}
              {partialSince.length > 0 && (
                <div className="small muted" style={{ marginTop: 6 }}>
                  {partialSince.length.toLocaleString()} of those runs covered only part of the site
                  (partial sample), so absence in those runs may just mean the page was not visited.
                </div>
              )}
            </div>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 90 }}>Run</th>
                  <th style={{ width: 200 }}>When</th>
                  <th>What the run recorded</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.run_id}>
                    <td>#{h.run_id}</td>
                    <td>{fmtDate(h.at)}</td>
                    <td>{historyStatusLabel(h.status)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      <h3>Triage</h3>
      <div className="panel card">
        <div className="controls" style={{ marginTop: 0 }}>
          <label htmlFor="triage-state" className="small muted">Status</label>
          <select
            id="triage-state"
            value={stateValue}
            onChange={(e) => setDraft({ state: asTriageState(e.target.value), note: noteValue })}
          >
            {TRIAGE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <textarea
          value={noteValue}
          rows={4}
          placeholder="Note for the team — who is fixing it, why it is a won't-fix, ticket number…"
          onChange={(e) => setDraft({ state: stateValue, note: e.target.value })}
          style={{ width: "100%", marginTop: 8 }}
        />
        <div className="controls">
          <button
            className="primary"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate({ state: stateValue, note: noteValue })}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
          {dirty && <button onClick={() => setDraft(null)} disabled={save.isPending}>Cancel</button>}
          {save.isSuccess && !dirty && <span className="small muted">Saved.</span>}
          {save.isError && (
            <span className="small e">
              Not saved: {save.error instanceof Error ? save.error.message : "unknown error"}
            </span>
          )}
        </div>
        <div className="small muted">
          Triage is per defect and carries across runs — marking it here keeps it out of the untriaged
          count on the next crawl.
        </div>
      </div>

      <h3>Details</h3>
      <div className="panel">
        {detailEntries.length === 0 ? (
          <div className="empty">This check recorded no extra details beyond what is shown above.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{ width: 200 }}>Field</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {detailEntries.map(([k, v]) => (
                <tr key={k}>
                  <td className="muted">{k.replace(/_/g, " ")}</td>
                  <td><DetailValue value={v} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="card small muted">
          Fingerprint <span className="url">{d.fingerprint}</span>
        </div>
      </div>
    </div>
  );
}
