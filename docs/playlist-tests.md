# Playlist import checks

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
