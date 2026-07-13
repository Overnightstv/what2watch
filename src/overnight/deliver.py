"""Email delivery — renders the daily edition and sends via SMTP.

For MVP: sends straight to the configured SEND_TO_EMAIL address.
If copy generation was blocked by lint, sends a review alert instead.
No figures ever appear in outbound email — the lint gate ensures this.
"""
from __future__ import annotations

import os
import smtplib
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

from overnight.models import AlertItem, Edition

load_dotenv(Path(__file__).parents[2] / ".env")

TEMPLATES_DIR = Path(__file__).parents[2] / "templates"
SEND_TO       = os.getenv("SEND_TO_EMAIL", "")
SMTP_USER     = os.getenv("SMTP_USER", "support@overnights.tv")
SMTP_PASSWORD = os.getenv("SMTP_APP_PASSWORD", "")

CHIP_LABELS = {
    "Banker":  "Tonight's pick",
    "Rising":  "On the rise",
    "Verdict": "Binge verdict",
    "Live":    "Live event",
    "Gem":     "Hidden gem",
    "Skip":    "Skip it",
    "Finale":  "Series finale",
}


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )


def _chip_label(chip: str) -> str:
    return CHIP_LABELS.get(chip, chip)


def _tx_time(item: AlertItem) -> str:
    if item.tx is None:
        return "On demand"
    return item.tx.strftime("%-I:%Mpm").lstrip("0") if item.tx else "Tonight"


def _availability(item: AlertItem) -> str:
    avail = [a for a in item.availability if a]
    return " · ".join(avail) if avail else "Check listings"


def render_html(edition: Edition, copy: dict, tx_date: date) -> str:
    """Render the HTML email from the Jinja2 template."""
    env = _jinja_env()
    tmpl = env.get_template("email_daily.html")

    # Zip copy items with alert items for image/tx data
    alert_map = {a.series_id: a for a in edition.items}
    rendered_items = []
    for ci in copy.get("items", []):
        sid   = ci.get("series_id", "")
        alert = alert_map.get(sid)
        rendered_items.append({
            "headline":     ci.get("headline", ""),
            "body":         ci.get("body", ""),
            "gem_line":     ci.get("gem_line", ""),
            "chip":         _chip_label(ci.get("chip", "")),
            "title":        alert.title if alert else sid,
            "channel":      alert.channel if alert else "",
            "tx_time":      _tx_time(alert) if alert else "",
            "availability": _availability(alert) if alert else "",
            "image_url":    (alert.image_ref or "") if alert else "",
        })

    date_str    = tx_date.strftime("%-d %B %Y")
    ticker_line = f"What 2 Watch · {date_str}"

    return tmpl.render(
        subject_line  = copy.get("subject_line", "Your daily TV picks"),
        edition_date  = date_str,
        ticker_line   = ticker_line,
        items         = rendered_items,
        unsubscribe_url = "#",
    )


def _send(to: str, subject: str, html: str, plain: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"What 2 Watch <{SMTP_USER}>"
    msg["To"]      = to

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(SMTP_USER, SMTP_PASSWORD)
        s.send_message(msg)


def send_edition(edition: Edition, copy: dict, tx_date: date) -> None:
    """Render and send the daily edition email."""
    if not SEND_TO or not SMTP_PASSWORD:
        print("  [deliver] SEND_TO_EMAIL or SMTP_APP_PASSWORD not set — skipping send")
        return

    subject = f"What 2 Watch · {copy.get('subject_line', 'Your picks for tonight')}"
    html    = render_html(edition, copy, tx_date)
    plain   = copy.get("whatsapp_compact", "See HTML version.")

    _send(SEND_TO, subject, html, plain)
    print(f"  [deliver] Edition sent to {SEND_TO}")


def send_lint_alert(edition: Edition, lint_issues: list[str], tx_date: date) -> None:
    """Notify the operator that copy generation was blocked by lint."""
    if not SEND_TO or not SMTP_PASSWORD:
        print("  [deliver] Lint blocked — but no email configured to notify")
        return

    date_str = tx_date.strftime("%-d %B %Y")
    subject  = f"[What 2 Watch] ⚠ Edition blocked — review needed ({date_str})"
    items_txt = "\n".join(
        f"  • {a.alert_type.value}: {a.title}" for a in edition.items
    )
    issues_txt = "\n".join(f"  • {i}" for i in lint_issues)
    plain = (
        f"The {date_str} edition was blocked by the compliance lint "
        f"and needs manual review.\n\n"
        f"Selected items:\n{items_txt}\n\n"
        f"Lint issues:\n{issues_txt}\n\n"
        f"Re-run pipeline or edit copy manually to resolve."
    )

    _send(SEND_TO, subject, plain, plain)
    print(f"  [deliver] Lint alert sent to {SEND_TO} ({len(lint_issues)} issues)")
