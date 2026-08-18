import { useEffect, useState } from "react";
import { fmtDuration } from "../lib/api";

/**
 * A ticking "how long has this been going for" readout.
 *
 * It ticks off a local timer rather than waiting for the next poll because an audit runs for
 * HOURS: a number that only moves every ten seconds reads as a hung job, and the whole point of
 * showing elapsed time is to make a six-hour crawl feel alive instead of stuck.
 *
 * `since` comes from the server as a timezone-aware ISO timestamp (Run.started_at is
 * DateTime(timezone=True)), so Date.parse handles the offset. Elapsed is clamped at zero — a
 * browser clock a few seconds behind the server must not render "-3s".
 */
export default function Elapsed({ since, className }: { since: string; className?: string }) {
  const start = Date.parse(since);
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  if (Number.isNaN(start)) return <span className={className}>—</span>;
  return <span className={className}>{fmtDuration(now - start)}</span>;
}
