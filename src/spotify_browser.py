"""Read public Spotify playlist pages using an anonymous, short-lived browser."""

import json
import os
import re
import sys
import time
from urllib.parse import urlparse


class PlaylistPages:
    def __init__(self, playlist_id, max_tracks=500):
        self.uri = f"spotify:playlist:{playlist_id}"
        self.max_tracks = max_tracks
        self.total = None
        self.name = ""
        self.items = {}

    def add(self, request, response):
        if request.get("operationName") not in {"fetchPlaylist", "fetchPlaylistContents"}:
            return
        if request.get("variables", {}).get("uri") != self.uri:
            return
        playlist = response.get("data", {}).get("playlistV2", {})
        content = playlist.get("content")
        if not isinstance(content, dict):
            raise ValueError("Spotify n'a pas fourni le contenu de cette playlist publique.")
        total = content["totalCount"]
        offset = content["pagingInfo"]["offset"]
        if not isinstance(total, int) or not 0 <= total <= self.max_tracks:
            raise ValueError(f"La playlist depasse la limite de {self.max_tracks} titres.")
        if self.total is not None and total != self.total:
            raise ValueError("La playlist a change pendant sa lecture. Relancez l'import.")
        self.total = total
        self.name = playlist.get("name") or self.name
        for index, item in enumerate(content["items"], offset):
            if not 0 <= index < total:
                raise ValueError("Pagination Spotify incoherente.")
            if index in self.items and self.items[index].get("uid") != item.get("uid"):
                raise ValueError("L'ordre de la playlist a change. Relancez l'import.")
            self.items[index] = item

    @property
    def complete(self):
        return self.total is not None and bool(self.name) and len(self.items) == self.total

    def result(self):
        if not self.complete:
            raise ValueError(f"Lecture Spotify incomplete ({len(self.items)}/{self.total or '?'} titres). Relancez l'import.")
        tracks = []
        for index in range(self.total):
            data = (self.items[index].get("itemV2") or {}).get("data") or {}
            if data.get("__typename") != "Track":
                tracks.append({"title": f"Titre Spotify indisponible (position {index + 1})", "artist": "", "duration": 0})
                continue
            tracks.append({
                "title": data["name"],
                "artist": ", ".join(artist["profile"]["name"] for artist in data["artists"]["items"]),
                "duration": data["trackDuration"]["totalMilliseconds"] / 1000,
            })
        return {"name": self.name, "tracks": tracks, "total": self.total}


def read_playlist(playlist_id):
    from playwright.sync_api import sync_playwright

    if not re.fullmatch(r"[A-Za-z0-9]{22}", playlist_id):
        raise ValueError("Identifiant Spotify invalide.")
    pages = PlaylistPages(playlist_id)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("TUNESAVER_CHROMIUM_PATH", "/usr/bin/chromium"),
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900}, locale="en-US")
            page.route("**/*", lambda route: route.abort() if route.request.resource_type in {"media", "image", "font"} else route.continue_())

            def collect(response):
                if urlparse(response.url).hostname != "api-partner.spotify.com":
                    return
                try:
                    request = response.request.post_data_json or {}
                    if request.get("operationName") in {"fetchPlaylist", "fetchPlaylistContents"}:
                        pages.add(request, response.json())
                except (ValueError, KeyError, TypeError):
                    errors.append("Spotify a fourni une page invalide ou modifiee pendant la lecture.")

            page.on("response", collect)
            page.goto(f"https://open.spotify.com/playlist/{playlist_id}", wait_until="domcontentloaded", timeout=45000)
            deadline = time.monotonic() + 100
            while not pages.complete and not errors and time.monotonic() < deadline:
                reject = page.get_by_role("button", name="REJECT ALL", exact=True)
                if reject.is_visible():
                    reject.click(timeout=2000)
                page.mouse.move(1000, 450)
                page.mouse.wheel(0, 1100)
                page.wait_for_timeout(750)
            if errors:
                raise ValueError(errors[0])
            return pages.result()
        finally:
            browser.close()


if __name__ == "__main__":
    try:
        print(json.dumps(read_playlist(sys.argv[1])))
    except Exception as exc:
        # Browser exceptions can contain request details; expose only our own validation messages.
        message = str(exc) if isinstance(exc, ValueError) else "Lecture Spotify par navigateur impossible pour le moment."
        print(json.dumps({"error": message}))
        sys.exit(1)
