"""Share transient platform credentials between GUI and terminal processes.

A shared flock keeps the session alive. A short lifecycle lock serializes first
initialization and last cleanup, including recovery after a crashed process.
"""
import fcntl
from pathlib import Path
import shutil


class LocalSession:
    def __init__(self, data: Path, initialize, *, terminal=False):
        self.data = data
        self.initialize = initialize
        self.terminal = terminal
        self.users = None
        self.lifecycle = None

    def __enter__(self):
        import musicdl
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lifecycle = (self.data / ".session.lifecycle.lock").open("a+b")
        fcntl.flock(self.lifecycle, fcntl.LOCK_EX)
        try:
            self.users = (self.data / ".session.users.lock").open("a+b")
            try:
                fcntl.flock(self.users, fcntl.LOCK_EX | fcntl.LOCK_NB)
                first = True
            except BlockingIOError:
                first = False
            if first:
                if self.terminal:
                    # Version 0.1 does not participate in the shared lease protocol.
                    with (self.data / ".desktop.lock").open("a+b") as legacy:
                        try:
                            fcntl.flock(legacy, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            raise RuntimeError("Ferme l’ancienne version de TuneSaver avant d’utiliser la commande terminal.") from None
                shutil.rmtree(musicdl.STATE_ROOT, ignore_errors=True)
                musicdl.SECRETS_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
                musicdl.ensure_layout()
                musicdl.sync_tiddl_config(musicdl.TIDDL_DOWNLOAD_DIR)
                self.initialize()
            fcntl.flock(self.users, fcntl.LOCK_SH)
            return self
        except BaseException:
            if self.users:
                self.users.close()
            self.lifecycle.close()
            self.users = self.lifecycle = None
            raise
        finally:
            if self.lifecycle:
                fcntl.flock(self.lifecycle, fcntl.LOCK_UN)

    def __exit__(self, *exc):
        import musicdl
        fcntl.flock(self.lifecycle, fcntl.LOCK_EX)
        try:
            fcntl.flock(self.users, fcntl.LOCK_UN)
            try:
                fcntl.flock(self.users, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            shutil.rmtree(musicdl.STATE_ROOT, ignore_errors=True)
        finally:
            self.users.close()
            self.lifecycle.close()
