# Wrapper design — scheduled runs + sheet output

**Status: BUILT 2026-08-04. Sheet is live. SCOPE CUT — no VM, no scheduling (see §7).**

Sheet: `1QnKHZBnEoxW2gIcOdDz6Ac2WjUbAa94Te_KR-7r2m-E` ("testingQAtool", owned by meetashirr@gmail.com).
Editor access for the service account verified 2026-08-04.

The auditor works and has found every headline defect in the project. What it does not have is a way
to reach Jake without a person running a command and pasting a markdown file. That is the gap.

---

## 0. SCOPE CHANGE 2026-08-04 — the VM was dropped

Syed or the QA team runs this by hand. **Cut: the systemd timer, the `OnFailure` email, and the
dead-man's switch.** Everything below about a VM and a 2-5am window is kept for the record but is
NOT what shipped. What shipped instead is §8.

## 1. Where it runs (NOT BUILT — kept for the record)

**Recommendation: a small dedicated Linux VM (~$6–12/mo, Hetzner/DO/Fly).**

Not Syed's Mac — three session deaths and two sleep interruptions already, and a 6-hour RR crawl
cannot depend on a laptop lid.

| Option | Verdict |
|---|---|
| **Dedicated VM** | **Chosen.** Always on; owns the 2–5am PST window; persistent disk for the resume cache; **and a static IP, which is exactly what CLAUDE.md §9 open question 2 needs for Cloudflare allowlisting.** |
| GitHub Actions | Rejected. 6h hard job limit — RR alone is ~5h crawl + ~1.5h parse. Splitting per brand is possible but there is no static IP to allowlist, and the resume cache would have to round-trip through artifacts every run. |
| Client Cloudways | Rejected. Client infra, and arranging it means going through HWA. |
| Syed's Mac | Rejected, per above. |

The VM also makes the Cloudflare question answerable: one IP to allowlist instead of "whatever
laptop is awake".

## 2. What triggers a run

`systemd` timer (cron is fine too; systemd gives `OnFailure=` for free, which matters in §5).

**Two phases, because the first run per brand is the expensive one and every run after is cheap** —
the content-hash cache means re-runs only re-audit changed pages.

- **Seeding (~1 week, one-off): SMALLEST BRAND FIRST.** TDRC (21) → AH (160) → AR (474) → DBH (574)
  → CAD (1,236) → COC (1,504) → GL (3,574) → RR (7,721) → MHD (15,635).
  **Why small-first and not largest-first:** week one's real risk is not throughput, it is deploy
  bugs — wrong path, missing env var, timer not firing, disk permissions, credentials not readable
  by the service user. Those surface on the FIRST run regardless of size, so find them on a 21-page
  run that fails in a minute, not by burning a whole night on RR. Largest-first optimises for
  finishing sooner; smallest-first optimises for cheap failure, which is what week one is about.
  RR still gets its own dedicated night once the pipeline has proven itself on the small brands.
- **Steady state (nightly):** all nine brands sequentially in the 2–5am window. Only changed pages
  are re-audited, so the run is a fraction of the seed cost.
- **Weekly forced-full: STAGGERED, two brands per night on a rotation.** A forced full re-audit
  ignores the cache, so a silently stale cache can never hide a regression indefinitely — but nine
  of them in one night is 10h+ of crawling and would blow straight through the 2–5am window. Two per
  night means every brand gets a forced-full each week and no single night is enormous:
  Mon TDRC+AH · Tue AR+DBH · Wed CAD+COC · Thu GL · Fri RR · Sat MHD · Sun spare//catch-up.
  RR and MHD get their own nights because either one alone fills the window.

Per-brand crawl politeness stays exactly as configured today (RR concurrency 10, MHD 2 — permanent,
already logged in config).

## 3. What gets written to the sheet

**One spreadsheet. Three kinds of tab.** Not one sheet per brand: nine links is nine things to lose,
and the network rollup showed the findings that matter most (cross-brand dialling, doorway pages) are
only visible when brands sit side by side.

| Tab | Contents | Lifecycle |
|---|---|---|
| **`Summary`** | One row per brand per run: timestamp, pages audited, findings by severity, new / resolved / rule_changed counts, duration, run status, and the data-quality flags (`css_status`, `sitemap_partial`). | **Appended** — this is the run history and the audit trail. |
| **`<BRAND> — new`** | The delta since the last run only: new findings, resolved, rule_changed, page_removed. | **Replaced** each run. This is the "what do I do today" tab. |
| **`<BRAND> — open`** | Every currently-open finding for that brand. | **Replaced** each run. Someone joining later can see the whole standing state without reading history. |

