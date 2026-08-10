# DBH domain did not resolve during the 2026-08-08 audit run — RESOLVED

**Status: RESOLVED.** The domain resolves and serves normally as of **2026-08-10 16:45 PKT**.
No action is outstanding. This is written up because a parent-brand DNS outage is worth knowing
about even after it clears, and because it may recur or indicate a migration in progress.

**This is not an emergency and does not need anyone contacted out of hours.** An earlier verbal
report of mine described it as live; by the time it was re-checked it had already cleared. The
timeline below is what was actually observed.

---

## What was observed

| When (PKT) | Observation |
|---|---|
| **2026-08-07, ~22:00–23:30** | `https://districtbehavioralhealth.com/` **crawled normally**. Its homepage is where the dead-CTA check found 401 clickable elements and 17 `<a href="#">` buttons that go nowhere, including `Verify Insurance`. So the site was fully up. |
| **2026-08-08, ~01:35** | Audit run reached DBH. Enumeration returned **0 pages in 7 seconds**. Underlying error on every request: `ConnectError: [Errno 8] nodename nor servname provided, or not known` — a **DNS failure**, not a 404, timeout, or Cloudflare block. |
| **2026-08-08, ~01:40** | Verified by hand: **no `A` record** for the apex at **either** `8.8.8.8` (Google) or `1.1.1.1` (Cloudflare). `www` is a `CNAME` to the apex, so it failed too. `curl` could not connect to either (`http=000`). `NS` records were present and answering (`*.ns.porkbun.com`) — the domain was still registered and delegated; the zone simply had no address record. `www.gratitudelodge.com` resolved normally throughout as a control, so it was not a local network or resolver problem. |
| **2026-08-10, 16:45** | **Recovered.** Apex resolves to `34.233.131.184` on both `8.8.8.8` and `1.1.1.1`; `curl` returns **HTTP 200**. `NS` unchanged (Porkbun). |

**Outage window:** began between roughly 23:30 on 08-07 and 01:35 on 08-08; ended at some point
before 16:45 on 08-10. The exact end time is unknown — nothing was polling it in between.

## What cannot be determined from outside

Whether this was a **DNS migration**, an **expired or mis-saved record**, a registrar/hosting
change, or an accident. All that is visible externally is that the delegation stayed intact while
the address record disappeared and later returned. The new address (`34.233.131.184`) is an AWS
range, and it is **not** behind Cloudflare, unlike the other brands (GL resolves to `172.66.40.198`,
a Cloudflare address) — which is consistent with a hosting or DNS change rather than a pure
accident, but is **not proof** of one. Anyone with registrar or hosting access can settle it in a
minute by looking at the zone's change history.

## What the tool did, and why that was right

DBH's audit **failed loudly and changed nothing**:

* enumeration returned nothing, so **no report was written and the run history was left untouched**;
* the publish guard (`EmptyAuditRefused`) **refused to overwrite DBH's sheet tab with an empty
  result**, so the tab still shows its previous findings rather than a misleading zero;
* the other seven brands continued and published normally.

This is the designed behaviour: *a brand that could not be audited must never look like a brand
with no problems.* Had the guard not existed, a DNS blip would have silently reported DBH as clean.

## Follow-up

* **DBH will be re-audited** now that the domain resolves; its tab currently holds pre-outage data.
* If this recurs, the same signature identifies it instantly: audit finishes in seconds with
  "0 pages audited (enumeration returned nothing)" and `dig +short <domain> A @8.8.8.8` is empty.
