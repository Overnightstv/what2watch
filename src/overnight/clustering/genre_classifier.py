"""Genre-based cluster classifier.

Maps each series to one or more interest clusters using channel signals
and title keywords. This is a proxy for real respondent-level affinity
data (spec 4.4-4.5) — when BARB panel viewing is available, replace
classify_series() with cluster_lift() calls from metrics/affinity.py.

Clusters are defined in config/thresholds.yaml under 'clusters'.
"""
from __future__ import annotations

import re


# Channel → primary cluster mapping
_CHANNEL_CLUSTERS: dict[str, list[str]] = {
    # Drama-leaning
    "Sky Atlantic":        ["drama"],
    "Sky Showcase":        ["drama"],
    "Alibi":              ["drama"],
    "Drama":              ["drama"],
    "BBC Four":           ["drama", "arts"],
    "More4":              ["drama", "factual"],

    # Factual / documentary
    "National Geographic": ["factual"],
    "Discovery":           ["factual"],
    "Sky Documentaries":   ["factual"],
    "Sky History":         ["factual"],
    "Sky Nature":          ["factual"],
    "Eden":               ["factual"],
    "Yesterday":          ["factual"],
    "Really":             ["factual"],

    # Entertainment / reality
    "ITV2":               ["entertainment"],
    "ITVBe":              ["entertainment"],
    "E4":                 ["entertainment"],
    "W":                  ["entertainment"],
    "TLC":                ["entertainment"],

    # Comedy
    "Dave":               ["comedy"],
    "Gold":               ["comedy"],
    "Sky Comedy":         ["comedy"],
    "Comedy Central":     ["comedy"],

    # Sport
    "Sky Sports Main Event":      ["sport"],
    "Sky Sports Premier League":  ["sport"],
    "Sky Sports Football":        ["sport"],
    "Sky Sports Cricket":         ["sport"],
    "Sky Sports Action":          ["sport"],
    "Sky Sports Golf":            ["sport"],
    "Sky Sports F1":              ["sport"],
    "Sky Sports Racing":          ["sport"],
    "TNT Sports 1":               ["sport"],
    "TNT Sports 2":               ["sport"],
    "TNT Sports 3":               ["sport"],
    "TNT Sports 4":               ["sport"],
    "Eurosport":                  ["sport"],
    "Eurosport 2":                ["sport"],
    "Quest":                      ["sport", "factual"],

    # Kids / family
    "CBeebies":           ["kids"],
    "CBBC":               ["kids"],
    "Nickelodeon":        ["kids"],
    "Nick Jr":            ["kids"],
    "Cartoonito":         ["kids"],
    "Boomerang":          ["kids"],

    # Arts & culture
    "Sky Arts":           ["arts"],
}

# Title keyword → additional clusters (applied on top of channel signal)
_TITLE_SIGNALS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r'\b(murder|detective|crime|killer|arrest|police|thriller|heist)\b', re.I), ["drama"]),
    (re.compile(r'\b(documentary|history|science|nature|planet|wildlife|war|secrets?|tours?|safari|expedition|explore|exploring|journeys?|countryside|landscape|rural|antiques?|archaeology|digging|gardening|gardeners?|railways?|heritage)\b', re.I), ["factual"]),
    (re.compile(r'\b(comedy|sitcom|stand.?up|funny)\b', re.I), ["comedy"]),
    (re.compile(r'\b(love island|strictly|bake off|apprentice|idol|got talent|big brother|celebrity)\b', re.I), ["entertainment"]),
    (re.compile(r'\b(f1|formula|football|premier league|champions league|cricket|wimbledon|rugby|golf|ufc|nba|nfl)\b', re.I), ["sport"]),
    (re.compile(r'\b(kids|children|junior|junior|cbeebies|cbbc|cartoon|animation)\b', re.I), ["kids"]),
    (re.compile(r'\b(art|opera|ballet|classical|museum|gallery|shakespeare)\b', re.I), ["arts"]),
]

