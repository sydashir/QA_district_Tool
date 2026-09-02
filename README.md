# District Site Auditor

Checks the nine District Behavioral Health websites for problems a machine can verify without
guessing: broken links, wrong or dead phone numbers, missing headings, blank sections, unresolved
template placeholders, duplicate titles. It writes what it finds into a Google Sheet.

You do not need to be a developer to run it. It is one command.

---

## Before anything else: STOP THE MACHINE SLEEPING

**This has now cost days of wall-clock time TWICE.** If the machine sleeps mid-run the audit does
not fail and does not warn you — it freezes and silently resumes whenever the machine wakes.

* On one run it turned an 8-hour job into a 22-hour one.
* On the 2026-08-08 nine-brand run it turned **~7h of actual crawling into 69h36m of wall clock** —
  the machine slept for ~62 hours in the middle of Gratitude Lodge. Nothing in the log says so; the
  only clue is a gap between timestamps.

So always start a real run with `caffeinate -s`, on mains power, lid open:

```
caffeinate -s python3 -m auditor.cli all -b tdrc -b ah -b ar -b dbh -b cad -b coc -b gl -b rr
```

(`caffeinate -s` keeps the machine awake while plugged in. `caffeinate -i` alone does **not** stop
lid-close sleep.) If a run reports a wildly longer time than the table below, suspect sleep first
and check the log for a timestamp gap.

## The command to run

```
python3 -m auditor.cli all -b tdrc -b ah -b ar -b dbh -b cad -b coc -b gl -b rr
```

That audits eight of the nine brands, smallest first, and publishes each to the sheet as it goes.
It prints progress as it works and a summary at the end. **It takes about 7 hours** — start it
before you leave and it is done by morning.

The ninth brand, **MHD, cannot be audited in full at all** — see the MHD note below. It is
run separately, and what gets published is an explicitly labelled **sample**:

```
python3 -m auditor.cli all -b mhd -n 400
```

There is also `python3 -m auditor.cli all` with no `-b` flags, which does all nine in one go — but
that is a multi-day job, so it is not what you normally want. See below.

### How long it takes — read this before you start

**Measured on a real full run, 2026-08-04, ON AN OTHERWISE IDLE MACHINE:**

| Brands | Pages | Time |
|---|---|---|
| The eight brands **excluding MHD** | 15,525 | **8h20m** |
| of which **RR alone** | 7,962 | **5h44m — 69% of the total** |
| **MHD** | 15,635 | **~131h for a full census — not attempted; audited as a sample** |

RR is most of the run. If you only have an evening, RR is the one to leave for its own night.

### THESE TIMES ASSUME THE MACHINE IS NOT BUSY, AND THAT IS THE BIGGEST VARIABLE

Throughput is dominated by host load, not by the tool or the sites. **Measured 2026-09-01/02 on the
same machine while Docker was saturating it:**

| brand | pages | minutes | pages/min | vs its own historical median |
|---|---|---|---|---|
| TDRC | 19 | 2 | 9.4 | 12.3 |
| AH | 179 | 14 | 12.8 | 33.4 |
| AR | 474 | 109 | 4.4 | 19.6 |
| DBH | 574 | 97 | 5.9 | 26.5 |
| CAD | 1,239 | 198 | 6.3 | 35.5 |
| COC | 1,458 | **873** | 1.7 | 16.8 |

Six brands, 3,943 pages, **21.6 hours** — work the 2026-08-04 baseline did at roughly ten times the
rate. **Every brand's slowest run on record is one of these**, which is what tells you it is the
host and not any individual site.

The machine at the time: **12 cores, load average 167**, `com.docker.backend` pinned at **1000% CPU**
(ten of twelve cores), 595k pageouts, disk 95% full. The auditor's own worker was using one core.
The same condition was recorded on 2026-08-21 at load 947 — it recurs.

**So check before you plan, and do not trust either number blindly:**

```bash
uptime                                   # load should be well under the core count
docker stats --no-stream                 # com.docker.backend must not be pinned
df -h .                                  # a full disk makes everything worse
```

A run on a quiet machine tracks the 2026-08-04 column. A run on a machine like the one above takes
five to ten times longer and there is nothing wrong with the tool. **Do not "fix" the documented
times by replacing them with the slow ones** — that would record a host problem as a property of
the auditor, and the next person would plan a week for an evening's work.

