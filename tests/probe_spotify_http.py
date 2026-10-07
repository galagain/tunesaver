"""Run the HTTP-only Spotify reader and print a diagnostic summary."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from spotify_http import read_playlist


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default="https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc")
    args = parser.parse_args()
    try:
        report = read_playlist(args.url)
        report.pop("tracks")
        print(json.dumps(report, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__,
                          "error": str(exc) if type(exc) is ValueError else "Unexpected source response"}))
        sys.exit(1)
