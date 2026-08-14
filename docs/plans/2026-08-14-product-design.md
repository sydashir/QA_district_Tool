# District Site Auditor — product design

**Design only. No code until Syed has read this.** Written 2026-08-14.

## The gap this closes

Jake asked for a tool his team uses. What exists is a CLI and a Google Sheet — a working engine
with no product around it. Concretely, today:

* a run happens only when a person remembers to type a command on a laptop;
* findings live in a spreadsheet that cannot be filtered by anything except Sheets' own filters;
* triage is a column somebody types into, preserved by a read-then-rewrite that we had to build
  defensively because the sheet is not a database;
* nobody is told when something breaks — a new ERROR appears silently in a tab.

The engine is not the problem. **The engine is finished and measured** (see `CLAUDE.md` §13). This
document designs the product around it.

**Scope: single-tenant, District only.** Multi-tenancy is explicitly not built now — but the data
model below carries a `brand` dimension already, and adding an `org` column later is a migration,
not a rewrite. That is the only concession made to it.

---

## 1. What we are keeping, and why that shapes everything

The audit engine stays exactly as it is. It already provides the things a product needs:

| What exists | Why it matters here |
|---|---|
| `run_audit(config, …) -> dict` — one async call per brand | The API layer *wraps* this. No engine rewrite. |
| `Finding` is a **pydantic model** (url, check, fingerprint, severity, issue, location, snippet, suggestion, details, first_seen, last_seen, status) | Pydantic is FastAPI's native currency. The API response model is the domain model. |
| **`fingerprint`** — stable identity across runs | This is the product's primary key. Triage, history and "what changed" all hang off it. |
| `diff.py` already computes `new` / `persisting` / `resolved` / `rule_changed` | "What changed since last run" is a **query**, not a feature to build. |
| `triage.py` — statuses `acknowledged` / `wontfix` + a note, keyed by fingerprint | Triage semantics already exist and are already merged across runs. |
| Resume cache, politeness limits, publish guards | Operational safety we do not re-derive. |
| `humanize.CHECK_LABELS` — plain-English names for every check | The UI's filter labels already exist. |

**The single most important consequence:** because `fingerprint` is stable and triage is already
keyed on it, moving from Sheets to Postgres is a *storage* change, not a semantics change. The
sheet becomes an export.

---

## 2. Stack

| Layer | Choice | Why |
|---|---|---|
| API | **FastAPI** (Python 3.12) | Same language and process model as the engine; `Finding` is already pydantic. Anything else means a serialisation layer for no gain. |
| DB | **Postgres 16** | Findings are relational and need filtering/aggregation. ~52k rows today, growing by run — small. JSONB for `details` keeps the checks free to evolve without migrations. |
| ORM/migrations | **SQLAlchemy 2.0 + Alembic** | Boring, and migrations will be needed as checks change. |
| Worker | **A long-running Python process** (see §6) | The crawl is 6–8h. This constraint eliminates most modern hosting. |
| Frontend | **React + Vite + TypeScript**, TanStack Query + Table, Tailwind | The core screen is a filterable 30k-row table; TanStack Table is purpose-built for it. Server-side pagination, so row count never becomes a frontend problem. |
| Auth | see §7 | |
| Email | see §8 | |

**Deliberately NOT used:** no Redis unless the queue choice demands it (§6); no Kubernetes; no
microservices; no serverless for the crawl (it cannot finish inside any function timeout).

---

## 3. Data model

Six tables. Everything else is a query.

```
brands            id, code(RR/GL/…), name, base_url, sitemap_url, enabled,
                  enumeration_mode('sitemap'|'urls_file'), schedule_cron, notes

runs              id, brand_id, started_at, finished_at,
                  status('queued'|'running'|'ok'|'failed'|'refused'),
                  pages_audited, pages_enumerated, checks_version,
                  changed_components jsonb, history_written bool,
                  enumeration_method, partial_sample bool,
                  error_text, log_path

findings          id, brand_id, run_id, fingerprint, url, check, severity,
                  issue, location, snippet, suggestion, details jsonb,
                  status('new'|'persisting'|'resolved'|'rule_changed'|…),
                  first_seen, last_seen, page_count, sources jsonb
                  UNIQUE (run_id, fingerprint)

triage            id, brand_id, fingerprint, state('open'|'acknowledged'|'wontfix'|'fixed'),
                  note, updated_by, updated_at
                  UNIQUE (brand_id, fingerprint)      <-- survives runs, keyed like today

pages             id, brand_id, url, first_seen_run_id, last_seen_run_id, status_code
                  UNIQUE (brand_id, url)              <-- powers new-page detection (§9)

users             id, email, name, role('admin'|'member'), created_at, last_login_at
```

