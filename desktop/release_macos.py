#!/usr/bin/env python3
"""Sign and notarize a COPY of the test app with an existing Developer ID.

Credentials are read by notarytool from a previously configured Keychain profile.
Never put an Apple password, API key or certificate private key in this repository.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from build_macos import ROOT, VERSION, capture, macho, run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", required=True, help="Developer ID Application: …")
    parser.add_argument("--keychain-profile", required=True)
    args = parser.parse_args()
    if not args.identity.startswith("Developer ID Application:"):
        raise SystemExit("A Developer ID Application certificate is required for distribution outside the Mac App Store.")
    if args.identity not in capture("security", "find-identity", "-v", "-p", "codesigning"):
        raise SystemExit("The requested Developer ID and its private key are not available in this Mac's Keychain.")
    release = ROOT / "dist/release"
    if release.exists():
        raise SystemExit("dist/release already exists. Move the previous release aside before creating another.")
    release.mkdir()
    app = release / "TuneSaver.app"
    shutil.copytree(ROOT / "dist/TuneSaver.app", app, symlinks=True)
    entitlements = ROOT / "desktop/macos/runtime-entitlements.plist"
    for path in sorted(app.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if macho(path):
            options = []
            if path.name in {"python3.13", "node", "chrome-headless-shell", "Chromium", "Google Chrome for Testing"}:
                options = ["--entitlements", entitlements]
            run("codesign", "--force", "--sign", args.identity, "--options", "runtime", "--timestamp", *options, path)
    for path in sorted(app.rglob("*.app"), key=lambda p: len(p.parts), reverse=True):
        run("codesign", "--force", "--sign", args.identity, "--options", "runtime", "--timestamp", path)
    run("codesign", "--force", "--sign", args.identity, "--options", "runtime", "--timestamp", app)
    run("codesign", "--verify", "--deep", "--strict", app)
    submission = release / "notarization.zip"
    run("ditto", "-c", "-k", "--keepParent", app, submission)
    result = run("xcrun", "notarytool", "submit", submission, "--keychain-profile", args.keychain_profile,
                 "--wait", "--output-format", "json", stdout=subprocess.PIPE, text=True)
    report = json.loads(result.stdout)
    (release / "notarization.json").write_text(json.dumps(report, indent=2) + "\n")
    if report.get("status") != "Accepted":
        raise SystemExit("Notarization was not accepted. Inspect dist/release/notarization.json and notarytool log before distribution.")
    run("xcrun", "stapler", "staple", app)
    run("xcrun", "stapler", "validate", app)
    run("spctl", "--assess", "--type", "execute", "--verbose=2", app)
    info = json.loads((ROOT / "dist/build-info.json").read_text())
    archive = release / f"TuneSaver-{VERSION}-macOS-{info['architecture']}.zip"
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, archive)
    info["notarized"] = True
    (release / "build-info.json").write_text(json.dumps(info, indent=2) + "\n")
    (release / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    submission.unlink()
    print(f"Signed and notarized archive: {archive}")


if __name__ == "__main__":
    main()
