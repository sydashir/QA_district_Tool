# Review screenshots — 8 September 2026

From the full nine-brand run (runs 131–140, 15,341 pages). Ten shots: five of the client report
Syed sends out, five of the internal web app. Palette-quantized, 2.3 MB total.

## The client report — `reports/_client/gl-audit.html` (Gratitude Lodge)

| # | file | what it shows |
|---|---|---|
| 1 | `01-report-top.png` | Title, audit date, and the count strip: **3,353 pages checked · 9,087 open findings · 758 certain problems**. Then the ordering rule — "by how much each problem matters, not by how many there are" — and the first section. |
| 2 | `02-headline-3353-pages.png` | **"retired phone number still present — on 3,353 pages."** Every page on the site carries a dead number. One template fault, one fix. This is the case a person reading pages by hand would never assemble. |
| 3 | `03-picture-dial-mismatch.png` | **The one that matters.** "displayed number differs from the click-to-call target — on 62 pages", snippet `shows '949-676-9364' but dials +18445760144`, and **a photograph of that element with the number outlined in red**. This is the defect class that opened the ticket, caught on GL, with visual proof. |
| 4 | `04-no-picture-explained.png` | A finding with **no** photograph, and the sentence saying why: *"this element is a mobile/desktop duplicate that is not visible at the width we photograph… The defect is still present in the page's code."* About half of these elements cannot be photographed; the report never lets that read as "no evidence". |
| 5 | `05-what-it-cannot-see.png` | The closing section — what the audit does **not** cover. Stated to the client rather than left implied. |

## The web app — http://localhost:5173

| # | file | what it shows |
|---|---|---|
| 6 | `06-dashboard-nine-brands.png` | All nine brands, open counts by severity, per-brand trend sparkline. MHD carries its "could not be checked" notice; DBH its "part of the site only". |
| 7 | `07-findings-list.png` | The triage queue: one row per **defect**, not per page — "on 3,344 pages" means one fix. Filters by brand, type, severity, triage state. Clipped to the first screenful of 4,370px. |
| 8 | `08-finding-detail.png` | One finding in full: what to do, every affected address, exact page text, run-by-run history ("still there" in all 8 runs since 8 Aug), triage box, raw fields. **No photograph — see the note below.** |
| 9 | `09-what-changed.png` | COC's diff: 168 new, 78 fixed. Clipped from 15,243px. |
| 10 | `10-runs.png` | Run history with each run's status. Clipped from 10,529px — a full-page shot is illegible. |

## One thing shot 8 does not show, and why

**The web app never displays the element photographs.** Verified twice: nothing in `web/src`
references `shot`, and `/api/findings/{hash}` does not return `shot` or `shot_absent` in its details
payload. The pictures exist only in the HTML client reports (shots 1–5).

That is a gap, not a defect — the report is the client-facing artefact and it has them. Closing it
would mean the API exposing the field and the detail screen rendering it. Not built.
