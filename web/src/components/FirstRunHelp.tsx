/**
 * The four sentences somebody needs before the dashboard means anything.
 *
 * This exists because the alternative is a document, and nobody opening a QA dashboard at 9am reads
 * a document. It is dismissible and the dismissal is remembered, so it costs a regular user exactly
 * one click, ever — and it can be brought back, because "I dismissed the only explanation and now I
 * cannot find it" is a worse outcome than one extra line of text.
 *
 * The scheduling sentence is DERIVED from `brand.scheduled`, never hardcoded. Whether a brand is
 * audited automatically is a per-brand fact that changes (MHD is deliberately unscheduled — its
 * origin cannot survive a full crawl), and a paragraph that flatly promised "audits run every
 * night" would be telling half the fleet's users something untrue about their brand.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import type { Brand } from "../lib/api";

const DISMISS_KEY = "auditor.intro.dismissed";

/** localStorage throws in Safari private browsing and when storage is disabled by policy. The
 *  panel is a nicety; it must never take the dashboard down with it. */
function readDismissed(): boolean {
  try {
    return window.localStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

function writeDismissed(value: boolean): void {
  try {
    if (value) window.localStorage.setItem(DISMISS_KEY, "1");
    else window.localStorage.removeItem(DISMISS_KEY);
  } catch {
    // Not persistable here — the panel still hides for this session.
  }
}

/** Plain-English truth about what runs on its own, straight from the brand list. */
function schedulingSentence(brands: Brand[]): string {
  const unscheduled = brands.filter((b) => !b.scheduled).map((b) => b.code);
  if (brands.length === 0) return "";
  if (unscheduled.length === 0) {
    return "Audits run by themselves overnight, so what you see here is the state of the sites as of the last night that finished.";
  }
  if (unscheduled.length === brands.length) {
    return "Nothing audits these sites automatically — a brand is only ever checked when somebody starts a run from the Runs page.";
  }
  return `Audits run by themselves overnight for most brands, so what you see is the state of the sites as of the last night that finished — except ${unscheduled.join(", ")}, which ${unscheduled.length === 1 ? "is" : "are"} only checked when somebody starts a run by hand from the Runs page.`;
}

export default function FirstRunHelp({ brands }: { brands: Brand[] }) {
  const [dismissed, setDismissed] = useState<boolean>(readDismissed);

  function hide() {
    setDismissed(true);
    writeDismissed(true);
  }

  function show() {
    setDismissed(false);
    writeDismissed(false);
  }

  if (dismissed) {
    return (
      <p className="small muted" style={{ margin: "0 0 12px" }}>
        <button type="button" className="linkish" onClick={show}>
          What is this page?
        </button>
      </p>
    );
  }

  return (
    <section className="panel card intro" aria-labelledby="intro-heading">
      <h3 id="intro-heading" style={{ margin: "0 0 8px" }}>
        New here? Start with this
      </h3>
      <p style={{ margin: "0 0 8px" }}>
        This tool visits every page of the brand websites and reports content problems it can prove
        — links that go nowhere, phone numbers that do not match the brand&rsquo;s real number,
        headings and sections that were never filled in. Each problem it reports is called a{" "}
        <strong>finding</strong>, and each finding is an <strong className="e">error</strong>{" "}
        (broken or wrong, a visitor will hit it), a <strong className="w">warning</strong> (probably
        wrong, worth a look) or <strong className="i">info</strong> (minor). {schedulingSentence(brands)}
      </p>
      <p style={{ margin: "0 0 8px" }}>
        Start on <Link to="/changes">What changed</Link> to see what appeared since the previous
        audit, then work through <Link to="/findings">Findings</Link>, marking each one acknowledged,
        won&rsquo;t fix or fixed as you go so nobody reads the same row twice.
      </p>
      <p className="small muted" style={{ margin: "0 0 10px" }}>
        One thing worth knowing: a brand that could <em>not</em> be audited — the site was
        unreachable, or gave us no list of its pages — is labelled everywhere its numbers appear. A
        low number on a brand like that means &ldquo;we do not know&rdquo;, never &ldquo;nothing
        wrong&rdquo;.
      </p>
      <button type="button" onClick={hide}>
        Got it, hide this
      </button>
    </section>
  );
}
