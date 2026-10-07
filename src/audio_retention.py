"""Seven-day retention for owned web audio; queue metadata is kept permanently."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import shutil
import time
import uuid

import musicdl

RETENTION_SECONDS = 7 * 24 * 60 * 60


def finished_at(job):
    try:
        value = datetime.fromisoformat(job['last_finished_at'].replace('Z', '+00:00'))
        return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
    except (KeyError, ValueError, TypeError, AttributeError):
        return None


def job_directory(job):
    owner = str(uuid.UUID(job['owner_id']))
    identifier = job['id']
    if not musicdl.JOB_ID_RE.fullmatch(identifier):
        raise ValueError('Invalid job ID')
    root = musicdl.DELIVERY_ROOT.resolve()
    parent = root / owner
    directory = parent / identifier
    if parent.is_symlink() or directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise ValueError('Unsafe delivery directory')
    return directory


def history(job):
    display = job.get('display') or {}
    source = job['urls'][0]
    if job['provider'] == 'tiddl' and not source.startswith('https://'):
        source = f'https://tidal.com/{source}'
    return {'job_id': job['id'], 'provider': job['provider'], 'source_url': source,
        'title': display.get('title', ''), 'artist': display.get('artist', ''),
        'resource_kind': display.get('kind', 'track'), 'state': 'done',
        'track_count': job.get('track_count', 0), 'estimated_savings_cents': job.get('estimated_savings_cents', 0),
        'created_at': job.get('created_at', ''), 'completed_at': job.get('last_finished_at'),
        'delivered_at': job.get('delivered_at')}


def expire(api, now=None):
    now = time.time() if now is None else now
    removed = 0
    for provider in musicdl.PROVIDERS:
        for path in (musicdl.QUEUE_ROOT / 'done' / provider).glob('*.json'):
            try:
                with musicdl.provider_lock(provider, 'enqueue'):
                    job = musicdl.load_job(path, expected_provider=provider)
                    end = finished_at(job)
                    if (job.get('ephemeral') is not True or end is None or now - end <= RETENTION_SECONDS
                            or job.get('delivery_expired_at') and not job.get('retention_pending')):
                        continue
                    directory = job_directory(job)
                    if path.stem != job['id']:
                        continue
                    # Save source metadata before deleting any bytes, including after a crash.
                    api.local_upsert_history(job['owner_id'], [history(job)])
                    job['delivery_expired_at'] = job.get('delivery_expired_at') or musicdl.utc_now()
                    job['retention_pending'] = True
                    job.pop('delivery', None)
                    for key in ('download_ticket_hash', 'download_ticket_expires', 'stream_ticket_hash', 'stream_ticket_expires'):
                        job.pop(key, None)
                    musicdl.atomic_write_json(path, job)
                    if directory.exists():
                        shutil.rmtree(directory)
                    job.pop('retention_pending', None)
                    musicdl.atomic_write_json(path, job)
                    removed += 1
            except (OSError, ValueError, KeyError, TypeError):
                api.LOGGER.warning('Audio retention skipped invalid or inaccessible job %s', path.stem)
    return removed


def requeue(api, owner, identifier):
    if not musicdl.JOB_ID_RE.fullmatch(identifier):
        raise api.PublicError('Morceau introuvable.', status=404)
    for provider in musicdl.PROVIDERS:
        with musicdl.provider_lock(provider, 'enqueue'):
            for state in ('pending', 'running', 'done', 'failed'):
                path = musicdl.QUEUE_ROOT / state / provider / f'{identifier}.json'
                if not path.is_file():
                    continue
                job = musicdl.load_job(path, expected_provider=provider)
                if job.get('owner_id') != owner:
                    raise api.PublicError('Morceau introuvable.', status=404)
                if state in ('pending', 'running') or (state == 'done' and api.delivery_for_job(identifier, owner)):
                    return {'job_id': identifier, 'duplicate': True}
                if not api.worker_is_active(provider):
                    raise api.PublicError('La source de telechargement est arretee.', status=503)
                for url in job['urls']:
                    musicdl.validate_url(provider, url)
                for key in ('delivery', 'delivery_expired_at', 'retention_pending', 'last_finished_at', 'last_error',
                            'last_exit_code', 'download_ticket_hash', 'download_ticket_expires', 'delivered_at'):
                    job.pop(key, None)
                job['attempts'] = 0
                musicdl.atomic_write_json(path, job)
                pending = musicdl.QUEUE_ROOT / 'pending' / provider / path.name
                os.replace(path, pending)
                with api.local_db() as db:
                    db.execute("update download_history set state='pending', completed_at=null, delivered_at=null where job_id=? and user_id=?",
                               (identifier, owner))
                return {'job_id': identifier, 'duplicate': False}
    raise api.PublicError('Lien source introuvable.', status=404)


@asynccontextmanager
async def context(api):
    async def loop():
        while True:
            try:
                count = await asyncio.to_thread(expire, api)
                if count:
                    api.LOGGER.info('Expired audio for %s jobs; source links preserved', count)
            except Exception as exc:
                api.LOGGER.warning('Audio retention unavailable: %s', type(exc).__name__)
            await asyncio.sleep(3600)
    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
