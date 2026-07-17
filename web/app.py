"""Sign-up backend. Receives email + clusters, stores subscriber, sends welcome email."""
from __future__ import annotations

import csv
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import stripe
from flask import Flask, jsonify, redirect, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SUBSCRIBERS_FILE = Path(os.environ.get("SUBSCRIBERS_FILE", "/tmp/subscribers.csv"))
TEMPLATE_FILE    = Path(__file__).parents[1] / "templates" / "email_welcome.html"

SMTP_HOST  = os.environ.get("SMTP_HOST",  "smtp.gmail.com")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER  = os.environ.get("SMTP_USER",  "hello@what2watch.co.uk")
SMTP_PASS  = os.environ.get("SMTP_PASS",  "")
FROM_NAME  = os.environ.get("FROM_NAME",  "What 2 Watch")
FROM_EMAIL = os.environ.get("FROM_EMAIL", SMTP_USER)

stripe.api_key             = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID            = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET      = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_SUCCESS_URL         = os.environ.get("STRIPE_SUCCESS_URL", "http://localhost:5001/signup-success")
STRIPE_CANCEL_URL          = os.environ.get("STRIPE_CANCEL_URL",  "http://localhost:5001/signup-cancel")


# ── subscriber storage ────────────────────────────────────────────────────────

def save_subscriber(email: str, clusters: list[str], whatsapp: bool, whatsapp_number: str = "") -> bool:
    """Upsert subscriber. Returns True if new, False if updated."""
    header = ["email", "clusters", "whatsapp_upgrade", "whatsapp_number", "signed_up_at"]

    if not SUBSCRIBERS_FILE.exists():
        with open(SUBSCRIBERS_FILE, "w", newline="") as f:
            csv.writer(f).writerow(header)

    rows = list(csv.DictReader(SUBSCRIBERS_FILE.open()))
    existing = next((r for r in rows if r["email"] == email), None)

    if existing:
        existing["clusters"]         = "|".join(clusters)
        existing["whatsapp_upgrade"] = "pending" if whatsapp else "no"
        existing["whatsapp_number"]  = whatsapp_number if whatsapp else ""
        with open(SUBSCRIBERS_FILE, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return False
    else:
        with open(SUBSCRIBERS_FILE, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=header).writerow({
                "email":            email,
                "clusters":         "|".join(clusters),
                "whatsapp_upgrade": "pending" if whatsapp else "no",
                "whatsapp_number":  whatsapp_number,
                "signed_up_at":     datetime.now(timezone.utc).isoformat(),
            })
        return True


def mark_whatsapp_paid(email: str) -> None:
    """Update whatsapp_upgrade to 'paid' for this email in the CSV."""
    if not SUBSCRIBERS_FILE.exists():
        return
    rows = list(csv.reader(SUBSCRIBERS_FILE.open()))
    header = rows[0] if rows else []
    try:
        wa_col    = header.index("whatsapp_upgrade")
        email_col = header.index("email")
    except ValueError:
        return
    updated = False
    for row in rows[1:]:
        if row and row[email_col] == email and row[wa_col] in ("pending", "yes", "no"):
            row[wa_col] = "paid"
            updated = True
    if updated:
        with open(SUBSCRIBERS_FILE, "w", newline="") as f:
            csv.writer(f).writerows(rows)


# ── email ─────────────────────────────────────────────────────────────────────

def send_welcome(to_email: str, clusters: list[str]) -> None:
    if not SMTP_PASS or SMTP_PASS in ("replace_me", "your Gmail app password"):
        print(f"SMTP not configured — skipping welcome email to {to_email}", flush=True)
        return

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

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(FROM_EMAIL, to_email, msg.as_string())


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/subscribe", methods=["POST"])
def subscribe():
    try:
        data             = request.get_json(silent=True) or {}
        email            = (data.get("email") or "").strip().lower()
        clusters         = [c.strip() for c in (data.get("clusters") or []) if c.strip()]
        whatsapp         = bool(data.get("whatsapp", False))
        whatsapp_number  = (data.get("whatsapp_number") or "").strip()

        if not email or "@" not in email:
            return jsonify({"error": "valid email required"}), 400

        is_new = True
        try:
            is_new = save_subscriber(email, clusters, whatsapp, whatsapp_number)
        except Exception as exc:
            print(f"CSV write failed for {email}: {exc}", flush=True)

        if is_new:
            try:
                send_welcome(email, clusters)
            except Exception as exc:
                print(f"Welcome email failed for {email}: {exc}", flush=True)

        return jsonify({"ok": True, "new": is_new}), 200
    except Exception as exc:
        import traceback
        print(traceback.format_exc(), flush=True)
        return jsonify({"error": str(exc)}), 500


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"error": "email required"}), 400

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        mode="subscription",
        customer_email=email,
        client_reference_id=email,
        success_url=STRIPE_SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=STRIPE_CANCEL_URL,
    )
    return jsonify({"url": session.url})


@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return "", 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email   = session.get("client_reference_id") or session.get("customer_email", "")
        if email:
            mark_whatsapp_paid(email)
            print(f"WhatsApp paid: {email}", flush=True)

    return "", 200


@app.route("/signup-success")
def signup_success():
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WhatsApp added — What 2 Watch</title>
<style>
  body{font-family:'Gill Sans','Gill Sans MT','Trebuchet MS',sans-serif;
    background:#fff;color:#111827;display:flex;align-items:center;
    justify-content:center;min-height:100vh;padding:24px;}
  .box{max-width:420px;text-align:center;}
  .logo{font-size:18px;font-weight:700;color:#111827;margin-bottom:40px;}
  .logo span{color:#1A6DFF;}
  h1{font-size:28px;font-weight:700;margin-bottom:12px;}
  p{font-size:15px;color:#6B7280;line-height:1.65;}
  .badge{font-size:40px;margin-bottom:20px;}
</style></head><body>
<div class="box">
  <p class="logo">What 2 Watch<span>.</span></p>
  <div class="badge">💬</div>
  <h1>WhatsApp delivery added.</h1>
  <p>We'll be in touch to get your number set up. Your first pick arrives when something's worth watching.</p>
</div>
</body></html>"""


@app.route("/signup-cancel")
def signup_cancel():
    return redirect(STRIPE_CANCEL_URL.replace("http://localhost:5001", "") or "/")


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    data  = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "valid email required"}), 400
    if SUBSCRIBERS_FILE.exists():
        rows = list(csv.DictReader(SUBSCRIBERS_FILE.open()))
        filtered = [r for r in rows if r["email"] != email]
        if len(filtered) < len(rows):
            header = ["email", "clusters", "whatsapp_upgrade", "signed_up_at"]
            with open(SUBSCRIBERS_FILE, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=header)
                w.writeheader()
                w.writerows(filtered)
            print(f"Unsubscribed: {email}", flush=True)
    return jsonify({"ok": True})


@app.route("/admin/subscribers")
def admin_subscribers():
    if request.args.get("token") != os.environ.get("ADMIN_TOKEN", "w2w-admin"):
        return jsonify({"error": "unauthorised"}), 401
    if not SUBSCRIBERS_FILE.exists():
        return jsonify({"subscribers": []})
    rows = list(csv.DictReader(SUBSCRIBERS_FILE.open()))
    return jsonify({"count": len(rows), "subscribers": rows})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