**Design decisions worth defending:**

* **`findings` is per-run, `triage` is per-fingerprint.** A finding row is an observation belonging
  to a run; a triage decision is a human judgement about a *defect*, which must outlive any run.
  Conflating them is how you lose triage on re-run — the exact failure the current sheet code
  defends against by hand.
* **`details` stays JSONB.** Every check puts different things there (`page_count`, `sources`,
  `intended`/`actual`, `matched`). Normalising it would couple the schema to the checks and force a
  migration every time a check gains a field.
* **`page_count`/`sources` are promoted to columns** because the shape-collapse made them
  first-class: a finding that says "on 524 pages" is the unit the team acts on.
* **Retention:** keep all runs; findings rows are ~52k per full cycle. At nightly cadence that is
  ~19M rows/year — still small for Postgres, but add a retention job at month 3 (drop `findings`
  for runs older than 90 days, keep `runs` and `triage` forever). Not needed on day one; noted so
  it is not a surprise.

Indexes that matter: `findings(brand_id, run_id)`, `findings(fingerprint)`,
`findings(brand_id, severity, status)`, `triage(brand_id, fingerprint)`.

---

## 4. API

Thin, REST, JSON. Every list endpoint is paginated and filterable.

```
GET    /api/brands                                  list + last run summary
GET    /api/brands/{code}                           detail + open counts by severity
POST   /api/brands/{code}/runs                      trigger a run  -> 202 + run id
GET    /api/runs?brand=&status=&limit=              run history
GET    /api/runs/{id}                               one run + counters
GET    /api/runs/{id}/log                            tail of the run log (for a live view)
GET    /api/findings?brand=&check=&severity=&state=&q=&page=   the main list
GET    /api/findings/{fingerprint}                  one defect + its full run history
PATCH  /api/triage/{fingerprint}                    {state, note}  -> upsert
GET    /api/changes?brand=&since_run=               new / resolved since a run
GET    /api/pages/new?brand=&since=                 pages first seen since a date
GET    /api/export/sheet?brand=                     push current open set to the Google Sheet
GET    /api/health                                  liveness for the host
```

Two notes:

* **`POST /runs` returns 202 immediately** and enqueues. It never blocks — the job is hours long.
  It must also **refuse to queue a second run for a brand already running** (the engine has no
  cross-process lock today; the queue provides it — see §6).
* **The Google Sheet becomes `/api/export/sheet`**, not the interface. The existing `publish.py`
  and `sheets.py` keep working, triggered on demand or after a run. Keeping it costs almost nothing
  and means nobody is forced off a workflow they already have.

---

## 5. Screens

Five. Nothing more until the team asks.

**A. Dashboard (landing).** One card per brand: open ERROR / WARNING / INFO counts, last run time
and status, untriaged-ERROR count, and a sparkline of open findings over the last 14 runs. A red
badge if the last run failed or was refused. This is the "is anything on fire" screen.

**B. Findings list** — the product's centre of gravity.
* Filters: brand, check (using the plain-English `CHECK_LABELS`), severity, triage state, free-text
  search across url/issue/snippet.
* Columns: severity, page URL, issue, "on N pages" where collapsed, first seen, triage state.
* **Inline triage**: set acknowledged/wontfix/fixed and type a note without leaving the row.
* Default sort is the one already encoded in `triage.sort_key`: untriaged ERRORs first, oldest
  first. The team's triage order is already a solved problem; reuse it.
* Server-side pagination — 30k rows never reach the browser.

**C. Finding detail.** The snippet, the suggestion in plain English, the affected page(s), and
**the history of this fingerprint across runs** — when it appeared, whether it has ever gone away.
That history is what makes it a defect tracker rather than a list.

**D. Run history.** Per brand: every run, duration, pages audited, findings by severity, new vs
resolved, and the failure reason if it failed. A refused run (the `EmptyAuditRefused` guard) shows
as **refused**, with its reason, not as a zero — that distinction is the whole point of the guard.

**E. What changed.** Pick a run (default: latest): new findings, resolved findings, and **new pages
discovered**. This is the screen the QA team should open each morning.

---

## 6. Scheduling and the worker

*Populated from verified research — see the addendum. The shape is fixed regardless of the pick:*

* Nightly, one brand at a time, in the client's requested 2am–5am PST window, staggered so two
  brands never crawl concurrently (politeness + memory).
