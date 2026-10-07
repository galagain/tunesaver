"""Opt-in Spotify metadata probe using HTTP only, never Chromium or audio downloads.

This uses the public web player's undocumented protocol. Keep it experimental:
Spotify may change the token, bundle or query format independently of TuneSaver.
"""

import json
import re
import time
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from playlist_sources import playlist_source
from spotify_browser import PlaylistPages


def read_playlist(url):
    provider, identifier, canonical = playlist_source(url)
    if provider != "spotify":
        raise ValueError("Expected a Spotify playlist")
    started = time.monotonic()
    request_count = 0
    with requests.Session() as session:
        def request(method, target, **kwargs):
            nonlocal request_count
            request_count += 1
            response = session.request(method, target, timeout=(5, 25), allow_redirects=False, **kwargs)
            if response.status_code != 200:
                raise ValueError(f"HTTP {response.status_code} from {urlparse(target).hostname}; no retry")
            return response

        embed = request("GET", f"https://open.spotify.com/embed/playlist/{identifier}")
        script = BeautifulSoup(embed.text, "html.parser").find("script", id="__NEXT_DATA__")
        state = json.loads(script.string)["props"]["pageProps"]["state"]
        token = state["settings"]["session"]["accessToken"]
        preview = state["data"]["entity"]["trackList"]

        page = request("GET", canonical, headers={"User-Agent":
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"})
        scripts = BeautifulSoup(page.text, "html.parser").find_all("script", src=True)
        bundle = next(tag["src"] for tag in scripts if re.fullmatch(
            r"https://open\.spotifycdn\.com/cdn/build/web-player/web-player\.[a-zA-Z0-9]+\.js", tag["src"]))
        javascript = request("GET", bundle).text
        match = re.search(r'"fetchPlaylistContents","query","([0-9a-f]{64})"', javascript)
        if not match:
            raise ValueError("Spotify query definition changed")
        query_hash = match[1]

        pages = PlaylistPages(identifier)
        pages.name = state["data"]["entity"]["name"]
        batches = []
        while not pages.complete:
            operation = "fetchPlaylistContents"
            offset = pages.first_missing
            body = {"operationName": operation,
                    "variables": {"uri": f"spotify:playlist:{identifier}", "offset": offset, "limit": 100},
                    "extensions": {"persistedQuery": {"version": 1, "sha256Hash": query_hash}}}
            response = request("POST", "https://api-partner.spotify.com/pathfinder/v2/query",
                headers={"Authorization": f"Bearer {token}", "Origin": "https://open.spotify.com",
                         "Referer": "https://open.spotify.com/"}, json=body).json()
            if response.get("errors"):
                raise ValueError("Spotify returned query errors")
            before = len(pages.items)
            pages.add(body, response)
            received = len(pages.items) - before
            if (not received and not pages.complete) or time.monotonic() - started > 120:
                raise ValueError("Pagination stopped making progress or timed out")
            batches.append({"offset": offset, "received": received})
        result = pages.result()
        for actual, expected in zip(result["tracks"], preview):
            if actual["title"] != expected["title"]:
                raise ValueError("Playlist order differs from its public preview")
        return {"status": "ok", "mode": "http-only", "url": canonical,
                "tracks": result["tracks"], "total": result["total"],
                "name": result["name"], "expected": result["total"], "read": len(result["tracks"]),
                "preview_checked": len(preview), "batches": batches, "requests": request_count,
                "seconds": round(time.monotonic() - started, 2),
                "first": result["tracks"][0] if result["tracks"] else None,
                "last": result["tracks"][-1] if result["tracks"] else None}