Delta alone loses context for anyone who did not watch the previous run; full-only buries the new
work under thousands of known rows. Both, cheaply.

Volume is not a constraint: GL's full run was 4,666 findings; nine brands at ~5k × ~10 columns is
~450k cells against Sheets' 10M limit.

**Column shape** follows the existing `Finding` model so the sheet and the CSV/JSON never drift:
`url | check | severity | issue | location | snippet | suggestion | fingerprint | first_seen | status`.

## 4. Client library

**Raw Sheets API v4 over `httpx` + `google-auth`. No `gspread`.**

Already proven working this session (auth, read, capability probe). Reasons: `gspread` is not
installed, it is sync-only and not thread-safe, and it adds a dependency for what is three REST
calls. `google-auth` is already present.

## 5. How anyone knows a run died

Nobody watches a cron job. This is the part that is usually skipped and is the reason scheduled jobs
rot silently, so it gets three layers:

1. **Start/finish rows.** The `Summary` tab gets a row at run START with status `running`, updated at
   the end to `ok` or `failed: <reason>`. A run that dies leaves a visible `running` row forever.
2. **Push on failure — EMAIL, plain text.** `systemd OnFailure=` fires a mail on any non-zero exit.
   Chosen over a webhook because a webhook needs somewhere to receive it, which is another service
   to build and host. **The subject line carries the state**, so it is readable from a phone lock
   screen without opening anything: `District auditor: RR run failed` /
   `District auditor: no successful run in 36h`. Body = brand, timestamp, exit code, last 40 log
   lines. Boring, and it works.
3. **Dead-man's switch.** A separate tiny timer checks "was there a successful run in the last 36h?"
   and alerts if not. This is the one that catches the failure mode the other two miss: the job never
   started at all — timer disabled, VM rebooted, disk full. Silence must be loud.

Also written to `Summary` on every run, because a degraded run that reports confident findings is the
trap this project has already hit twice: `css_status` per brand (partial CSS ⇒ findings marked
lower-confidence) and `sitemap_partial` (⇒ coverage findings withheld).

## 5a. Rate-limit budget — measured, not assumed

Official quota (developers.google.com/workspace/sheets/api/limits, checked 2026-08-04):
**60 read and 60 write requests per minute PER USER** per project (300 per project), no daily cap.

Measured call count for a full publish: **10 API calls per brand, 90 for nine brands.**

- **Unpaced, all nine back to back: 90 calls — over the 60/min ceiling.** It would start backing off
  around brand seven and run into the morning. So pacing is load-bearing, not decorative.
- **Paced at 40 calls/min** (1.5s spacing) the publish takes ~2.2 min of wall clock and sits at
  two-thirds of the per-minute ceiling. Since reads and writes have *separate* 60/min buckets and
  our 40 is the combined rate, real usage is ~20 of each — a wide margin.
- Extra headroom is deliberate: **this service account is shared with the GeoData Fetcher.** If that
  runs concurrently it draws from the same per-user bucket, and a nightly job that starts backing
  off does not fail loudly, it just finishes late.

## 5b. Crash safety — per-brand atomic, safe to re-run

A run that dies on brand four must be re-runnable with no duplicated history and no half-updated tab.

- **Every tab write is a full replacement** keyed by tab name, so redoing a brand converges.
- **The one append — the `Summary` history row — is idempotent by `(run_id, brand)`**: a retry
  updates that row rather than adding a second.
- **`Summary` says `running` before any tab is touched and `ok` only after every tab is written**, so
  a crash leaves a visible `running` row instead of silence.
- **Writes go update-then-trim, never clear-then-write.** Clearing first leaves the tab EMPTY if the
  process dies between the two calls; writing first means the worst case is correct new data plus a
  few stale trailing rows, which the next run removes.
- Order within a brand is `open` → `new` → close `Summary`. `open` carries the client's triage and
  refuses to write at all if the existing triage cannot be read.

## 5c. Dry run

`--publish --dry-run` renders exactly what would be written — tab, header, and rows — to stdout or a
file, and touches nothing. Reads still happen, so the preview shows the true triage merge against
whatever the client has actually written. This is what makes the first deploy safe and what can be
shown to the client before anything is live.

