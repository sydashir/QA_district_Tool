#!/usr/bin/env bash
# MHD census progress readout. Run any time:  bash census_status.sh
# Reads the detached run's log + the resume cache. Read-only; never touches the crawl.
cd "$(dirname "$0")" || exit 1
# track whichever MHD run is most recent (full census leg or seeded sample)
LOG=$(ls -t reports/mhd_census.log reports/mhd_sample.log 2>/dev/null | head -1)
LOG=${LOG:-reports/mhd_census.log}
# denominator = what THIS run is auditing (from the log's own progress line), else the full union
TOTAL=$(grep -oE 'fetch pages: [0-9]+/[0-9]+' "$LOG" 2>/dev/null | tail -1 | sed -E 's#.*/([0-9]+)#\1#')
TOTAL=${TOTAL:-15640}
echo "tracking: $LOG"

banked=$(wc -l < cache/mhd/resume.jsonl 2>/dev/null | tr -d ' ')
banked=${banked:-0}
last=$(grep -oE 'fetch pages: [0-9]+/[0-9]+' "$LOG" 2>/dev/null | tail -1)
attempted=$(echo "$last" | sed -E 's#.*: ([0-9]+)/.*#\1#')
attempted=${attempted:-0}

if pgrep -f "auditor.cli audit --brand mhd" >/dev/null 2>&1; then
  state="RUNNING"
else
  state="NOT RUNNING (relaunch: the resume cache keeps the $banked banked pages)"
fi

# progress bar on BANKED pages (what's actually audited and durable)
pct=$(( banked * 100 / TOTAL ))
filled=$(( pct * 40 / 100 ))
bar=$(printf '%*s' "$filled" '' | tr ' ' '#')$(printf '%*s' $((40-filled)) '' | tr ' ' '.')

echo "MHD census — $state"
echo "[$bar] ${pct}%   banked ${banked} / ${TOTAL} pages"
echo "attempted this leg: ${attempted:-0}   (banked < attempted = pages that failed and will be retried on the next leg)"

# rate + ETA from the last two milestones in the log (bash 3.2-safe: no mapfile)
marks=$(grep -oE '\[[0-9:]+\] fetch pages: [0-9]+/' "$LOG" 2>/dev/null | tail -2)
m1=$(echo "$marks" | head -1); m2=$(echo "$marks" | tail -1)
if [ -n "$m1" ] && [ -n "$m2" ] && [ "$m1" != "$m2" ]; then
  t1=$(echo "$m1" | grep -oE '[0-9]{2}:[0-9]{2}:[0-9]{2}')
  t2=$(echo "$m2" | grep -oE '[0-9]{2}:[0-9]{2}:[0-9]{2}')
  n1=$(echo "$m1" | grep -oE '[0-9]+/' | tr -d '/')
  n2=$(echo "$m2" | grep -oE '[0-9]+/' | tr -d '/')
  s1=$(( $(echo "$t1" | cut -d: -f1)*3600 + $(echo "$t1" | cut -d: -f2)*60 + $(echo "$t1" | cut -d: -f3) ))
  s2=$(( $(echo "$t2" | cut -d: -f1)*3600 + $(echo "$t2" | cut -d: -f2)*60 + $(echo "$t2" | cut -d: -f3) ))
  ds=$(( s2 - s1 )); dn=$(( n2 - n1 ))
  [ "$ds" -lt 0 ] && ds=$(( ds + 86400 ))   # crossed midnight
  if [ "$ds" -gt 0 ] && [ "$dn" -gt 0 ]; then
    rem=$(( TOTAL - banked ))
    eta_s=$(( rem * ds / dn ))
    if [ "$eta_s" -ge 3600 ]; then eta="~$(( eta_s / 3600 ))h $(( (eta_s % 3600) / 60 ))m"; else eta="~$(( eta_s / 60 ))m"; fi
    echo "recent pace: ${dn} pages / ${ds}s  -> ETA for the remaining ${rem}: ${eta} at this pace"
  fi
fi
echo
echo "last log lines:"; tail -3 "$LOG" 2>/dev/null
