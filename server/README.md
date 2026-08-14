# server — the product layer

Wraps the finished audit engine. Nothing in `auditor/` was changed to build this.

## Local bring-up

```bash
docker run -d --name district-pg -e POSTGRES_PASSWORD=district -e POSTGRES_USER=district \
  -e POSTGRES_DB=district -p 55432:5432 postgres:16
python3 -m server.importer          # backfills reports/ -> postgres (85 runs, ~247k findings)
python3 -m uvicorn server.api:app --port 8099
```

`DATABASE_URL` overrides the connection; it defaults to the container above.

## What is stubbed, and why

* **Auth** — `api.get_current_user()` returns a fixed local user. One seam; deploy-time it verifies
  the Cloudflare Access JWT (signature + `iss` + `aud`). No endpoint signature changes.
* **Email** — `mail.send()` writes to `reports/_outbox/` unless `SMTP_HOST` is set. The *logic*
  (who gets told, when, what it says) is finished and testable without a sending domain.
* **Deploy** — deferred by decision. See `docs/plans/2026-08-14-product-design.md`.
