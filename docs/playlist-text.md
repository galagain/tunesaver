# Playlist text files

The playlist export menu writes UTF-8 JSON to a `.txt` file. Each ZIP download
also includes `playlist.txt` in this format:

```json
{
  "format": "tunesaver-playlist",
  "version": 1,
  "name": "My playlist",
  "tracks": [
    {"title": "Song", "artist": "Artist", "duration_seconds": 185, "url": "https://tidal.com/track/123"}
  ]
}
```

Track order follows the saved playlist order. Unknown durations are `null`, not
guessed. Valid links remain exportable after file expiry or download failure.
Legacy API consumers still receive the links-only JSON response unless they
request `?format=tunesaver`.

Dropping this file into the signed-in website restores its name and metadata,
then automatically queues its tracks. A plain TXT file containing one HTTPS
music URL per line uses normal imports instead; individual tracks go to the
library without creating a collection. Files must be UTF-8 and at most 1 MiB.
Files are read in the browser, validated on the server, and never executed.

New website imports send `auto_download: true`; the API retains the explicit
preview-only mode for existing clients. Auto-download intent is persisted in
the import metadata before source reading, so restarts do not lose it.
An owned, completed job with its audio still present is reused, including when
its provider worker is offline. Missing audio is queued again.
