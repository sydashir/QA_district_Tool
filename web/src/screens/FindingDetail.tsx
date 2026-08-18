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
import Explain from "../components/Explain";
import SourceList from "../components/SourceList";
import QueryBoundary, { describeFailure, FailureNote } from "../components/QueryBoundary";
import EmptyState from "../components/EmptyState";

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
        <div className="panel">
          <EmptyState
            title="No defect was specified."
            detail="Open a defect from the findings list to see it here."
            action={<button onClick={() => navigate(-1)}>Go back</button>}
          />
        </div>
      </div>
    );
  }

  // Loading, or failed outright. Either way there is no defect to show, and the boundary says
  // which — a 404 here means the defect is gone, not that it was fixed.
  if (d === undefined) {
    return (
      <div className="wrap">
        <div className="panel">
          <QueryBoundary query={findingQ} label="this defect" skeletonRows={4} />
        </div>
        <div className="controls">
          <button onClick={() => navigate(-1)}>Go back</button>
        </div>
      </div>
    );
  }

  const history = d.history;
  const sources = d.sources ?? [];
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
        <span>
          <span className={`badge ${sevClass(d.severity)}`}>{d.severity}</span>
          <Explain term="severity" />
        </span>
        <span className="chip">{d.check_label}</span>
        <span className="chip">{d.brand}</span>
        {d.page_count > 1 && (
          <span>
            <span className="chip">on {d.page_count.toLocaleString()} pages</span>
            <Explain term="pageCount" />
          </span>
        )}
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
          <SourceList
            sources={sources}
            pageCount={d.page_count}
            check={d.check}
            exampleUrl={d.url}
          />
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
          <EmptyState
            tone="warning"
            title="No run history has been recorded for this defect yet."
            detail="It was reported, but no run has been recorded against it — so there is nothing here that shows whether it has ever gone away."
          />
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
                  Could not load this brand's run list, so the runs above have not been checked
                  against every run of {d.brand} — a run that was refused or never covered this page
                  would not be called out here. <FailureNote query={runsQ} label="the run list" />
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
            <div className="tscroll">
              <table className="stack-sm">
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
                      <td data-label="Run">#{h.run_id}</td>
                      <td data-label="When">{fmtDate(h.at)}</td>
                      <td data-label="What the run recorded">{historyStatusLabel(h.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      <h3>
        Triage
        <Explain term="triage" />
      </h3>
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
        {/* A placeholder is not a label: it disappears the moment anyone types, and a screen reader
            reaching this field with a value in it would announce nothing at all. */}
        <label htmlFor="triage-note" className="small muted" style={{ display: "block", marginTop: 8 }}>
          Note for the team
        </label>
        <textarea
          id="triage-note"
          value={noteValue}
          rows={4}
          placeholder="Who is fixing it, why it is a won't-fix, ticket number…"
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
              Not saved — your triage is still only on this screen.{" "}
              {describeFailure(save.error, "the triage change").detail}
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
          <EmptyState title="This check recorded no extra details beyond what is shown above." />
        ) : (
          <div className="tscroll">
            <table className="stack-sm">
              <thead>
                <tr>
                  <th style={{ width: 200 }}>Field</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                {detailEntries.map(([k, v]) => (
                  <tr key={k}>
                    <td className="muted" data-label="Field">{k.replace(/_/g, " ")}</td>
                    <td data-label="Value"><DetailValue value={v} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="card small muted">
          Fingerprint <span className="url">{d.fingerprint}</span>
        </div>
      </div>
    </div>
  );
}
