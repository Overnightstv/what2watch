"""Copy generation harness. Spec section 7.

Sends the selected AlertItems (derived descriptors only) to the LLM,
validates the returned JSON, and runs the compliance lint (spec 6).
Any lint hit blocks the send to human review - never auto-publishes.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict

from overnight.copygen.lint import ComplianceLint
from overnight.models import Edition

SYSTEM_PROMPT = """You write 'What 2 Watch' - the UK daily TV verdict. You turn measured audience
signals into short, confident, honest consumer copy.

VOICE: Direct, warm, opinionated, British. Like a sharp friend who works in
TV. Never breathless, never press-release. Contractions fine. One light
flourish per edition maximum.

HARD RULES - violating any of these fails the output:
1. NEVER mention BARB, ratings, audience figures, shares, or percentages.
   Banned patterns: BARB, ratings data, any number followed by m/million/
   viewers/share/%, 'x million', 'a third of the country', any audience
   arithmetic of any kind.
2. Express performance ONLY through: rank ('last night's no.1 drama'),
   momentum ('third week of growth'), comparison ('well ahead of its slot'),
   retention ('held its audience all series'), or the provided descriptor.
3. Use ONLY the shows and facts in the payload. Never add shows, cast names,
   plot details, or claims not present in the input.
4. Every pick carries its reason in one sentence, grounded in the payload's
   'evidence' field. If evidence feels thin, say less, not more.
5. Respect 'availability' field exactly - never send viewers to a service
   not listed for that programme.
6. British English. No exclamation marks except at most one per edition.
7. Never disparage a show beyond what a 'skip' item's evidence supports,
   and never mock people who enjoy it.

OUTPUT: JSON only, no markdown fences, matching:
{"subject_line": str (<= 8 words, no clickbait),
 "items": [{"series_id": str, "headline": str (<= 9 words),
            "body": str (<= 40 words),
            "chip": "Banker"|"Rising"|"Finale"|"Live"|"Gem"|"Skip"|"Verdict"}],
 "whatsapp_compact": str (whole edition <= 110 words, light emoji ok)}

Weekly gem items additionally get "gem_line": one sentence of the form
'People with your taste are unusually loyal to this - and almost
nobody has found it yet', adapted to the evidence. Never claim precision
the payload does not contain."""


def build_payload(edition: Edition) -> str:
    items = []
    for it in edition.items:
        d = asdict(it)
        d.pop("score")  # internal only, never shown to the copy layer consumer
        d["tx"] = it.tx.isoformat() if it.tx else None
        items.append(d)
    return json.dumps({
        "edition_date": edition.edition_date.isoformat(),
        "quiet_day": edition.quiet_day,
        "items": items,
    }, default=str)


def generate_copy(edition: Edition, client=None, model: str = "claude-sonnet-5",
                  max_retries: int = 1) -> dict:
    """Returns {"status": "ok"|"blocked", "copy": dict|None, "lint": [...]}.

    `client` is an anthropic.Anthropic instance; created from env if None.
    In Claude Code, ANTHROPIC_API_KEY is already configured.
    """
    if client is None:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    lint = ComplianceLint()
    payload = build_payload(edition)
    last_issues: list[str] = []

    for _ in range(max_retries + 1):
        resp = client.messages.create(
            model=model,
            max_tokens=2000,
            temperature=0.4,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": payload}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        try:
            copy = json.loads(text)
        except json.JSONDecodeError:
            last_issues = ["invalid_json"]
            continue

        issues = lint.check_edition(copy, allowed_series={i.series_id for i in edition.items})
        if not issues:
            return {"status": "ok", "copy": copy, "lint": []}
        last_issues = issues

    return {"status": "blocked", "copy": None, "lint": last_issues}
