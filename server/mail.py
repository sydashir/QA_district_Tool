"""Digest email — content and delivery.

Built now, sending deferred: no sending domain exists yet, so `send()` writes the message to a file
and logs it unless `SMTP_HOST` (or a provider key) is configured. That way the *logic* — who gets
told, when, and what the message says — is finished and testable, and switching it on later is
configuration rather than code.

Editorial rule, and it is the whole design: **an email only goes out when a run found NEW ERRORs or
the run itself failed.** A nightly "here is your report" that arrives whether or not anything
happened trains people to ignore it, which would undo the thing this tool is for.
"""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from auditor.humanize import CHECK_LABELS

OUTBOX = Path(__file__).resolve().parent.parent / "reports" / "_outbox"
APP_URL = os.getenv("APP_URL", "http://localhost:5173")
MAIL_FROM = os.getenv("MAIL_FROM", "auditor@localhost")
MAIL_TO = [a.strip() for a in os.getenv("MAIL_TO", "syed@localhost").split(",") if a.strip()]


def render_digest(brand, run, new_errors: list) -> tuple[str, str]:
    """Plain text, written for a non-developer. Returns (subject, body)."""
    if run.status == "failed":
        subject = f"[{brand.code}] audit FAILED"
        body = (f"The {brand.name} audit did not finish.\n\n"
                f"Reason: {run.error_text or 'unknown'}\n\n"
                f"Nothing was published, so the dashboard still shows the previous results.\n"
                f"{APP_URL}/runs?brand={brand.code}\n")
        return subject, body

    if run.status == "cancelled":
        # Completes the branch set. Without this a cancelled run falls through to the "N new
        # problems found" wording below, which for a run that was stopped part-way would report
        # whatever it happened to have found as though the audit had finished — the precise
        # failure the `cancelled` status exists to prevent.
        subject = f"[{brand.code}] audit stopped before finishing"
        body = (f"The {brand.name} audit was stopped on purpose before it finished, so nothing\n"
                f"went wrong and there is nothing to fix.\n\n"
                f"Reason: {run.error_text or 'a person stopped it'}\n\n"
                f"IMPORTANT: this brand was only partly checked. Anything the run had not reached\n"
                f"was never looked at, and the previous results are unchanged.\n"
                f"{APP_URL}/runs?brand={brand.code}\n")
        return subject, body

    if run.status == "refused":
        subject = f"[{brand.code}] audit could not run"
        body = (f"The {brand.name} audit could not be run and was stopped deliberately.\n\n"
                f"Reason: {run.error_text or 'no pages could be found to audit'}\n\n"
                f"This usually means the website was unreachable or its page index is missing.\n"
                f"IMPORTANT: the previous results are untouched — this brand has NOT been given a\n"
                f"clean bill of health, it simply could not be checked.\n"
                f"{APP_URL}/runs?brand={brand.code}\n")
        return subject, body

    n = len(new_errors)
    subject = f"[{brand.code}] {n} new problem{'s' if n != 1 else ''} found"
    lines = [f"The {brand.name} audit finished and found {n} new problem"
             f"{'s' if n != 1 else ''} that look serious.", ""]
    for f in new_errors[:10]:
        where = f"on {f.page_count} pages" if f.page_count > 1 else f.url
        lines.append(f"  - [{CHECK_LABELS.get(f.check, f.check)}] {f.issue}")
        lines.append(f"      {where}")
    if n > 10:
        lines.append(f"  ... and {n - 10} more")
    lines += ["", f"See them all: {APP_URL}/findings?brand={brand.code}&status=new&severity=error",
              "", f"Pages checked this run: {run.pages_audited:,}"]
    if run.partial_sample:
        lines.append("NOTE: this run was a partial sample, not a complete check of the site.")
    return subject, "\n".join(lines)


def send(subject: str, body: str, to: list[str] | None = None) -> bool:
    """Send, or park in the outbox when no mail transport is configured.

    Returns True only if it actually went somewhere.
    """
    recipients = to or MAIL_TO
    host = os.getenv("SMTP_HOST")
    if not host:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in subject)[:80]
        (OUTBOX / f"{safe}.txt").write_text(
            f"To: {', '.join(recipients)}\nFrom: {MAIL_FROM}\nSubject: {subject}\n\n{body}",
            encoding="utf-8")
        print(f"[mail] no SMTP_HOST configured — wrote {safe}.txt to the outbox instead")
        return False

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, MAIL_FROM, ", ".join(recipients)
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587"))) as s:
        if os.getenv("SMTP_USER"):
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)
    return True
