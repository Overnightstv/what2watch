"""Post-generation compliance lint. Spec section 6.

Scans generated copy for anything resembling an audience figure.
Philosophy: false positives are acceptable, false negatives are not -
a blocked send goes to human review, a leaked figure breaches a licence.
"""
from __future__ import annotations

import re

# number (incl. decimals / "4.2m" / "4,200,000") followed by an audience noun
_NUM = r"\d[\d,.]*\s*"
_PATTERNS = [
    re.compile(_NUM + r"(m\b|million|viewers?\b|households?\b|homes\b|share\b|%|percent)", re.I),
    re.compile(r"(audience|share|rating)s?\s+of\s+" + _NUM, re.I),
    re.compile(r"\b(a|one)\s+(third|quarter|half|fifth)\s+of\s+(the\s+)?(country|nation|britain|viewers)", re.I),
    re.compile(r"\bwatched\s+by\s+" + _NUM, re.I),
]

# permitted numeric contexts: times, dates, ranks, episode/series counts, streaks
_SAFE = re.compile(
    r"(\d{1,2}[:.]\d{2}\s*(am|pm)?)|(\bno\.?\s*\d\b)|(#\d\b)"
    r"|(\b(ep|episode|series|week|weeks|hours?|part)\s*\d+)"
    r"|(\d+\s*(weeks?|episodes?|hours?|parts?|nights?)\b)"
    r"|(\d{1,2}\s*(am|pm)\b)",
    re.I,
)


class ComplianceLint:
    def check_text(self, text: str) -> list[str]:
        issues = []
        cleaned = _SAFE.sub(" ", text)
        for pat in _PATTERNS:
            for m in pat.finditer(cleaned):
                issues.append(f"figure_pattern: '{m.group(0).strip()}'")
        return issues

    def check_edition(self, copy: dict, allowed_series: set[str]) -> list[str]:
        issues: list[str] = []
        texts = [copy.get("subject_line", ""), copy.get("whatsapp_compact", "")]
        for item in copy.get("items", []):
            if item.get("series_id") not in allowed_series:
                issues.append(f"unknown_series: {item.get('series_id')}")
            texts += [item.get("headline", ""), item.get("body", ""),
                      item.get("gem_line", "")]
            if len(item.get("body", "").split()) > 45:
                issues.append(f"body_too_long: {item.get('series_id')}")
        for t in texts:
            issues += self.check_text(t or "")
        exclaims = sum((t or "").count("!") for t in texts)
        if exclaims > 1:
            issues.append("too_many_exclamations")
        return issues
