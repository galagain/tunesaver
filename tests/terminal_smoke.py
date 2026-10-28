"""Exercise the bundled terminal runtime with synthetic audio, no real account.

The provider subprocess boundary is replaced; argument parsing, configuration,
session lifetime, subprocess streaming, output files and exit codes are real.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import tomllib
import wave


def fixture(args):
    sys.path.insert(0, str(args.resources / "src"))
    import desktop
    data = args.data_dir
    desktop.configure(data, args.resources)
    import musicdl
    import terminal

    class MemoryVault:
        def get(self, provider):
            return {"auth.json": json.dumps({"token": "fixture-token", "refresh_token": "fixture-refresh"})} if provider == "tiddl" else {}
        def set(self, provider, value):
            assert provider == "tiddl" and "fixture-refresh" in value["auth.json"]
        def delete(self, provider):
            raise AssertionError("Download must not disconnect account")

    def no_listener(*args, **kwargs):
        raise AssertionError("The terminal must not open a network listener")
    socket.socket.bind = no_listener
    desktop.KeychainVault = MemoryVault
    stream = musicdl.stream_command

    def provider(command, *, cwd, log_path, environment):
        assert command[1].endswith("desktop_tiddl.py"), command
        assert command[2:5] == ["download", "--raise-errors", "url"], command
        assert musicdl.validate_url("tiddl", command[5]) == musicdl.validate_url("tiddl", "https://tidal.com/track/123"), command
        assert environment["TUNESAVER_DESKTOP"] == "1"
        config = tomllib.loads((musicdl.STATE_ROOT / "tiddl/config.toml").read_text())
        output = Path(config["download"]["download_path"]) / "Essai été.wav"
        code = "import sys,wave; a=wave.open(sys.argv[1],'wb'); a.setparams((1,2,22050,0,'NONE','not compressed')); a.writeframes(b'\\0\\0'*22050); a.close()"
        return stream([sys.executable, "-B", "-c", code, str(output)], cwd=cwd, log_path=log_path, environment=environment)

    musicdl.stream_command = provider
    run = terminal.run
    def diagnose(*args, **kwargs):
        try:
            return run(*args, **kwargs)
        except Exception:
            import traceback
            traceback.print_exc()
            raise
    terminal.run = diagnose
    result = terminal.main(["--resources", str(args.resources), "--data-dir", str(data),
        "https://tidal.com/track/123", "-o", str(data / "Mon set été")])
    assert result == 0, result
    assert not (data / "session").exists(), "Last client must remove transient credentials"
    assert not (data / "library.sqlite").exists(), "CLI must not create a GUI library"
    with wave.open(str(data / "Mon set été/Essai été.wav")) as audio:
        assert audio.getnframes() == 22050
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    args.resources = args.resources.resolve()
    if args.fixture:
        return fixture(args)
    launcher = args.resources / "tunesaver"
    for option in ("--help", "--version"):
        result = subprocess.run([str(launcher), option], text=True, capture_output=True, check=True, cwd="/tmp")
        assert "TuneSaver" in result.stdout or "tunesaver" in result.stdout
    with tempfile.TemporaryDirectory(prefix="tunesaver-terminal-") as folder:
        subprocess.run([str(args.resources / "python/bin/python3.13"), "-B", str(Path(__file__).resolve()),
            "--resources", str(args.resources), "--fixture", "--data-dir", folder],
            check=True, cwd="/tmp", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    print("Terminal smoke passed: bundled launcher, headless download, audio, cleanup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
