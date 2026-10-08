"""Lossless browser playback copies; downloads retain their original metadata."""
import os
from pathlib import Path
import shutil
import tempfile
import threading

_lock = threading.Lock()


def playback_file(path: Path) -> Path:
    if path.suffix.lower() != '.flac':
        return path
    with _lock:
        stat = path.stat()
        cached = path.with_name(f'.stream-{stat.st_mtime_ns}-{stat.st_size}.flac')
        if cached.is_file() and not cached.is_symlink():
            return cached
        with path.open('rb') as source:
            if source.read(4) != b'fLaC':
                return path
            blocks = []
            removed = False
            for _ in range(256):
                header = source.read(4)
                if len(header) != 4:
                    return path
                kind = header[0] & 127
                size = int.from_bytes(header[1:], 'big')
                if source.tell() + size > stat.st_size:
                    return path
                # STREAMINFO and SEEKTABLE are sufficient to decode and seek.
                if kind in (0, 3):
                    blocks.append((kind, source.read(size)))
                else:
                    source.seek(size, 1)
                    removed = True
                if header[0] & 128:
                    break
            else:
                return path
            if not removed or not blocks or blocks[0][0] != 0:
                return path
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.stream-', delete=False) as target:
                    temporary = Path(target.name)
                    target.write(b'fLaC')
                    for index, (kind, data) in enumerate(blocks):
                        target.write(bytes([kind | (128 if index == len(blocks) - 1 else 0)]))
                        target.write(len(data).to_bytes(3, 'big'))
                        target.write(data)
                    shutil.copyfileobj(source, target, 1024 * 1024)
                os.replace(temporary, cached)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
        return cached
