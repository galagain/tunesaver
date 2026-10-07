"""Persistent website imports backed by the same metadata matcher as the CLI."""
import json
import re
from urllib.parse import parse_qs, urlparse

import catalog_matcher as matcher
import playlist_sources as sources


class ImportStopped(BaseException):
    pass


def identify(value):
    if isinstance(value, str) and value.lstrip().startswith('['):
        values = json.loads(value)
        if not isinstance(values, list) or not values or not all(isinstance(item, str) and not item.lstrip().startswith('[') for item in values):
            raise ValueError('Liste de liens invalide.')
        return 'group', json.dumps(list(dict.fromkeys(identify(item)[1] for item in values)))
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("Saisissez un lien ou Artiste - Titre.")
    value = value.strip()
    if not re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        if not re.fullmatch(r".+\s+[-\u2013\u2014\u00b7]\s+.+", value):
            raise ValueError("Pour un titre manuel, utilisez Artiste - Titre.")
        return "manual", value
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Utilisez un lien HTTPS public de musique.")
    try:
        provider, _, canonical = sources.playlist_source(value)
        return provider, canonical
    except sources.PlaylistError:
        pass
    hosts = {"open.spotify.com": "spotify", "music.apple.com": "applemusic",
             "soundcloud.com": "soundcloud", "www.soundcloud.com": "soundcloud",
             "tidal.com": "tiddl", "listen.tidal.com": "tiddl", "www.tidal.com": "tiddl",
             "beatport.com": "beatport", "www.beatport.com": "beatport",
             "qobuz.com": "qobuz", "www.qobuz.com": "qobuz", "play.qobuz.com": "qobuz", "open.qobuz.com": "qobuz"}
    provider = hosts.get(parsed.hostname)
    patterns = {"spotify": r"/(?:intl-[a-z]+/)?track/[A-Za-z0-9]{22}/?",
                "applemusic": r"/[a-z]{2}/album/(?:[^/]+/)?[0-9]+/?",
                "soundcloud": r"/[\w-]+/[\w-]+/?",
                "tiddl": r"/(?:browse/)?(?:track|album)/[0-9]+/?",
                "beatport": r"/(?:[a-z]{2}/)?(?:track|release)/(?:[^/]+/)?[0-9]+/?",
                "qobuz": r"/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:track|album)/(?:[^/]+/)?[A-Za-z0-9]+/?"}
    if not provider or not re.fullmatch(patterns[provider], parsed.path):
        raise ValueError("Lien de musique non pris en charge.")
    return provider, value


def read(value):
    provider, canonical = identify(value)
    if provider == 'group':
        tracks, seen = [], set()
        for source in json.loads(canonical):
            collection = read(source)
            for track in collection['tracks']:
                key = track.get('download_url') or track.get('source_url') or json.dumps([track.get('artist'), track.get('title'), track.get('duration')])
                if key not in seen:
                    seen.add(key)
                    tracks.append(track)
        if not tracks:
            raise ValueError('Aucun morceau dans ces liens.')
        return {'name': 'Playlist regroupée', 'kind': 'playlist', 'provider': 'group', 'source': canonical, 'total': len(tracks), 'tracks': tracks}
    payload = matcher.read_input(value, matcher.Catalog())
    tracks = payload['tracks']
    if not tracks or payload.get('total', len(tracks)) != len(tracks):
        raise ValueError("Liste vide ou incomplete : aucun telechargement lance.")
    return payload


def is_single_track(value):
    provider, canonical = identify(value)
    if provider == 'group':
        return False
    if provider == 'manual':
        return True
    try:
        sources.playlist_source(canonical)
        return False
    except sources.PlaylistError:
        pass
    parsed = urlparse(canonical)
    if provider == 'applemusic':
        return 'i' in parse_qs(parsed.query, keep_blank_values=True)
    return not re.search(r'/(album|release)/', parsed.path)


def add_single_track(api, owner, value):
    payload = read(value)
    if payload.get('kind') != 'track' or len(payload['tracks']) != 1:
        raise api.PublicError('Ce lien ne correspond pas a un morceau seul.')
    result = matcher.match_collection(payload, matcher.Catalog())['tracks'][0]
    candidate = result.get('match')
    if result['status'] != 'matched' or not candidate:
        raise api.PublicError('Aucune correspondance fiable pour ce morceau. Aucun telechargement lance.', status=422)
    target = matcher.direct_target(candidate)
    if not target:
        raise api.PublicError('Correspondance sans lien de telechargement.', status=422)
    provider = 'tiddl' if target['provider'] == 'tidal' else target['provider']
    if not api.worker_is_active(provider):
        raise api.PublicError('La source de telechargement est arretee.', status=503)
    job_id = api.enqueue_import_track({'user_id': owner, 'playlist_id': None},
        {**candidate, 'provider': provider, 'download_url': target['url']})
    return {'job_id': job_id, 'kind': 'track', 'playlist_id': None}


