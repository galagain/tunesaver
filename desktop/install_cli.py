#!/usr/bin/env python3
"""Install a self-contained terminal runtime into the current macOS account."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import uuid

PAYLOAD = ("python", "src", "web", "config", "bin", "lib", "browsers", "Licenses",
           "export_playlist.py", "tunesaver", "install_cli.py", "native-libraries.json")


def default_bin():
    candidates = (Path.home() / "bin", Path.home() / ".local/bin")
    entries = {Path(p).expanduser() for p in os.environ.get("PATH", "").split(os.pathsep) if p}
    return next((candidate for candidate in candidates if candidate in entries), candidates[1])


def install(resources: Path, destination: Path, bin_dir: Path):
    resources = resources.expanduser().resolve()
    destination = destination.expanduser().resolve()
    bin_dir = bin_dir.expanduser().absolute()
    entry = bin_dir / "tunesaver"
    for name in PAYLOAD:
        if not (resources / name).exists():
            raise RuntimeError(f"Paquet CLI incomplet : {name}")
    if entry.exists() or entry.is_symlink():
        # Do not replace another program or a user-maintained shell wrapper.
        if not entry.is_symlink() or not Path(os.path.abspath(entry.parent / os.readlink(entry))).is_relative_to(destination):
            raise RuntimeError(f"{entry} existe déjà et n’appartient pas à cette installation.")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (destination / ".install.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        token = uuid.uuid4().hex
        staging = destination / (".install-" + token)
        staging.mkdir(mode=0o700)
        try:
            for name in PAYLOAD:
                source = resources / name
                if source.is_dir():
                    shutil.copytree(source, staging / name, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                else:
                    shutil.copy2(source, staging / name)
            (staging / "tunesaver").chmod(0o755)
            (staging / "installation.json").write_text(json.dumps({"application": "TuneSaver CLI", "source": str(resources)}, indent=2) + "\n")
            runtime = destination / ("runtime-" + token)
            staging.rename(runtime)
            current_link = destination / (".current-" + token)
            current_link.symlink_to(runtime.name, target_is_directory=True)
            os.replace(current_link, destination / "current")
            bin_dir.mkdir(parents=True, exist_ok=True)
            entry_link = bin_dir / (".tunesaver-" + token)
            entry_link.symlink_to(destination / "current/tunesaver")
            os.replace(entry_link, entry)
            # Older runtimes remain usable by commands that are already running.
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return entry


def main():
    parser = argparse.ArgumentParser(description="Installer la commande tunesaver pour cet utilisateur, sans sudo.")
    parser.add_argument("--resources", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--bin-dir", type=Path, default=default_bin())
    parser.add_argument("--destination", type=Path, default=Path.home() / "Library/Application Support/TuneSaver/CLI")
    args = parser.parse_args()
    try:
        entry = install(args.resources, args.destination, args.bin_dir)
    except (OSError, RuntimeError) as exc:
        print(f"Installation impossible : {exc}", file=sys.stderr)
        return 1
    print(f"Commande installée : {entry}\nLance tunesaver pour télécharger un son, ou tunesaver --help pour les commandes.")
    if str(entry.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print("Ajoute ce dossier à ton PATH dans ~/.zshrc :\nexport PATH=" + shlex.quote(str(entry.parent)) + ':"$PATH"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
