/**
 * The list of pages one defect appears on.
 *
 * Two honesty problems live here, and both are about the same thing: the list the API hands us
 * is often NOT the whole list, and a list that looks complete would be read as complete.
 *
 *  - A template-wide defect (`auditor/audit.py::_collapse_templates`) keeps only the first 8
 *    pages as examples while reporting the true `page_count`. So "Contact button goes nowhere on
 *    571 pages" arrives with exactly 8 URLs. We say so.
 *  - A broken link (`auditor/checks/links.py::check_links`) keeps at most 5 source pages, and the
 *    page count is then derived from that same capped list — so "on 5 pages" is a FLOOR, not a
 *    count. We say that too, because nobody could infer it.
 *
 * The API itself truncates nothing (`server/api.py::finding_detail` returns `sources` whole), so
 * the biggest real list is large: 3,353 pages on one Gratitude Lodge phone finding. That is why
 * the list reveals in chunks instead of dumping every anchor into the DOM at once.
 */
import { useMemo, useState } from "react";

const FIRST_SHOWN = 25;
const STEP = 100;

/** Per-check caps applied by the auditor when it wrote the report. Keyed by `check`. */
const EXAMPLE_CAPS: Record<string, number> = {
  // A broken link records at most 5 of the pages that link to it, and page_count is counted from
  // that capped list — so the count itself understates reality.
  broken_links: 5,
};

interface Props {
  sources: string[];
  /** The finding's own `page_count` — the number of pages the auditor actually saw the defect on. */
  pageCount: number;
  /** The check name, used to explain WHY a list is short. */
  check: string;
  /** The example page shown above the list, so it can be marked rather than looking duplicated. */
  exampleUrl?: string;
}

export default function SourceList({ sources, pageCount, check, exampleUrl }: Props) {
  const [visible, setVisible] = useState(FIRST_SHOWN);
  const [filter, setFilter] = useState("");
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");

  const total = sources.length;
  const cap = EXAMPLE_CAPS[check];
  const countIsFloor = cap !== undefined && total >= cap;
  const incomplete = total < pageCount;

  const matches = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (needle === "") return sources;
    return sources.filter((s) => s.toLowerCase().includes(needle));
  }, [sources, filter]);

  const shown = matches.slice(0, visible);
  const remaining = matches.length - shown.length;

  async function copyAll() {
    try {
      await navigator.clipboard.writeText(sources.join("\n"));
      setCopied("done");
    } catch {
      setCopied("failed");
    }
  }

  if (total === 0) {
    return (
      <div className="small" style={{ marginTop: 14 }}>
        <strong>{pageCount.toLocaleString()} pages are affected.</strong>{" "}
        <span className="muted">
          The list of which pages was not stored for this defect — only the one example page above.
          Search the findings list for the same issue text to find the others.
        </span>
      </div>
    );
  }

  return (
    <div className="srclist" style={{ marginTop: 14 }}>
      <div className="small">
        <strong>{pageCount.toLocaleString()} pages are affected.</strong>{" "}
        {incomplete ? (
          <>
            The auditor stored only <strong>{total.toLocaleString()}</strong> of them as examples
            when it wrote this report, so <strong>the list below is not the full list</strong> and
            the other {(pageCount - total).toLocaleString()} pages cannot be named here. Fixing the
            shared template corrects all {pageCount.toLocaleString()} regardless.
          </>
        ) : countIsFloor ? (
          <>All {total.toLocaleString()} pages the auditor recorded are listed below.</>
        ) : (
          <>All {total.toLocaleString()} are listed below.</>
        )}
      </div>

      {countIsFloor && (
        <div className="small muted" style={{ marginTop: 6 }}>
          Note: for broken links the auditor records at most {cap} of the pages that link to a bad
          address, and the page count is taken from that same capped list. So{" "}
          {pageCount.toLocaleString()} is a <em>minimum</em> — more pages may link to it.
        </div>
      )}

      <div className="controls" style={{ margin: "10px 0 0" }}>
        {total > 50 && (
          <input
            type="search"
            className="srcfilter"
            value={filter}
            placeholder="Filter these pages by address…"
            aria-label="Filter affected pages"
            onChange={(e) => {
              setFilter(e.target.value);
              setVisible(FIRST_SHOWN);
            }}
          />
        )}
        <button onClick={() => void copyAll()}>Copy all {total.toLocaleString()} addresses</button>
        {copied === "done" && <span className="small muted">Copied.</span>}
        {copied === "failed" && (
          <span className="small muted">
            Could not copy — select the list and copy it by hand.
          </span>
        )}
      </div>

      {filter.trim() !== "" && (
        <div className="small muted" style={{ marginTop: 8 }}>
          {matches.length.toLocaleString()} of {total.toLocaleString()} stored pages contain “
          {filter.trim()}”.
        </div>
      )}

      {matches.length === 0 ? (
        <div className="small muted" style={{ marginTop: 8 }}>
          No stored page address contains that text.
        </div>
      ) : (
        /* Past a screenful the list gets its own scroll box, so expanding 3,353 addresses does not
           push History and Triage 200,000 pixels down the page. */
        <ol className={shown.length > 40 ? "srcitems tall" : "srcitems"} start={1}>
          {shown.map((s) => (
            <li key={s}>
              <a href={s} target="_blank" rel="noreferrer" className="url">
                {s}
              </a>
              {s === exampleUrl && <span className="chip">shown above</span>}
            </li>
          ))}
        </ol>
      )}

      <div className="controls" style={{ marginTop: 8 }}>
        <span className="small muted">
          Showing {shown.length.toLocaleString()} of {matches.length.toLocaleString()}
          {filter.trim() === "" ? " stored pages" : " matching pages"}.
        </span>
        {remaining > 0 && (
          <>
            <button onClick={() => setVisible((v) => v + STEP)}>
              Show {Math.min(STEP, remaining).toLocaleString()} more
            </button>
            <button onClick={() => setVisible(matches.length)}>
              Show all {matches.length.toLocaleString()}
            </button>
          </>
        )}
        {shown.length > FIRST_SHOWN && (
          <button onClick={() => setVisible(FIRST_SHOWN)}>Show fewer</button>
        )}
      </div>
    </div>
  );
}