# Mixed-signal channels — need title to disambiguate; default to broad clusters
_BROAD_CHANNELS: dict[str, list[str]] = {
    "BBC One":    ["drama", "entertainment", "factual"],
    "BBC Two":    ["factual", "drama", "arts"],
    "BBC Three":  ["entertainment", "drama"],
    "ITV":        ["drama", "entertainment"],
    "Channel 4":  ["factual", "entertainment", "drama"],
    "Channel 5":  ["factual", "entertainment"],
    "Sky One":    ["entertainment", "drama"],
    "Sky Max":    ["entertainment", "drama"],
    "Sky Witness":["drama", "factual"],
    "Sky Crime":  ["drama", "factual"],
    "Film4":      ["drama"],
    "5STAR":      ["entertainment"],
    "5USA":       ["entertainment", "drama"],
    "ITV3":       ["drama"],
    "ITV4":       ["entertainment", "sport"],
    "Crime + Investigation": ["drama", "factual"],
    "Talking Pictures": ["drama"],
}


# Streaming platform → primary cluster mapping
_PLATFORM_CLUSTERS: dict[str, list[str]] = {
    "Netflix":      ["drama", "entertainment"],
    "Prime Video":  ["drama", "entertainment"],
    "Disney+":      ["entertainment", "drama", "kids"],
    "Apple TV+":    ["drama"],
    "NOW":          ["drama", "entertainment"],
    "Paramount+":   ["drama", "entertainment"],
    "ITVX":         ["drama", "entertainment", "factual"],
    "iPlayer":      ["drama", "factual", "entertainment"],
    "All 4":        ["factual", "entertainment", "drama"],
    "My5":          ["entertainment"],
    "BritBox":      ["drama"],
    "Mubi":         ["arts", "drama"],
    "Shudder":      ["drama"],
}

# Genre keyword → cluster refinement for VOD titles
_VOD_GENRE_SIGNALS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r'\b(documentary|factual|nature|history|science|true crime)\b', re.I), ["factual"]),
    (re.compile(r'\b(comedy|stand.?up|sitcom)\b', re.I), ["comedy"]),
    (re.compile(r'\b(animation|cartoon|family|children|kids)\b', re.I), ["kids"]),
    (re.compile(r'\b(sport|football|cricket|tennis|golf|racing|f1)\b', re.I), ["sport"]),
    (re.compile(r'\b(arts|culture|music|ballet|opera|theatre)\b', re.I), ["arts"]),
]


def classify_vod_series(series_id: str, title: str, platform: str, genre: str) -> list[str]:
    """Return cluster IDs for a VOD title using platform and genre signals."""
    clusters: set[str] = set()

    if platform in _PLATFORM_CLUSTERS:
        clusters.update(_PLATFORM_CLUSTERS[platform])

    # Refine by genre string from the API
    for pattern, tags in _VOD_GENRE_SIGNALS:
        if pattern.search(genre) or pattern.search(title):
            clusters.update(tags)

    if not clusters:
        clusters.add("entertainment")

    return sorted(clusters)


def classify_series(series_id: str, title: str, channel: str) -> list[str]:
    """Return the list of cluster IDs this series belongs to.

    Priority: explicit channel mapping → title keyword signals → broad
    channel defaults. A series may belong to multiple clusters.
    """
    clusters: set[str] = set()

    # Specific channel signal
    if channel in _CHANNEL_CLUSTERS:
        clusters.update(_CHANNEL_CLUSTERS[channel])

    # Title keyword signals (additive)
    for pattern, tags in _TITLE_SIGNALS:
        if pattern.search(title):
            clusters.update(tags)

    # Fall back to broad channel defaults if nothing else matched
    if not clusters and channel in _BROAD_CHANNELS:
        clusters.update(_BROAD_CHANNELS[channel])

    # Last resort: entertainment (something is always better than nothing)
    if not clusters:
        clusters.add("entertainment")

    return sorted(clusters)


def build_cluster_index(
    universe: dict[str, list],
    vod_universe: dict[str, list] | None = None,
) -> dict[str, list[str]]:
    """Return {cluster_id: [series_id, ...]} for linear + VOD universes."""
    index: dict[str, list[str]] = {}

    for sid, eps in universe.items():
        latest = eps[-1]
        for cluster in classify_series(sid, latest.title, latest.channel):
            index.setdefault(cluster, []).append(sid)

    if vod_universe:
        for sid, recs in vod_universe.items():
            latest = recs[-1]
            for cluster in classify_vod_series(sid, latest.title, latest.platform, latest.genre):
                index.setdefault(cluster, []).append(sid)

    return index
