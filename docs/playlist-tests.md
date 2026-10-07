# Playlist import checks

## Complete input matrix (19 cases)

From the project directory:

```sh
python3 run_playlist_tests.py --docker
```

This runs Spotify `57HAIqxdHbi332994xcOKc`, Apple Music Jazz House
`pl.2e69eda9c8af461e8924dfebbcdf9794` (the supplied link target, not its
different display label), and SoundCloud `delicieuse-musique/sets/jazz-house`.
Spotify uses HTTP only. No audio is downloaded and no user playlist is created.

The native playlist cases are:

- Beatport chart: https://www.beatport.com/fr/chart/best-of-classics-techno/820196
- TIDAL: https://tidal.com/playlist/0bfac8e5-4ff8-44e4-b112-5fb71d39bcc1
- Qobuz: https://www.qobuz.com/fr-fr/playlists/jazz-n-electro/1270781

The Beatport chart is treated like a playlist. Every native track must retain
its direct target, with `requires_matching: false`.

The same run also reads these albums, using the same ordered track-list format:

- Spotify: https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa
- Apple Music: https://music.apple.com/us/album/random-access-memories/617154241
- SoundCloud: https://soundcloud.com/daftpunkofficialmusic/sets/random-access-memories-10th
- TIDAL: https://tidal.com/album/20115556
- Beatport release: https://www.beatport.com/release/random-access-memories/1091742
- Qobuz: https://www.qobuz.com/fr-fr/album/random-access-memories-daft-punk/0886443927087

`TRACKS` in `run_playlist_tests.py` also lists one verified direct song URL for
each of these six services. A manual `Artist - Title` entry completes the matrix:
6 playlists/charts, 6 albums/releases, 6 tracks, 1 manual entry. Beatport calls albums
and EPs releases (`/release/`); these are read as ordered album track lists.

The first two are Random Access Memories; the SoundCloud case is its 10th
Anniversary Edition. On 2026-10-07 they returned 13, 13 and 22 tracks respectively.
Album exports use `spotify-album`, `applemusic-album` and `soundcloud-album`
filenames, so they cannot overwrite the playlist exports. Each report records
the collection kind. SoundCloud albums use the existing sets reader.

Each execution creates a unique directory under `data/playlist-tests/`, containing
TXT and JSON exports for successful reads and a `report.json` with counts,
timings and failures. `inputs.json` groups all successful collections using the
same versioned schema. A failed case is listed in the report, never silently
included as a partial collection. All 19 cases are attempted even if one fails.
Exit code is 0 only when all cases succeed, otherwise 1 (130 on interruption).
Use `--output-dir PATH` for a different parent directory, or omit `--docker`
when running inside the configured Python environment. Counts are checked
against source metadata, not historical hard-coded totals.

Every track has the same fields, including `target` and `requires_matching`.
Native TIDAL/Beatport/Qobuz entries have a validated direct track target and
`requires_matching: false`. Other sources have `target: null` and
`requires_matching: true`. Tests check this contract, order and completeness.
The Python matcher skips both album and track searches for native entries.
No matching or audio downloading occurs in this metadata test runner.

Imports have two independent phases:

1. `POST /api/playlist-imports` creates a playlist and reads its source metadata.
   The worker changes `pending` to `ready` and stops. No matching or download runs.
2. `GET /api/playlist-imports/{id}` returns all source entries in their original
   order, including duplicates. `POST /api/playlist-imports/{id}/start` explicitly
   starts matching and enqueuing. Both endpoints enforce account ownership.

The ready state and source entries survive restarts. Existing `running` imports
continue. The three-concurrent-reading-jobs safeguard remains; it is not a limit
on playlist size. Network timeouts remain to avoid abandoned processes.

## Offline regression tests

From an environment with the application dependencies installed:

```sh
python -m unittest discover -s tests
```

The fixtures cover more than 500 entries, order, duplicates, incomplete responses,
cross-account access, idempotent confirmation and absence of downloads before
confirmation. Browser checks are in `tests/playlist-preview-browser.cjs`.

## Live metadata tests

```sh
python tests/probe_playlists.py
python tests/probe_playlists.py --source spotify
python tests/probe_playlists.py --url 'https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc'
```

`tests/playlist_cases.json` contains a public example for all six sources.
Run in the application's runtime environment, with Chromium and provider
configuration available. The runner prohibits matching, enqueuing and database
access. It prints one JSON report per source and exits nonzero on any failure.
It does not create user playlists or audio files. Authentication helpers may
refresh expired provider sessions.

Public playlists can change, disappear or become private. Counts are checked
against the source response when available, never a hard-coded historical count.
A failure is reported as such, not reclassified as a successful partial import.
Spotify's anonymous preview boundary at 100 selects the browser reader; it is
not a maximum playlist length. Beatport follows pagination until the last page.

## Experimental Spotify HTTP-only probe

```sh
python tests/probe_spotify_http.py
python tests/probe_spotify_http.py 'https://open.spotify.com/playlist/57HAIqxdHbi332994xcOKc'
```

This probe starts no browser and requires no personal Spotify credentials. It
reads the anonymous session supplied in the public embed page, discovers the
current pagination query in the public player bundle, and requests all pages
directly. It reuses only `PlaylistPages` validation, not its browser runner.
It checks totals, positions and the first entries against the public preview.
Tokens are kept in memory and never printed. HTTP errors stop the probe without
automatic retries, including rate-limit responses.

On 2026-10-07 the supplied playlist returned 166/166 titles in 2.72 seconds,
using five HTTP requests and two content pages (100 + 66). This is an observed
test result, not a speed guarantee. This undocumented protocol remains
experimental and is not yet used by production imports.
