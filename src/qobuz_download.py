"""Qobuz runner with bounded, atomic retries for audio and cover transfers."""
import http.client
import os
from pathlib import Path
import runpy
import tempfile
import time
import requests

ATTEMPTS = 3


def download(url, fname, desc):
    target = Path(fname)
    for attempt in range(ATTEMPTS):
        temporary = None
        try:
            with requests.get(url, allow_redirects=True, stream=True, timeout=(10, 60)) as response:
                response.raise_for_status()
                expected = int(response.headers.get('content-length', 0))
                received = 0
                fd, temporary = tempfile.mkstemp(prefix='.tunesaver-transfer-', dir=target.parent)
                with os.fdopen(fd, 'wb') as output:
                    for chunk in response.iter_content(chunk_size=65536):
                        if chunk:
                            output.write(chunk)
                            received += len(chunk)
                if not received or (expected and expected != received):
                    raise requests.exceptions.ChunkedEncodingError('Incomplete download')
                os.replace(temporary, target)
                temporary = None
                return
        except requests.exceptions.RequestException as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            transient = status is None or status in (408, 429) or status >= 500
            if not transient or attempt + 1 == ATTEMPTS:
                # Third-party callers print exception strings: never include a signed URL.
                raise RuntimeError('Qobuz transfer failed after bounded retries' if transient else f'Qobuz HTTP {status}') from None
            print(f'[tunesaver] Qobuz : transfert interrompu, reprise {attempt + 1}/{ATTEMPTS - 1}', flush=True)
            time.sleep(2 ** (attempt + 1))
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


def main():
    # The CDN can exceed Python's default 100 headers. Keep a finite ceiling;
    # this change is scoped to the isolated Qobuz subprocess.
    http.client._MAXHEADERS = 512
    import qobuz_dl.downloader
    qobuz_dl.downloader.tqdm_download = download
    runpy.run_module('qobuz_dl.cli', run_name='__main__')


if __name__ == '__main__':
    main()
