#!/usr/bin/env python3
"""Small JSON bridge between the web service and qobuz-dl."""

from __future__ import annotations

import configparser
import json
import os
import sys
from pathlib import Path


PREFIX = "MUSICDL_JSON:"


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("qobuz-search: une recherche est requise", file=sys.stderr)
        return 2
    config_path = Path(
        os.environ.get(
            "MUSICDL_QOBUZ_CONFIG",
            "/state/qobuz/.config/qobuz-dl/config.ini",
        )
    )
    config = configparser.ConfigParser()
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config.read_file(handle)
        values = config["DEFAULT"]
        from qobuz_dl.qopy import Client

        client = Client.from_token(
            values["user_id"],
            values["user_auth_token"],
            values["app_id"],
            [secret for secret in values["secrets"].split(",") if secret],
        )
        result = client.search_tracks(sys.argv[1].strip(), 8)
    except Exception as exc:
        print(f"qobuz-search: {type(exc).__name__}", file=sys.stderr)
        return 1
    if not isinstance(result, dict):
        print("qobuz-search: réponse invalide", file=sys.stderr)
        return 1
    print(PREFIX + json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