* **MHD excluded from the nightly schedule** — it is a labelled partial sample by design and its
  origin degrades under load. It runs manually or weekly at most.
* **RR gets its own night.** It is 8,028 pages and ~6h — most of the run on its own.
* The queue must provide: a **per-brand lock** (never two runs of one brand), **retry on
  infrastructure failure but not on `EmptyAuditRefused`** (that is a correct refusal, not an error),
  and a **run record written before the job starts** so a crashed run is visible rather than absent.

---

## 7. Auth

*Recommendation from verified research — see addendum.* Requirements are modest and should stay so:
one organisation, ~5 users, no public signup, no password reset flow we have to own if avoidable.
Roles: `admin` (can trigger runs, change brand config) and `member` (can triage). That is the whole
model.

---

## 8. Notifications

One email, after a run finishes, **only if it found new ERRORs or the run failed**. Silence
otherwise — a digest that arrives every night regardless is a digest nobody reads.

Content: brand, new ERROR count, the top few by severity with their plain-English issue text, and a
deep link into the findings list filtered to `status=new&severity=error`. Recipients configurable
per brand.

---

## 9. New-page detection

**Already computed; just unsurfaced.** `diff.py` classifies pages and the audit already knows the
enumerated set each run. The `pages` table records `first_seen_run_id` per URL, so:

* "N pages published since your last run" on the dashboard;
* a filter on the findings list for *findings on pages first seen in this run* — the highest-value
  view for a marketing site, because a brand-new page with a broken CTA is worse than an old one;
* it also makes DBH's static-list limitation **visible**: if DBH's page list is stale, new-page
  count is permanently zero, which is a symptom somebody will notice.

---

## 10. Build order and honest estimates

Sequenced so something is usable early. Estimates are working days for one engineer, and I have
separated what is genuinely a day from what is genuinely a week.

| # | Slice | Days | Notes |
|---|---|---|---|
| 1 | Postgres schema + Alembic + an **importer for existing report JSONL** | **2** | **Verified on disk: 85 historical runs, 247,595 finding rows, all 9 brands** already exist in `reports/`, and `summary.json` carries every field the `runs` table needs (pages_audited, changed_components, history_written, urls_file, enumeration). The product opens with months of real trend history rather than an empty database — which is what makes the dashboard sparkline and the 'what changed' view useful on day one instead of after a month of runs. |
| 2 | FastAPI skeleton, read-only endpoints (brands, runs, findings, filters) | **3** | The filtering/pagination is most of it. |
| 3 | Worker + queue + per-brand lock; `POST /runs` wired to `run_audit` | **3** | The lock and "run record before start" are the fiddly parts, not the invocation. |
| 4 | Frontend: findings list with filters + inline triage | **5** | The biggest single slice. Table, filters, optimistic triage updates. |
| 5 | Dashboard + run history + finding detail | **3** | |
| 6 | Auth + deploy (host, domain, TLS, backups) | **3** | Deploy is a day; making it repeatable is the rest. |
| 7 | Nightly schedule + failure alerting | **2** | |
| 8 | "What changed" + new-page views | **2** | Mostly queries — the diff already exists. |
| 9 | Email digest | **1** | Genuinely a day, given a provider. |
| 10 | Sheet export kept working + docs/handover | **2** | |
| | **Total** | **~26 working days (~5–6 weeks)** | |

**A usable internal product exists after slices 1–4 (~13 days).** Everything after that is
operations and polish. If time is short, ship 1–4, keep triggering runs by hand, and add scheduling
after — the team gets the dashboard weeks earlier and the crawl still runs.

**What is a day, not a week:** the email digest, the sheet export, the run-history screen.
**What is a week, not a day:** the findings list with filters and triage; and deployment done
properly (backups, restarts, TLS renewal, log retention).

**Risks I would name up front:**
* Deployment/ops is the part most likely to overrun — it always is.
* The crawl's memory profile at RR scale needs verifying on the chosen host before committing.
* Any check change still invalidates the resume cache network-wide (`CLAUDE.md` hard rule). That
  operational rule does not disappear because there is a UI; the UI should *show* checks_version on
  each run so the cause of a mass `rule_changed` is visible rather than mysterious.

---

## 11. What I need from Syed

Nothing below can be assumed, and each blocks a specific slice:

1. **Host** — approval of the recommendation in the addendum, and who owns the account/billing.
2. **Domain** — a hostname for the app (e.g. `audit.<something>`), and who controls DNS.
3. **Google Workspace** — does District (or HWA) have Workspace, and can we restrict login to a
   domain? Decides the auth pick.
