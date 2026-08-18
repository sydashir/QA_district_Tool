import type { ReactNode } from "react";

/**
 * An empty list, with what the emptiness MEANS attached.
 *
 * "Nothing here" has two completely different causes in this product — the pages were checked and
 * were clean, or they were never checked at all — and they look identical in a table. The client's
 * standing rule is that the second must never read as the first, so the caller always says which.
 *
 * `tone="warning"` is for the second kind: an absence of knowledge, not an absence of problems.
 * Use it for refused runs, brands that have never been audited, and fixed page lists — never for a
 * genuinely clean result, or the colour stops meaning anything.
 */
export interface EmptyStateProps {
  /** One line. What is not here, and why. */
  title: ReactNode;
  /** The consequence or the next step, in smaller text. */
  detail?: ReactNode;
  /** A way out — "Clear filters", "Clear filter". Rendered centred under the text. */
  action?: ReactNode;
  /** "warning" when nothing was checked; "neutral" when nothing was wrong. */
  tone?: "neutral" | "warning";
}

export default function EmptyState({ title, detail, action, tone = "neutral" }: EmptyStateProps) {
  return (
    <div className="empty">
      <strong className={tone === "warning" ? "e" : undefined}>{title}</strong>
      {detail !== undefined ? <div className="small state-detail">{detail}</div> : null}
      {action !== undefined ? <div className="controls">{action}</div> : null}
    </div>
  );
}