def export_result(record):
    from export_playlist import duration_seconds
    tracks = json.loads(record['tracks'])
    settings = tracks[0].get('_import', {}) if tracks else {}
    limit = settings.get('max_tracks', 0) if settings.get('kind') != 'track' else 0
    rows = []
    for number, track in enumerate(tracks[:limit or None], 1):
        state = track.get('matching', {})
        target = state.get('target') or matcher.direct_target(track)
        if target:
            target = {**target, 'method': state.get('method') or target.get('method', 'direct')}
        rows.append({'number': number, 'title': track['title'], 'artist': track.get('artist', ''),
            'duration_seconds': duration_seconds(track.get('duration')), 'url': track.get('source_url') or track.get('download_url') or '',
            'unavailable': bool(track.get('unavailable')), 'mix': track.get('mix', ''), 'album': track.get('album', ''),
            'target': target, 'requires_matching': target is None, 'matching': state})
    return {'schema_version': 1, 'collections': [{'schema_version': 1, 'name': settings.get('name', 'Import'),
        'source': identify(record['source_url'])[0], 'url': record['source_url'], 'kind': settings.get('kind', 'playlist'),
        'total': len(rows), 'source_total': len(tracks), 'max_tracks': limit, 'tracks': rows}]}


def process(api, record):
    tracks = json.loads(record['tracks'])
    settings = tracks[0].get('_import', {})
    limit = settings.get('max_tracks', 0)
    total = min(limit, len(tracks)) if limit and settings.get('kind') != 'track' else len(tracks)
    download = settings.get('download', True)
    failures = json.loads(record['failures'])
    queued = record['queued']
    completed = {i for i, t in enumerate(tracks[:total]) if t.get('matching', {}).get('final')}
    if not settings:
        completed.update(range(record['processed']))

    def active():
        with api.local_db() as db:
            row = db.execute("select state from playlist_imports where id = ?", (record['id'],)).fetchone()
        if not row or row['state'] != 'running':
            raise ImportStopped()

    def save():
        with api.local_db() as db:
            db.execute("update playlist_imports set tracks = ?, processed = ?, queued = ?, failures = ? where id = ? and state = 'running'",
                       (json.dumps(tracks), len(completed), queued, json.dumps(failures), record['id']))

    def enqueue(index, candidate):
        nonlocal queued
        if not download or tracks[index].get('matching', {}).get('job_id'):
            return
        target = matcher.direct_target(candidate)
        if not target:
            raise ValueError('Correspondance sans lien natif valide.')
        provider = 'tiddl' if target['provider'] == 'tidal' else target['provider']
        if not api.worker_is_active(provider):
            raise ValueError('La source de telechargement est arretee.')
        job = api.enqueue_import_track(record, {**candidate, 'provider': provider, 'download_url': target['url']})
        tracks[index]['matching']['job_id'] = job
        queued += 1

    def event(value):
        active()
        if value['type'] not in {'track_started', 'track_updated'}:
            return
        index = value['number'] - 1
        if index in completed:
            return
        track = tracks[index]
        if value['type'] == 'track_started':
            track['matching'] = {'status': 'searching', 'provider': value['provider'], 'final': False}
        else:
            track['matching'] = {k: v for k, v in value.items() if k not in {'type', 'number'}}
            if value.get('final'):
                completed.add(index)
                reason = ''
                if value['status'] == 'matched':
                    try:
                        enqueue(index, value['candidate'])
                    except (api.PublicError, ValueError) as exc:
                        reason = str(exc)
                else:
                    reason = value.get('error') or 'Aucune correspondance fiable sur les trois catalogues.'
                if reason:
                    track['matching']['error'] = reason
                    failures.append({'title': track['title'], 'artist': track.get('artist', ''), 'reason': reason})
        save()

    try:
        active()
        # Previously matched targets can be queued without any catalog requests.
        for index in sorted(completed):
            state = tracks[index].get('matching', {})
            if state.get('status') == 'matched' and state.get('candidate'):
                active()
                try:
                    enqueue(index, state['candidate'])
                except (api.PublicError, ValueError) as exc:
                    state['error'] = str(exc)
                    failures.append({'title': tracks[index]['title'], 'artist': tracks[index].get('artist', ''), 'reason': str(exc)})
                save()
        if len(completed) < total:
            source = {'name': settings.get('name', ''), 'kind': settings.get('kind', 'playlist'),
                      'source': record['source_url'], 'tracks': tracks}
            matcher.match_collection(source, matcher.Catalog(), on_event=event, max_tracks=total)
        active()
        with api.local_db() as db:
            state = 'matched' if not download else 'partial' if failures or record['error'] else 'done'
            db.execute("update playlist_imports set state = ? where id = ? and state = 'running'", (state, record['id']))
    except ImportStopped:
        return