4. **Who logs in** — the actual list of people and whether any need admin.
5. **Email** — a sending domain we may verify (SPF/DKIM), and the digest recipient list.
6. **Google service-account credentials** — already held for the sheet; needs to move to the server
   as a secret rather than a local file.
7. **A decision on retention** — how long finding history must be kept.
8. **Confirmation that the sheet stays** as an export (my assumption) or is retired.

---

## 12. The ticket gap: grammar/spelling — a fourth attempt, separately

**This is not part of the product build and must not be sequenced into it.** It is the ticket's
headline and it is still undelivered after three measured failures (D9 AI, D10 dictionary, D11
rule-based — all in `ARCHITECTURE.md`).

The one untried lever is a **biomedical vocabulary** rather than a general-English one. The premise:
D10 failed because rare pharmaceutical words and genuine typos are the same population *by rarity*.
A drug vocabulary separates them by **knowing the drugs**.

*The feasibility of UMLS specifically — licence, contents, whether drug names are even in the
SPECIALIST Lexicon rather than RxNorm, and open alternatives — is answered in the addendum. That
answer decides whether this attempt is worth making at all.*

**Design, gated exactly as D11 was:**
1. Build the drug/clinical vocabulary from a legally usable source; load as an allowlist layer
   above the existing 2,488-term proper-noun list.
2. Re-run the **D10 corpus** (150 GL pages) — ground truth is recoverable: `ARCHITECTURE.md` D10
   names the 7 real words of 78, and 2 of those 7 are already caught by `empty_slot`.
3. **Volume gate first**, then precision, hand-classified, no sampling.
4. **Bar unchanged: 80% precision, proper nouns clean.** If it misses, it is D12 and spelling is
   closed permanently.

Estimate: **3–4 days**, most of it vocabulary acquisition and hand-classification, not code.

---

## Addendum — verified research

Prices read from official pricing pages; the UMLS/openFDA figures re-measured locally by me, not
taken on the researcher's word. Anything unverified is marked as such.

### Hosting — recommendation: one Hetzner Cloud CX33, everything on it

**CX33 (4 vCPU / 8 GB RAM / 80 GB NVMe) — EUR 8.49/mo + EUR 0.50 IPv4 = ~EUR 9/mo net**, running
FastAPI, Postgres and the crawl worker together. Realistically **EUR 11–15/mo** once VAT and offsite
backup storage are included.

The reason is that this workload's defining feature — one long, I/O-bound, memory-hungry process
running ~7h a night — is exactly what managed platforms charge most for and a plain VM charges
nothing extra for. The comparison that settles it: **Render's Postgres alone is USD 19/mo, more
than the entire Hetzner box** that runs the database, the API and the crawler with 8 GB of RAM.

| Option | Realistic monthly | Note |
|---|---|---|
| **Hetzner CX33 (recommended)** | **~EUR 9 net** | no execution model to fight |
| DigitalOcean droplet only / + managed PG | USD 24 / USD 39 | US region if latency matters |
| Render (Background Worker + web + PG) | USD 51 | **Background Workers only** |
| Fly.io / Railway always-on | ~USD 61 | Fly can start/stop the worker per night (~USD 6 compute) but its managed PG is USD 38 |

**Execution limits are what eliminate candidates, before price.** Render **Cron Jobs stop at 12h**
and **Workflows time out at 2h** by default — either would silently kill an RR crawl; only Render
*Background Workers* are safe. Fly's `auto_stop_machines` is **on by default** and must be disabled.
Plain VMs have no such trap.