## 6. Open — needs Syed, do not guess

**The service account cannot create a spreadsheet. Verified, not assumed:**

- `sheets.spreadsheets.create` → **403**; create-in-folder → **403 "The user's Drive storage quota
  has been exceeded"**; `about.storageQuota.limit` = **0**; **no Shared Drives** available.
- A service account owns no Drive storage, so it can never own a file. The only path is: a human
  creates the sheet, then shares it with edit access.
- It *can* already read and edit 20+ spreadsheets shared with it, so sharing demonstrably works.

**So the two questions are:**
1. Which spreadsheet should the auditor write to? (A human creates it.)
2. Grant edit to `app-service-account@lexical-sol-454719-s2.iam.gserviceaccount.com`.

**Decided 2026-08-03:** notification = plain-text email to Syed; seed order = smallest brand first;
weekly forced-full = staggered two-per-night. Syed provides the spreadsheet and the VM.

**Verified as a side effect:** `NAP_SHEET_ID` (`1AU_wNukif…`) is correct — it reads as
*"NAP Phone numbers / UTM Codes / DBAs"* with a `NAP (Current)` tab, matching the snapshot exactly.
CLAUDE.md §8 marks it UNVERIFIED; that can now be updated.


---

## 7. THE HONEST GAP: nothing runs unless somebody remembers

This is the cost of dropping the VM, written down rather than assumed away.

- **The sheet is only as fresh as the last time a human ran the command.** There is no schedule, no
  timer, no alert.
- **A page that breaks the day after a run is not noticed until the next run.** The tool cannot tell
  anyone about a defect it has not looked for yet.
- **The failure mode is silence, and silence looks exactly like "no problems".** A sheet nobody
  refreshed and a site with nothing wrong are indistinguishable from the sheet. The `Summary` tab's
  `run_started` column is the only thing that distinguishes them, and it relies on someone checking.
- **Interruption is handled; forgetting is not.** Resume covers the laptop closing mid-run. It does
  nothing about a month passing between runs.

**This is Syed's call and a reasonable one for now** — a manual tool that exists beats a scheduled
one that does not. But it should be a decision, not an accident.

**If it becomes a problem, the fix is the timer and it is about a day's work:** a small VM, a
systemd timer on the schedule in §2, `OnFailure=` email, and the dead-man's switch from §5. All of
the per-brand crash-safety, idempotence and pacing that makes unattended running safe is already
built and tested — the only missing pieces are the trigger and the alerting.

## 8. WHAT ACTUALLY SHIPPED

```
python3 -m auditor.cli all              # audit all nine brands, publish each
python3 -m auditor.cli all --dry-run    # print exactly what would be written, touch nothing
python3 -m auditor.cli all -b gl        # one brand
```

- **One command, no flags to remember**, because a QA person runs it — not nine invocations of
  `audit --brand X --publish`.
- **Smallest brand first** (TDRC 21 pages → MHD 15,635) so a broken credential or sheet permission
  surfaces in the first minute, not four hours in.
- **Resume is automatic and the run keeps its identity.** Closing the laptop and running the same
  command again continues the SAME run, so completed brands do not get a duplicate history row on
  every restart. A clean finish clears the marker.
- **One brand failing does not stop the rest**; failures are listed at the end with what to do next,
  and that brand's `Summary` row stays `running` as the visible marker.
- **`--dry-run` still READS**, so the preview shows the true triage merge against what the client
  has actually written.
- **README.md** is written for a non-developer: install, credentials, the one command, what each tab
  means, which two columns are theirs, and what to do when a run dies.

### Bugs the live runs caught that the unit tests could not

1. **A new OAuth token was minted on every single API call** → HTTP 429. Credentials now cached.
2. **No backoff on 429/5xx.** Added, with pacing at 40 calls/min against the measured 60/min quota.
3. **`clear`-then-write left the tab EMPTY** if the process died between the two calls. Now
   write-then-trim, so the worst case is stale trailing rows rather than an empty tab.
4. **The `Summary` tab had no header row**, so the first appended row was read back AS the header —
   which meant `(run_id, brand)` could never be found and **every re-run would have appended a
   duplicate instead of updating**. The original test fake invented a header and hid this; the fake
   was made faithful and now reproduces it.
