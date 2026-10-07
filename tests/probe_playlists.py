"""Opt-in live metadata checks. Never searches for matches or downloads audio."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import webapp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", help="Only this source (repeatable)")
    parser.add_argument("--url", help="Read a different public playlist")
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("playlist_cases.json").read_text())
    if args.url:
        cases = [{"source": webapp.playlist_sources.playlist_source(args.url)[0], "url": args.url}]
    if args.source:
        cases = [case for case in cases if case["source"] in args.source]
    if not cases:
        parser.error("No matching source")
    failed = 0
    with ExitStack() as guard:
        for name in ("search_tidal", "search_qobuz", "search_beatport", "enqueue_import_track", "local_db"):
            guard.enter_context(patch.object(webapp, name, side_effect=AssertionError(f"Forbidden during metadata read: {name}")))
        guard.enter_context(patch.object(webapp.musicdl, "enqueue", side_effect=AssertionError("Audio download forbidden")))
        for case in cases:
            started = time.monotonic()
            report = dict(case)
            try:
                result = webapp.read_import_source(case["url"])
                tracks = result["tracks"]
                assert tracks and all(track.get("title") for track in tracks), "Missing titles"
                if "total" in result:
                    assert result["total"] == len(tracks), "Incomplete playlist"
                report.update(status="ok", name=result["name"], read=len(tracks),
                              expected=result.get("total"), first=tracks[0], last=tracks[-1])
            except Exception as exc:
                failed += 1
                # Public errors are sanitized by the readers; never expose request URLs/tokens.
                public = isinstance(exc, (webapp.SourceError, webapp.playlist_sources.PlaylistError, AssertionError))
                report.update(status="failed", error=str(exc) if public else type(exc).__name__)
            report["seconds"] = round(time.monotonic() - started, 2)
            print(json.dumps(report, ensure_ascii=False), flush=True)
    return int(failed > 0)


if __name__ == "__main__":
    sys.exit(main())