Two honest caveats: **Hetzner's cheap CX line is EU-only**, so we would crawl US-hosted WordPress
from Germany — for a politeness-limited crawl the added RTT is absorbed by concurrency, but it must
be measured against the current 8,028-page/6h RR baseline before committing, with a DigitalOcean NYC
droplet (USD 24) as the fallback. And **we own backups entirely** — a nightly `pg_dump` to offsite
storage is a Phase-1 task, not a later one. (Hetzner's backup surcharge is commonly cited at 20% but
the docs URL 404'd — **unverified**.)

*Note: Hetzner repriced on 2026-06-15 — CPX22 EUR 7.99→19.49, CCX13 EUR 15.99→42.99. Any older
Hetzner estimate is stale; the CX line is now the only cheap tier.*

### Scheduling — Procrastinate on the same Postgres, supervised by systemd. No Redis.

* **Per-brand lock for free**: `lock='brand:<slug>'` guarantees a second run cannot start before the
  first ends — the hard requirement in §6, without writing a lock table.
* **systemd `OnCalendar` timer at 02:00 with `Persistent=true`**, not Procrastinate's `@app.periodic`
  — because a periodic task only fires if a worker was alive at the time, whereas a persistent timer
  **replays a run missed while the box was down**. That difference matters for a nightly job.
* A sweep every 10 minutes calling `get_stalled_jobs()` + `retry_job()` so a killed worker's job is
  requeued rather than stuck in `doing`.
* **Healthchecks.io Hobbyist (USD 0, 20 checks)** pinged per brand, so a night that silently did not
  happen actually pages someone — the current system's biggest blind spot.
* Marginal infrastructure cost over the box: **USD 0**.

### Auth — Cloudflare Access + Google Workspace. Email — see below.

**Cloudflare Access, free to 50 users (we have ~5), USD 0/mo.** It is both the cheapest and the
*least code*: no password store, no reset flow, no session policy, no MFA decision, no offboarding
path — removing someone from Workspace removes their access. It also matches the client's existing
stack, since all nine brand sites already sit behind Cloudflare.

Two things that are **requirements, not options**: the API must verify the JWT **signature** and
check both `iss` and the per-application `aud` claim (Cloudflare's own docs state header presence
alone permits identity spoofing); and the origin must be reachable **only** through Cloudflare — a
Cloudflare Tunnel is the clean answer and removes the need for any public inbound port on the box
running the crawler. Keep identity behind a one-function seam (`get_current_user(request) -> email`)
so swapping to direct Google OAuth later is a contained change.

---

## 13. The spelling lever — UMLS is REJECTED, and D11's recommendation was wrong

**This overturns what I wrote in `ARCHITECTURE.md` D11.** I recommended the UMLS SPECIALIST Lexicon
as "the one remaining lever". Measurement says do not use it — and I verified the decisive parts
locally rather than accepting the research.

**Licensing was never the problem** (it is free, BSD-style, redistributable). **The content is.**

1. **It misses the drugs that killed D10.** Of the four named terms, `isotonitazene`,
   `solriamfetol` and `pitolisant` are **absent**; only `buprenorphine` is present. It also misses
   every modern brand (Sublocade, Zubsolv, Brixadi, Lucemyra, Spravato, Sunosi).
2. **Decisively, it whitelists real misspellings.** It wrongly accepts **232 of 4,308** known English
   misspellings (**5.39%**) — `accidently`, `occured`, `developement`, `adminstration`, `dependance`
   — because it is a *descriptive lexical resource* that records non-preferred variants
   (`{base=occur}` carries both `variants=regd` and `variants=reg`). **Loading it as an allowlist
   would silently suppress the exact errors the tool exists to find** — the precise opposite of the
   client's stated over-flag bias.
3. **The published PPV 0.90 does not transfer.** That method took the 1,000 most *frequent* corpus
   words as targets and hunted near-neighbours, never reported recall, and had 29 false positives
   total. **Drug names were not a false-positive source because the design excluded rare tokens by
   construction** — not because the lexicon covered them. The comparable naive use of the Lexicon as
   a plain dictionary measured **PPV 47%**.

### The replacement, measured by me today: openFDA NDC Directory

| | |
|---|---|
| licence | **CC0 public domain**, explicit commercial use, no account, no licence |
| size | 28 MB zip, **136,942 records**, refreshed daily (**last_updated 2026-08-14** when I pulled it) |
| distinct drug tokens | **23,687** |
| D10 killer coverage | **7 of 12** — incl. `solriamfetol`, `pitolisant`, `buprenorphine`, `armodafinil`, `estazolam`, `loperamide`, `naloxone` |
| **typo contamination** | **0 of 13** known misspellings present — clean |

**The misses are principled, and they define the residual risk.** openFDA lists *FDA-registered
drugs*, so it does not contain **illicit or novel substances** (`isotonitazene`, `mephedrone`,
`pentedrone`) or **clinical conditions** (`xerostomia`, `methemoglobinemia`). Both categories appear
in rehab marketing copy, so a spellcheck built on openFDA alone would still flag them. Any D12 must
either add a second source for those or accept them as known false positives — and that residue is
precisely what sank D10, so **the honest prior is that D12 is more likely to fail than succeed.**

**Recommendation: openFDA NDC (+ RxNorm Prescribable, also licence-free) as the vocabulary, not
UMLS.** Same gate as before — volume, then precision, hand-classified, 80% bar, proper nouns clean.
And it stays outside the product build.
