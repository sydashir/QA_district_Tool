# One image, two roles: the API and the worker run the SAME code and differ only in their command.
# Two images would let the engine drift between the process that queues an audit and the process
# that runs it — and a `checks_version` mismatch between them silently invalidates every brand's
# resume cache.
#
# 3.12 is the pinned target (CLAUDE.md §4). Do not drop below 3.11: auditor/config.py reads the
# per-brand config/*.toml with `tomllib`, which is stdlib only from 3.11.
FROM python:3.12-slim

# Unbuffered output is not cosmetic here. A crawl is six hours of streaming progress lines, and
# buffered stdout means `docker logs -f` shows nothing at all until the job ends.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements first so the dependency layer survives every source change.
# Both files are copied at their real repo-relative paths because requirements-server.txt starts
# with `-r ../requirements.txt`, and pip resolves that relative to the file's own directory.
COPY requirements.txt /app/requirements.txt
COPY deploy/requirements-server.txt /app/deploy/requirements-server.txt
RUN pip install --no-cache-dir -r /app/deploy/requirements-server.txt

# Source. Explicit paths rather than `COPY . .` so that adding a new top-level directory is a
# deliberate decision instead of an accident that lands 600 MB of cache in a layer.
COPY auditor/ /app/auditor/
COPY server/ /app/server/
COPY config/ /app/config/
COPY migrations/ /app/migrations/
COPY scripts/ /app/scripts/
COPY deploy/ /app/deploy/
COPY alembic.ini /app/alembic.ini

# Non-root. uid is fixed so the bind-mounted cache/ and reports/ on the host can be chowned to a
# known owner once, in the runbook, rather than guessed at after the first permission error.
#
# data/ is created empty ON PURPOSE: data/nap_snapshot.xlsx is gitignored, so it is not in the
# build context and cannot be baked in. It is bind-mounted. Without it the phone check — the
# flagship check — has no canonical numbers to compare against.
RUN useradd --system --uid 10001 --create-home --home-dir /home/auditor auditor \
    && mkdir -p /app/cache /app/reports /app/data \
    && chown -R auditor:auditor /app

USER auditor

EXPOSE 8099

# No HEALTHCHECK instruction. This image also runs the worker, which has no port and must never be
# probed (see the comment in docker-compose.yml). The API's healthcheck is declared per-service in
# compose, where it applies to the API alone.

# One uvicorn worker: this API is a handful of queries a minute for ~5 users, and a second worker
# process would open a second connection pool and a second Procrastinate pool for no benefit.
CMD ["python3", "-m", "uvicorn", "server.api:app", "--host", "0.0.0.0", "--port", "8099"]
