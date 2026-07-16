"""Sign-up backend. Receives email + clusters, stores subscriber, sends welcome email."""
from __future__ import annotations

import csv
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SUBSCRIBERS_FILE = Path(__file__).parent / "subscribers.csv"
TEMPLATE_FILE    = Path(__file__).parents[1] / "templates" / "email_welcome.html"

SMTP_HOST  = os.environ.get("SMTP_HOST",  "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER  = os.environ.get("SMTP_USER",  "hello@what2watch.co.uk")
SMTP_PASS  = os.environ.get("SMTP_PASS",  "")
FROM_NAME  = os.environ.get("FROM_NAME",  "What 2 Watch")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)


def save_subscriber(email: str, clusters: list[str]) -> None:
    new_file = not SUBSCRIBERS_FILE.exists()
    with open(SUBSCRIBERS_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["email", "clusters", "signed_up_at"])
        w.writerow([email, "|".join(clusters), datetime.now(timezone.utc).isoformat()])


def send_welcome(to_email: str, clusters: list[str]) -> None:
    cluster_label = " · ".join(c.title() for c in clusters) if clusters else "All"

    html = TEMPLATE_FILE.read_text()
    html = html.replace("{{ cluster_label }}", cluster_label)
    html = html.replace("{{ unsubscribe_url }}", "#")
    html = html.replace("{{ change_cluster_url }}", "#")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "You're signed up — What 2 Watch"
    msg["From"]    = f"{FROM_NAME} <{FROM_EMAIL}>"
    msg["To"]      = to_email
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(FROM_EMAIL, to_email, msg.as_string())


@app.route("/subscribe", methods=["POST"])
def subscribe():
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").strip().lower()
    clusters = [c.strip() for c in (data.get("clusters") or []) if c.strip()]

    if not email or "@" not in email:
        return jsonify({"error": "valid email required"}), 400

    save_subscriber(email, clusters)

    try:
        send_welcome(email, clusters)
    except Exception as exc:
        print(f"Welcome email failed for {email}: {exc}", flush=True)

    return jsonify({"ok": True}), 200


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
