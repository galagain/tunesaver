"""Discovery sources, identity matching and explainable ranking. No audio requests."""

import hashlib
import json
import os
import re
import time
from collections import Counter

import requests

from playlist_sources import normalized_words

LB = "https://labs.api.listenbrainz.org/"
ALGORITHM = "session_based_days_7500_session_300_contribution_5_threshold_15_limit_50_skip_30"


def identity(track):
    title = track.get("title", "")
    mix = track.get("mix", "")
    if mix and normalized_words(mix) not in normalized_words(title):
        title += " " + mix
    title = re.sub(r"\b(original mix|original version)\b", "", normalized_words(title)).strip()
    return hashlib.sha256((normalized_words(track.get("artist", "")) + "|" + title).encode()).hexdigest()[:24]


def select_seeds(groups, limit=12):
    """Equal influence per collection; explicit tracks have priority, duplicates do not."""
    candidates = {}
    for tracks, explicit in groups:
        unique = {identity(t): t for t in tracks if t.get("title") and t.get("artist")}
        for key, track in unique.items():
            weight = 2 if explicit else 1 / max(1, len(unique))
            if key not in candidates or candidates[key]["weight"] < weight:
                candidates[key] = {**track, "seed_id": key, "weight": weight}
    chosen, artists = [], Counter()
    while candidates and len(chosen) < limit:
        key = max(candidates, key=lambda k: candidates[k]["weight"] / (1 + artists[normalized_words(candidates[k]["artist"])]))
        track = candidates.pop(key)
        chosen.append(track)
        artists[normalized_words(track["artist"])] += 1
    return chosen


class Sources:
    def __init__(self, db):
        self.db = db
        self.last_call = 0

    def get(self, url, params):
        # Cache metadata only, never API credentials or user playlist/tag names.
        public_params = {k: v for k, v in params.items() if k != "api_key"}
        key = hashlib.sha256(json.dumps([url, public_params], sort_keys=True).encode()).hexdigest()
        with self.db() as db:
            row = db.execute("select payload from dig_cache where id = ? and expires > ?", (key, time.time())).fetchone()
        if row:
            return json.loads(row[0])
        time.sleep(max(0, 1.05 - (time.monotonic() - self.last_call)))
        self.last_call = time.monotonic()
        response = requests.get(url, params=params, timeout=(5, 15), allow_redirects=False,
                                headers={"User-Agent": "TuneSaver-Dig/1.0 (+https://github.com/galagain/tunesaver)"})
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Unexpected recommendation response")
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise ValueError("Recommendation service unavailable")
        with self.db() as db:
            db.execute("delete from dig_cache where expires < ?", (time.time(),))
            db.execute("insert or replace into dig_cache values (?, ?, ?)", (key, json.dumps(data), time.time() + 86400))
        return data

    def listenbrainz(self, seed):
        mappings = self.get(LB + "acr-lookup/json", {"artist_credit_name": seed["artist"], "recording_name": seed["title"]})
        mapping = next((m for m in mappings if identity({"title": m.get("recording_name", ""),
                       "artist": m.get("artist_credit_name", "")}) == identity(seed)), None)
        if not mapping:
            return []
        rows = self.get(LB + "similar-recordings/json", {"recording_mbids": mapping["recording_mbid"], "algorithm": ALGORITHM})
        result = []
        for i, row in enumerate(rows[:50]):
            mbid = row.get("recording_mbid", "")
            if not re.fullmatch(r"[a-f0-9-]{36}", mbid):
                continue
            result.append({"title": row["recording_name"], "artist": row["artist_credit_name"],
                           "album": row.get("release_name", ""), "source_url": f"https://musicbrainz.org/recording/{mbid}",
                           "engine": "ListenBrainz", "strength": 1 / (1 + i / 10), "reason": "Écoutes similaires"})
            cover = row.get("caa_release_mbid", "") or ""
            if re.fullmatch(r"[a-f0-9-]{36}", cover):
                result[-1]["cover_url"] = f"https://coverartarchive.org/release/{cover}/front-250"
        return result

    def lastfm(self, seed):
        key = os.environ.get("TUNESAVER_LASTFM_API_KEY", "")
        if not key:
            return []
        data = self.get("https://ws.audioscrobbler.com/2.0/", {"method": "track.getSimilar", "artist": seed["artist"],
                        "track": seed["title"], "api_key": key, "format": "json", "limit": 40, "autocorrect": 1})
        return [{"title": t["name"], "artist": t["artist"]["name"], "engine": "Last.fm",
                 "strength": 1 / (1 + i / 10), "reason": "Écoutes similaires"}
                for i, t in enumerate(data.get("similartracks", {}).get("track", []))]


def rank(candidates, seeds, owned, feedback, mode):
    excluded = {identity(t) for t in owned + seeds}
    excluded.update(k for k, v in feedback.items() if v["state"] == "rejected")
    preferred = Counter(normalized_words(v["artist"]) for v in feedback.values() if v["state"] == "kept")
    seed_artists = {normalized_words(t["artist"]) for t in seeds}
    merged = {}
    for track, seed in candidates:
        key = identity(track)
        if key in excluded or not track.get("title") or not track.get("artist"):
            continue
        item = merged.setdefault(key, {**track, "id": key, "signals": {}, "references": {}, "matches": [], "checked": False})
        signal = (seed["seed_id"], track["engine"])
        item["signals"][signal] = max(item["signals"].get(signal, 0), track["strength"] * (1.5 if seed["weight"] >= 2 else 1))
        item["references"][seed["seed_id"]] = f"{seed['artist']} · {seed['title']}"
        if track.get("download_url") and not any(m["id"] == track["id"] for m in item["matches"]):
            item["matches"].append(track)
    for item in merged.values():
        novel = normalized_words(item["artist"]) not in seed_artists
        item["score"] = sum(item["signals"].values()) + min(0.3, preferred[normalized_words(item["artist"])] * 0.05)
        item["score"] *= (1.8 if novel else 0.4) if mode == "explore" else (1 if novel else 1.15)
        item["engines"] = sorted({s[1] for s in item.pop("signals")})
        item["references"] = list(item["references"].values())
        item["reasons"] = [item["reason"], f"{len(item['references'])} référence(s)"]
        if novel:
            item["reasons"].append("Autre artiste")
    # Diversify each batch rather than allowing one artist to fill the first page.
    result, counts = [], Counter()
    remaining = list(merged.values())
    while remaining and len(result) < 60:
        best = max(remaining, key=lambda t: t["score"] / (1 + counts[normalized_words(t["artist"]) ]))
        remaining.remove(best)
        counts[normalized_words(best["artist"])] += 1
        result.append(best)
    return result
