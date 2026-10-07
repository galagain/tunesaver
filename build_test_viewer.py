#!/usr/bin/env python3
"""Build a self-contained HTML viewer from a completed metadata test run."""

import argparse
import json
from pathlib import Path

from export_playlist import ROOT, write_output


def build_viewer(directory):
    directory = Path(directory).resolve()
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    scenarios = []
    for result in report["results"]:
        payload = None
        if result["status"] == "ok":
            filename = next(name for name in result["files"] if name.endswith(".json"))
            path = (directory / filename).resolve()
            if path.parent != directory:
                raise ValueError("Fichier de scenario hors du dossier de test.")
            payload = json.loads(path.read_text(encoding="utf-8"))
        scenarios.append({"result": result, "payload": payload})
    data = json.dumps({"created_at": report["created_at"], "scenarios": scenarios}, ensure_ascii=True)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    assets = ROOT / "web" / "test-viewer"
    html = (assets / "index.html").read_text(encoding="utf-8")
    icons = (ROOT / "web" / "vendor" / "player-icons.js").read_text(encoding="utf-8")
    # The vendored module is embedded for file:// use, without external imports.
    icons = icons.replace("export const ", "const ")
    html = html.replace("/* VIEWER_CSS */", (assets / "style.css").read_text(encoding="utf-8"))
    html = html.replace("/* VIEWER_JS */", icons + "\n" + (assets / "app.js").read_text(encoding="utf-8"))
    html = html.replace("<!-- VIEWER_DATA -->", data)
    destination = directory / "viewer.html"
    write_output(destination, html, force=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, help="Dossier contenant report.json")
    args = parser.parse_args()
    directory = args.directory
    if directory is None:
        reports = sorted((ROOT / "data" / "playlist-tests").glob("*/report.json"), key=lambda p: p.stat().st_mtime)
        if not reports:
            parser.error("Aucun resultat de test disponible.")
        directory = reports[-1].parent
    print(build_viewer(directory))


if __name__ == "__main__":
    main()
