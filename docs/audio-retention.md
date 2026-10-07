# Audio retention

The web process checks completed, user-owned web downloads at startup and hourly.
Audio expires seven days after `last_finished_at` (server download completion),
not after the user saves the file to their device. The hourly check means expiry
can take up to one extra hour. Invalid or missing completion dates are skipped.

The cleanup saves source metadata to download history before removing the owned
delivery directory, including packaged archives and cover sidecars. Queue JSON,
playlist membership, ordering, title, artist and source URL remain. Legacy CLI
downloads outside owned delivery directories are not cleaned by this service.
The cleanup rejects symlinked or unsafe owner/job directories and records a
retry marker before deleting files, so interrupted cleanup can resume.

Archived library rows expose `expired: true`, `streamable: false`. The UI offers
`POST /api/library/{job_id}/redownload`; the original job ID and provider URL are
reused, keeping the track in its playlists. Only the owner can requeue it. An
active download is not duplicated. A new completed download starts a new week.
Deleting a row explicitly still removes both its files and library reference.

Tags are no longer displayed or editable in the interface. Existing tag tables
and legacy API endpoints remain compatible; no destructive schema migration is
required for this UI change.

Tests: `python -m unittest discover -s tests` and
`node tests/retention-browser.cjs` (Playwright, mocked accounts and downloads).
