# District Site Auditor

Checks the nine District Behavioral Health websites for problems a machine can verify without
guessing: broken links, wrong or dead phone numbers, missing headings, blank sections, unresolved
template placeholders, duplicate titles. It writes what it finds into a Google Sheet.

You do not need to be a developer to run it. It is one command.

---

## The one command

```
python3 -m auditor.cli all
```

That audits all nine brands, smallest first, and publishes each one to the sheet as it goes.
It prints progress as it works and a summary at the end.

### How long it takes — read this before you start

**Measured on a real full run, 2026-08-04:**

| Brands | Pages | Time |
|---|---|---|
| The eight brands **excluding MHD** | ~15,400 | **about 7 hours** |
| **MHD on its own** | 15,635 | **about 29 hours** |

MHD is deliberately slow. Its web host rate-limits us, so it is locked to a gentle setting that we
are not going to raise — pushing it risks the client's live site.

**So `all` is NOT an overnight job as configured.** Do this instead:

```
python3 -m auditor.cli all -b tdrc -b ah -b ar -b dbh -b cad -b coc -b gl -b rr
```

That is the eight brands, about 7 hours — start it in the morning and it is done by evening, or
start it before you leave and it is done overnight.

Then run MHD separately, when a machine can be left alone for a day or more:

```
python3 -m auditor.cli all -b mhd
```

You can stop MHD and restart it as often as you like; it resumes. If leaving a machine running for a
day is not practical, tell Syed — the answer is to check a sample of MHD regularly and do the full
count rarely, but that is a decision to make deliberately, not a workaround to improvise.

### Before you trust it, do a practice run

```
python3 -m auditor.cli all --dry-run
```

This does everything **except** write to the sheet. It prints exactly what it *would* write. Nothing
is changed. Use this the first time, and any time you want to see what is about to happen.

---

## If you need to stop it

Press `Ctrl+C`, or just close the laptop. Nothing breaks.

To carry on, **run the same command again.** It remembers the pages it already checked and skips
them, so it picks up roughly where it stopped rather than starting over. It also keeps the same run
identity, so the sheet does not fill up with duplicate rows every time you stop and start.

---

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
