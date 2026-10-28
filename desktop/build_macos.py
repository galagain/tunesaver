#!/usr/bin/env python3
"""Build a portable .app and DMG on macOS. Developer tools are build-only.

No server .env, credentials, user data or working-tree .git directory is bundled.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".build"
SOURCES = BUILD / "sources"
BIN = BUILD / "bin"
VERSION = "0.1.0"
PYTHON = "3.13.15"
ARCHIVES = {
    "beatportdl": ("unspok3n/beatportdl", "878d3f61c5a98cffd43b9e0306b8cbc75488999e", "91f64963d51c0e358a4dcc595e8ef1b081f0e3e5a300e8ac93152a4cfade5bdc"),
    "qobuz-dl": ("guyathomas/qobuz-dl", "00f8ce21b276ddf9278f0a944a2f85de2421d98d", "32d1effc81c4ee7772ea49ada289ec4724d901faf29d03306859d6d8080822a4"),
    "libkeyfinder": ("mixxxdj/libkeyfinder", "941e517ebf853c2153a8b9d6efcc0c729199aa0b", "7b922bb7605d2ada93bafe54a766b4547f109277f46dc48c31d946afb11639dd"),
    "keyfinder-cli": ("evanpurkhiser/keyfinder-cli", "8958d9219fda8a48952da365d19752e43ee81f63", "be7b6a80b52b707ece2b621ddda46d48c60138630a534934949d88d2ce3a12a7"),
    "qm-dsp": ("c4dm/qm-dsp", "e34a3cc188332ed7c33cd9257ef164de5b587191", "34d77740f926a73be1a996a2ed77c064e0555a346ea2734b91d1cfdf9a1ac215"),
}


def run(*args, **kwargs):
    print("+", " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def capture(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def source(name):
    repo, commit, checksum = ARCHIVES[name]
    archive = SOURCES / f"{name}.tar.gz"
    if not archive.exists():
        urllib.request.urlretrieve(f"https://codeload.github.com/{repo}/tar.gz/{commit}", archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
        raise SystemExit(f"Checksum mismatch: {archive}")
    destination = SOURCES / name
    if not destination.exists():
        destination.mkdir()
        run("tar", "-xf", archive, "-C", destination, "--strip-components=1")
    return destination


def native_tools():
    prefix = Path(capture("brew", "--prefix"))
    goenv = {**os.environ, "GOCACHE": str(BUILD / "go-cache"), "GOTOOLCHAIN": "go1.26.7", "CGO_ENABLED": "0"}
    beatport = source("beatportdl")
    (beatport / "cmd/musicdl-search").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "src/beatport_search.go", beatport / "cmd/musicdl-search/main.go")
    run("go", "build", "-mod=vendor", "-trimpath", "-ldflags=-s -w", "-o", BIN / "beatport-search", "./cmd/musicdl-search", cwd=beatport, env=goenv)
    flags = f"-I{prefix}/opt/taglib/include"
    goenv.update({"CGO_ENABLED": "1", "CGO_CFLAGS": flags, "CGO_CXXFLAGS": flags,
                  "CGO_LDFLAGS": f"-L{prefix}/opt/taglib/lib -lc++"})
    run("go", "build", "-mod=vendor", "-trimpath", "-ldflags=-s -w", "-o", BIN / "beatportdl", "./cmd/beatportdl", cwd=beatport, env=goenv)
    library = source("libkeyfinder")
    run("cmake", "-S", library, "-B", library / "build", "-DBUILD_TESTING=OFF", "-DBUILD_SHARED_LIBS=OFF", "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_INSTALL_PREFIX={BUILD}/native", f"-DCMAKE_PREFIX_PATH={prefix}")
    run("cmake", "--build", library / "build", "-j4")
    run("cmake", "--install", library / "build")
    keyfinder = source("keyfinder-cli")
    run("cmake", "-S", keyfinder, "-B", keyfinder / "build", "-DCMAKE_BUILD_TYPE=Release", f"-DCMAKE_PREFIX_PATH={BUILD}/native;{prefix}")
    run("cmake", "--build", keyfinder / "build", "-j4")
    shutil.copy2(keyfinder / "build/keyfinder-cli", BIN / "keyfinder-cli")
    rhythm = source("qm-dsp")
    run("make", "-f", "build/general/Makefile.inc", "-j4",
        "CXXFLAGS=-O2 -std=c++11 -DUSE_PTHREADS -I. -Iext/kissfft -Iext/kissfft/tools -Dkiss_fft_scalar=double",
        "CFLAGS=-O2 -I. -Iext/kissfft -Iext/kissfft/tools -Dkiss_fft_scalar=double -DNO_BLAS_WRAP -DADD_ -Iext/clapack/include -Iext/cblas/include", cwd=rhythm)
    run("c++", "-O2", "-std=c++17", f"-I{rhythm}", f"-I{prefix}/opt/libsndfile/include", ROOT / "src/qm_beats.cpp", rhythm / "libqm-dsp.a", f"-L{prefix}/opt/libsndfile/lib", "-lsndfile", "-lpthread", "-o", BIN / "qm-beats")


def macho(path):
    if path.is_symlink() or not path.is_file():
        return False
    with path.open("rb") as handle:
        return handle.read(4) in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf")


def bundle_libraries(resources):
    libraries = resources / "lib"
    libraries.mkdir()
    pending = list((resources / "bin").iterdir())
    seen = set()
    manifest = {}
    while pending:
        binary = pending.pop()
        if binary in seen:
            continue
        seen.add(binary)
        for line in capture("otool", "-L", binary).splitlines()[1:]:
            dependency = line.strip().split(" (", 1)[0]
            if not dependency.startswith(("/opt/homebrew/", "/usr/local/")):
                continue
            original = Path(dependency).resolve()
            target = libraries / original.name
            if not target.exists():
                shutil.copy2(original, target)
                target.chmod(0o755)
                pending.append(target)
                manifest[original.name] = {"original": str(original), "sha256": hashlib.sha256(original.read_bytes()).hexdigest()}
                run("install_name_tool", "-id", "@loader_path/" + target.name, target)
            relative = os.path.relpath(target, binary.parent)
            run("install_name_tool", "-change", dependency, "@loader_path/" + relative, binary)
    (resources / "native-libraries.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def icon(resources):
    image = BUILD / "icon.png"
    run("swift", ROOT / "desktop/macos/icon.swift", image)
    iconset = BUILD / "TuneSaver.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
            run("sips", "-z", size * scale, size * scale, image, "--out", iconset / name, stdout=subprocess.DEVNULL)
    run("iconutil", "-c", "icns", iconset, "-o", resources / "TuneSaver.icns")


def sign(app, identity):
    # Sign leaves before bundles; adhoc builds are test artifacts, not notarized releases.
    for path in sorted(app.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if macho(path):
            run("codesign", "--force", "--sign", identity, path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for path in sorted(app.rglob("*.app"), key=lambda p: len(p.parts), reverse=True):
        run("codesign", "--force", "--deep", "--sign", identity, path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run("codesign", "--force", "--deep", "--sign", identity, app)
    run("codesign", "--verify", "--deep", "--strict", app)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-native-build", action="store_true")
    parser.add_argument("--skip-archives", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        raise SystemExit("Build on macOS, on the architecture being shipped.")
    for directory in (SOURCES, BIN, ROOT / "dist"):
        directory.mkdir(parents=True, exist_ok=True)
    for name in ARCHIVES:
        source(name)
    if not args.skip_native_build:
        native_tools()
    arch = platform.machine()
    python_arch = "aarch64" if arch == "arm64" else "x86_64"
    python_env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(BUILD / "python")}
    run("uv", "python", "install", PYTHON, env=python_env)
    runtime = BUILD / f"python/cpython-{PYTHON}-macos-{python_arch}-none"
    app = ROOT / "dist/TuneSaver.app"
    if app.exists():
        shutil.rmtree(app)
    resources = app / "Contents/Resources"
    executable_dir = app / "Contents/MacOS"
    executable_dir.mkdir(parents=True)
    resources.mkdir()
    shutil.copytree(runtime, resources / "python", symlinks=True)
    python = resources / "python/bin/python3.13"
    site = resources / "python/lib/python3.13/site-packages"
    run("uv", "pip", "install", "--python", python, "--target", site, "-r", ROOT / "desktop/requirements.lock")
    run("uv", "pip", "install", "--python", python, "--target", site, "--no-deps", SOURCES / "qobuz-dl")
    for metadata in site.glob("*/direct_url.json"):
        metadata.unlink()
    shutil.rmtree(site / ".bin", ignore_errors=True)
    for name in ("src", "web", "config"):
        shutil.copytree(ROOT / name, resources / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "export_playlist.py", resources / "export_playlist.py")
    shutil.copytree(BIN, resources / "bin")
    for name in ("ffmpeg", "ffprobe"):
        shutil.copy2(shutil.which(name), resources / "bin" / name)
        (resources / "bin" / name).chmod(0o755)
    bundle_libraries(resources)
    browser_env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(BUILD / "browsers")}
    run(python, "-m", "playwright", "install", "chromium", "--only-shell", env=browser_env)
    (resources / "browsers").mkdir()
    for folder in (BUILD / "browsers").iterdir():
        if folder.is_dir() and folder.name.startswith(("chromium_headless_shell-", "ffmpeg-")):
            shutil.copytree(folder, resources / "browsers" / folder.name, symlinks=True)
    licenses = resources / "Licenses"
    licenses.mkdir()
    for name in ARCHIVES:
        for candidate in (SOURCES / name).glob("*"):
            if candidate.is_file() and candidate.name.startswith(("LICENSE", "COPYING")):
                shutil.copy2(candidate, licenses / (name + "-" + candidate.name))
    (licenses / "sources.json").write_text(json.dumps(ARCHIVES, indent=2) + "\n")
    # Homebrew licenses + build receipts accompany copied native libraries.
    manifest = json.loads((resources / "native-libraries.json").read_text())
    cellars = {Path(v["original"]).parents[1] for v in manifest.values()}
    for cellar in cellars:
        if not (cellar / "INSTALL_RECEIPT.json").is_file():
            continue
        dest = licenses / f"{cellar.parent.name}-{cellar.name}"
        dest.mkdir(exist_ok=True)
        shutil.copy2(cellar / "INSTALL_RECEIPT.json", dest / "INSTALL_RECEIPT.json")
        for candidate in cellar.rglob("*"):
            if candidate.is_file() and candidate.name.upper().startswith(("LICENSE", "COPYING")):
                shutil.copy2(candidate, dest / candidate.name)
    minimum = "13.0"
    for file in (resources / "lib").iterdir():
        for value in re.findall(r"\bminos\s+(\d+\.\d+)", capture("otool", "-l", file)):
            if tuple(map(int, value.split('.'))) > tuple(map(int, minimum.split('.'))):
                minimum = value
    run("xcrun", "swiftc", "-O", "-target", f"{arch}-apple-macosx{minimum}", ROOT / "desktop/macos/main.swift", "-o", executable_dir / "TuneSaver", "-framework", "Cocoa", "-framework", "WebKit")
    icon(resources)
    info = {"CFBundleIdentifier": "com.tunesaver.desktop", "CFBundleName": "TuneSaver", "CFBundleDisplayName": "TuneSaver",
            "CFBundleExecutable": "TuneSaver", "CFBundlePackageType": "APPL", "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": "1", "CFBundleIconFile": "TuneSaver", "LSMinimumSystemVersion": minimum,
            "NSHighResolutionCapable": True, "NSPrincipalClass": "NSApplication",
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            "NSHumanReadableCopyright": "TuneSaver — composants tiers : voir Contents/Resources/Licenses"}
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    # Console scripts contain build-time shebangs; runtime uses python + explicit entry points.
    for item in (resources / "python/bin").iterdir():
        if not item.name.startswith("python"):
            item.unlink()
    for cache in list(resources.rglob("__pycache__")):
        shutil.rmtree(cache)
    run("xattr", "-cr", app)
    sign(app, "-")
    (ROOT / "dist/build-info.json").write_text(json.dumps({"version": VERSION, "architecture": arch, "minimum_macos": minimum, "notarized": False}, indent=2) + "\n")
    if not args.skip_archives:
        stem = f"TuneSaver-{VERSION}-macOS-{arch}"
        archive = ROOT / "dist" / (stem + ".zip")
        run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, archive)
        staging = BUILD / "dmg"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        (staging / "Applications").symlink_to("/Applications")
        shutil.copytree(app, staging / "TuneSaver.app", symlinks=True)
        run("hdiutil", "create", "-volname", "TuneSaver", "-srcfolder", staging, "-ov", "-format", "UDZO", ROOT / "dist" / (stem + ".dmg"))
        checksums = []
        for path in (ROOT / "dist").glob(stem + ".*"):
            checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        (ROOT / "dist/SHA256SUMS").write_text("\n".join(sorted(checksums)) + "\n")
    print(f"Ready: {app} ({arch}, macOS {minimum}+, local test signature)")


if __name__ == "__main__":
    main()