COC's 873-minute run has a second cause layered on top; see the throttling note below.

**MHD is a special case, and the reason is CONCURRENCY, not speed.**

Its host collapses under parallel requests. Measured 2026-07-30 across concurrency levels:
2 parallel connections give ~90% of pages and the best rate we can get; 3 starts throttling; **4
pushed the origin into HTTP 500s that outlasted the run** — the next audit could not even read the
sitemap. So 2 is a permanent locked ceiling (`config/mhd.toml`), and the resulting **~2 pages per
minute is the CONSEQUENCE of that ceiling, not a separate throughput problem.** You cannot buy
speed here by waiting or retrying; the only lever is concurrency, and it is already at its safe
maximum.

At that rate a full **15,635-page** census is **~131 hours (5.5 days)**. That is why a complete MHD
audit is not attempted: what we publish is a **sample**, and the `Summary` tab labels it
`PARTIAL SAMPLE` so nobody mistakes it for a full check.

> An earlier version of this file said MHD "takes about 29 hours". **That figure was never
> supported by any measurement** and has been removed — do not reinstate it. The measured numbers
> are the ones above.

### COC throttles too, and it is not the same thing as MHD

COC's 2026-09-02 run took **873 minutes for 1,458 pages**, and the hourly page counts recorded by the
crawl show what happened:

```
02:00  275 pages   03:00  380   04:00  350   05:00  201     <- healthy, ~5/min
06:00    (none)
07:00    2   08:00   2   09:00  10   10:00  13   11:00   8   <- nine hours at 2-13 pages/HOUR
12:00   11   13:00  13   14:00   7   15:00  10
16:00   50 and climbing                                     <- released, back to ~8-10/min
```

That is not the machine (the machine was equally busy at 03:00, when it managed 380 pages/hour) and
it is not a stall — a stall gives zero, not two. It is the origin holding the crawler at a trickle
and then releasing it.

**It differs from MHD in the way that matters.** MHD collapses into 500s under parallel requests, so
its ceiling is CONCURRENCY and is locked at 2. COC's pages all returned **200** — slowly. Nothing
failed, no findings were lost, and `crawl_verdict` correctly did not refuse the run. The cost is
wall clock only.

**No concurrency change is proposed for COC on this evidence.** One observation is not a
characterisation, and lowering concurrency on a host that answers every request successfully would
trade certain slowness for a guess. Watch it on the next run; if the trickle recurs, measure it
against concurrency the way MHD's ceiling was measured before changing anything.

**If enumeration comes back `blocked` with 0 URLs, stop.** That state is itself evidence the origin
is degraded, and the tool correctly refuses to publish anything from it. Leave MHD alone and try
another day — retrying into a degraded client origin makes it worse. A refusal here is the guard
working, not a failure.

Run it like this, and expect a sample rather than everything:

```
python3 -m auditor.cli all -b mhd -n 400
```

**So `all` is NOT an overnight job as configured.** Do this instead:

```
python3 -m auditor.cli all -b tdrc -b ah -b ar -b dbh -b cad -b coc -b gl -b rr
```

That is the eight brands, about 7 hours — start it in the morning and it is done by evening, or
start it before you leave and it is done overnight.

Then run MHD separately as a sample (see the note on MHD below):

```
python3 -m auditor.cli all -b mhd -n 400
```

You can stop MHD and restart it as often as you like; it resumes.

### Before you trust it, do a practice run

```
python3 -m auditor.cli all --dry-run
```

This does everything **except** write to the sheet. It prints exactly what it *would* write. Nothing
is changed. Use this the first time, and any time you want to see what is about to happen.

---

## Do not close the laptop lid

See **"Before anything else: stop the machine sleeping"** at the top of this file. It is the single
most common way to lose a day on this tool, and it has happened twice.

## If you need to stop it

Press `Ctrl+C`, or close the laptop. Nothing breaks.

To carry on, **run the same command again.** It remembers the pages it already checked and skips
them, so it picks up roughly where it stopped rather than starting over. It also keeps the same run
identity, so the sheet does not fill up with duplicate rows every time you stop and start.

---

## DBH is enumerated from a static page list

