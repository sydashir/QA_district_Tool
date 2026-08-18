/**
 * `Explain` — the in-product glossary.
 *
 * The team using this screen are QA people, not developers, and nobody reads a separate document.
 * So the vocabulary the product invented — finding, severity, triage, "on N pages" — has to be
 * explainable at the exact spot it is used.
 *
 * Why not a `title` attribute: a native tooltip is invisible to touch, unreachable by keyboard, and
 * on some screen readers never announced at all. This is the disclosure pattern instead — a real
 * `<button>` with `aria-expanded` and `aria-controls`, focus moves into the revealed text so it is
 * actually read out, and Escape closes it and puts focus back on the button.
 *
 * Why the popover is `position: fixed`: the findings table lives inside a panel with
 * `overflow-x: auto`, and per CSS an `overflow` value on one axis forces the other to `auto` too —
 * an absolutely-positioned popover hanging off a `<th>` would be clipped or would add a scrollbar.
 * A fixed-position element is not clipped by an ancestor's overflow (only by a transform/filter
 * ancestor, of which there are none here), so it escapes the table cleanly. The cost is that it
 * does not travel with the page, so a scroll closes it.
 *
 * Every element here is phrasing content (`<span>`, never `<div>`/`<ul>`) because these are dropped
 * inside `<strong>`, `<th>` and `<label>` — a block element in those places is invalid HTML.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

/** Wide enough for a four-line definition, narrow enough to sit beside a table column. */
const POP_WIDTH = 330;
/** Below this much room underneath the button, the popover flips above it. */
const MIN_SPACE_BELOW = 230;
const GAP = 6;
const EDGE = 8;

export type ExplainTerm = "finding" | "severity" | "triage" | "pageCount" | "openFindings";

interface TermCopy {
  /** Used verbatim in the button's accessible name and as the popover heading. */
  label: string;
  body: ReactNode;
}

/**
 * The wording is deliberately plain and deliberately honest — in particular "fixed" does not claim
 * the defect is gone, only that somebody believes it is, because the only thing that proves a fix
 * is the next audit not finding it.
 */
const TERMS: Record<ExplainTerm, TermCopy> = {
  finding: {
    label: "Finding",
    body: (
      <>
        <span className="explain-row">
          A finding is one content problem the auditor found on a page — a link that goes nowhere, a
          phone number that does not match the brand&rsquo;s real number, a heading with nothing in
          it, a section of the page that was never filled in.
        </span>
        <span className="explain-row muted">
          Findings come from a fixed set of checks, not from an opinion about the writing. Each one
          names the page, what is wrong, and what to do about it.
        </span>
      </>
    ),
  },
  severity: {
    label: "Severity",
    body: (
      <>
        <span className="explain-row">
          <span className="badge e">Error</span> — broken or wrong, and a visitor will hit it.
        </span>
        <span className="explain-row">
          <span className="badge w">Warning</span> — probably wrong, worth a look.
        </span>
        <span className="explain-row">
          <span className="badge i">Info</span> — minor, or just something to be aware of.
        </span>
        <span className="explain-row muted">Work errors first.</span>
      </>
    ),
  },
  triage: {
    label: "Triage",
    body: (
      <>
        <span className="explain-row">
          <strong>Open</strong> — nobody has looked at it yet.
        </span>
        <span className="explain-row">
          <strong>Acknowledged</strong> — seen, it is real, it is going to be fixed.
        </span>
        <span className="explain-row">
          <strong>Won&rsquo;t fix</strong> — a deliberate decision to leave it as it is.
        </span>
        <span className="explain-row">
          <strong>Fixed</strong> — believed corrected. If it really is, the next audit will not find
          it and it will drop off the list on its own.
        </span>
        <span className="explain-row muted">
          Triage sticks to the defect and carries across audits, so you never re-read the same row
          twice.
        </span>
      </>
    ),
  },
  pageCount: {
    label: "On N pages",
    body: (
      <>
        <span className="explain-row">
          The same fault repeated across that many pages — it comes from one shared template, or one
          shared header, footer or navigation block.
        </span>
        <span className="explain-row">
          It is <strong>one fix, not one fix per page</strong>. Correct the template once and every
          affected page is corrected with it.
        </span>
      </>
    ),
  },
  openFindings: {
    label: "Open findings",
    body: (
      <>
        <span className="explain-row">
          Open means the problem is still on the site: the last audit either saw it for the first
          time or saw it again.
        </span>
        <span className="explain-row muted">
          Problems that have gone away, that a page no longer exists to have, or that stopped being
          reported because we changed a check, are not counted as open.
        </span>
        <span className="explain-row muted">
          A count of zero for a brand that could not be audited does not mean the brand is clean —
          it means nothing was checked. Those brands are labelled wherever their numbers appear.
        </span>
      </>
    ),
  },
};

export default function Explain({ term }: { term: ExplainTerm }) {
  const { label, body } = TERMS[term];
  const popId = useId();
  const [open, setOpen] = useState(false);
  const [style, setStyle] = useState<CSSProperties>({});

  const wrapRef = useRef<HTMLSpanElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLSpanElement>(null);

  /** Anchor to the button in viewport coordinates, kept inside the window on both axes. */
  const place = useCallback(() => {
    const btn = btnRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const maxLeft = Math.max(EDGE, window.innerWidth - POP_WIDTH - EDGE);
    const left = Math.min(Math.max(EDGE, r.left - EDGE), maxLeft);
    const spaceBelow = window.innerHeight - r.bottom;
    // Flip above only when below is genuinely cramped AND above is roomier, so the popover does not
    // jump sides on every small viewport change.
    setStyle(
      spaceBelow < MIN_SPACE_BELOW && r.top > spaceBelow
        ? { left, bottom: window.innerHeight - r.top + GAP }
        : { left, top: r.bottom + GAP },
    );
  }, []);

  function toggle() {
    if (!open) place();
    setOpen((v) => !v);
  }

  // Focus moves into the popover so a screen reader reads it; Escape sends focus back. Anything
  // that would leave the popover hanging in the wrong place — a scroll, a resize, a click
  // elsewhere — just closes it.
  useEffect(() => {
    if (!open) return;
    popRef.current?.focus();

    const onPointerDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onReflow = () => setOpen(false);

    document.addEventListener("mousedown", onPointerDown);
    // Capture phase: a scroll inside the findings panel does not bubble to window.
    window.addEventListener("scroll", onReflow, true);
    window.addEventListener("resize", onReflow);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("scroll", onReflow, true);
      window.removeEventListener("resize", onReflow);
    };
  }, [open]);

  return (
    <span
      className="explain"
      ref={wrapRef}
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          setOpen(false);
          btnRef.current?.focus();
        }
      }}
      onBlur={(e) => {
        // Tabbing (or clicking) out of the whole control closes it. Moving between the button and
        // the popover keeps relatedTarget inside the wrapper, so that does not count as leaving.
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <button
        type="button"
        ref={btnRef}
        className="explain-btn"
        aria-label={`What does “${label}” mean?`}
        aria-expanded={open}
        aria-controls={popId}
        onClick={toggle}
      >
        {/* The glyph is decoration; the accessible name is on the button. */}
        <span aria-hidden="true">i</span>
      </button>
      <span
        id={popId}
        ref={popRef}
        role="note"
        tabIndex={-1}
        hidden={!open}
        className="explain-pop"
        style={style}
      >
        <span className="explain-title">{label}</span>
        {body}
      </span>
    </span>
  );
}