District Behavioral Health was **rebuilt on a different platform on 2026-08-08** — it is now
headless WordPress behind Next.js. It serves **no sitemap, no `robots.txt` and no WP-REST**, so
there is nothing for the crawler to read to find out what pages exist. Its content is still fully
auditable (the pages are server-rendered and still Elementor-shaped), so the brand is enumerated
from a fixed list instead: `config/urls/dbh.txt`, wired via `urls_file` in `config/dbh.toml`.

**This has a permanent limitation you must know about: a fixed list cannot discover new pages.**
Anything DBH publishes after that list was written is invisible to the audit and will never be
checked — silently. It is not a temporary workaround; on this platform there is no index to read.

So whenever DBH adds pages, the list has to be regenerated by hand, and any report covering DBH
should say it was audited from a fixed list. **A page the audit cannot see must never be mistaken
for a page it checked and found clean.**

## If a brand fails

One brand failing does not stop the others — the run continues and tells you at the end which ones
did not finish.

**What to do: run the same command again.** The brands that already worked are cached and go
quickly; the failed one is retried.

In the sheet, an unfinished brand's row in `Summary` still says **`running`**. That is the marker to
look for. A row that says `ok` finished properly.

If the same brand fails twice with the same error, send that error text to Syed.

---

## What the sheet tabs mean

| Tab | What it is |
|---|---|
| **`Summary`** | One row per brand per run — when it ran, how many pages, how many problems by severity, how many are new or fixed. **New rows are added, never replaced**, so this is the history. Check the `status` column: `ok` means it finished, `running` means it did not. |
| **`<BRAND> — new`** | Only what changed since the last run. This is the "what do I look at today" tab. |
| **`<BRAND> — open`** | Every outstanding problem for that brand. Replaced each run. |

### The two columns you fill in

On every `— open` tab, the last two columns are **yours**:

- **`status`** — leave blank if you have not looked at it yet. Put `acknowledged` if you have seen it
  and are not fixing it yet. Put `wontfix` if it is deliberate and should stay as it is.
- **`note`** — anything you want to record. A ticket number, a reason, a name.

**Your edits are kept.** Each run reads what you wrote before it rewrites the tab. If the tool cannot
read your notes for any reason, it refuses to write that tab at all rather than risk overwriting
them — so a run may skip a tab, but it will never wipe your work.

Rows are sorted so the **untriaged errors sit at the top, oldest first**. Things you marked `wontfix`
sink to the bottom.

If a `note` says *"triage reset: rule changed…"*, that means the check itself was updated, so the
earlier decision was about a slightly different question and is worth a fresh look. Nothing was lost
— the old value is quoted inside the note.

### Severity

- **ERROR** — broken for a visitor or wrong on the page. Worth fixing.
- **WARNING** — probably wrong, worth a look.
- **INFO** — noted, not necessarily a problem.

---

## Setup (once)

You need Python 3.12 or newer.

```
pip install -r requirements.txt
```

Credentials: the tool reads a Google service-account key from

```
~/Documents/workk/district/credentials/service-account.json
```

The output spreadsheet must be shared as **Editor** with:

```
app-service-account@lexical-sol-454719-s2.iam.gserviceaccount.com
```

That sharing is already done for the current sheet. A service account cannot create a spreadsheet of
its own, so if you ever want a different one, a person has to create it and share it.

### Check the setup works

```
python3 -m auditor.cli all --dry-run -b tdrc -n 5
```

That audits five pages of the smallest brand and prints what it would write. It takes about a
minute. If that works, everything works.

---

## Running one brand only

```
python3 -m auditor.cli all -b gl
```

Brand codes: `tdrc`, `ah`, `ar`, `dbh`, `cad`, `coc`, `gl`, `rr`, `mhd`.

---

## Things worth knowing

- **Nothing runs on its own.** There is no schedule. The sheet only updates when somebody runs the
  command. If a page breaks the day after a run, it will not be noticed until the next one.
- **It is polite to the websites.** It limits how fast it requests pages, per brand. Do not try to
  speed it up.
- **It only reads the websites.** It never changes them.
- **A brand marked `partial` in the `css_status` column** means the tool could not read all of that
  site's stylesheets, so some findings on it are less certain. It says so rather than pretending.
