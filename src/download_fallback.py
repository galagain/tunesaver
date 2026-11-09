"""Retry recent, owned single-track failures on another matching catalog."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone

from catalog_matcher import Catalog, match_collection


def reason(api, job):
    provider = job['provider']
    log = api.download_engine.QUEUE_ROOT / 'logs' / provider / f"{job['id']}-attempt-{job.get('attempts', 1)}.log"
    if log.is_symlink() or not log.is_file():
        return ''
    with log.open('rb') as handle:
        handle.seek(max(0, log.stat().st_size - 65536))
        tail = handle.read(65536).decode('utf-8', errors='replace')
    if 'Requested quality is not available' in tail:
        return 'Qualite demandee indisponible sur la plateforme.'
    if 'Response body does not contain valid json' in tail:
        return 'Reponse invalide du service de telechargement.'
    return ''


def retry(api, path):
    m = api.download_engine
    job = m.load_job(path)
    owner, identifier = job.get('owner_id'), job['id']
    if not owner or not job.get('ephemeral') or len(job['urls']) != 1:
        return False
    if api.resource_kind(job['provider'], job['urls'][0]) != 'track':
        return False
    finished = job.get('last_finished_at', '')
    if not finished or job.get('fallback_checked_at') == finished:
        return False
    if datetime.fromisoformat(finished) < datetime.now(timezone.utc) - timedelta(days=1):
        return False
    cause = reason(api, job)
    if not cause:
        return False
    with api.local_db() as db:
        user = db.execute('select approved,blocked from users where id=?', (owner,)).fetchone()
    if not user or not user['approved'] or user['blocked']:
        return False
    provider = job['provider']
    tried = list(dict.fromkeys([*job.get('fallback_tried', []), provider]))
    excluded = [('tidal' if p == 'tiddl' else p) for p in m.PROVIDERS
                if p in tried or not api.worker_is_active(p)]
    catalog = Catalog()
    reference = job.get('fallback_reference')
    replacement = None
    try:
        if reference is None:
            reference = catalog.track('tidal' if provider == 'tiddl' else provider,
                                      job['urls'][0].rstrip('/').split('/')[-1].split('?')[0])
        # Never let a native URL bypass matching; retain the original recording
        # as reference across all attempts to prevent progressive version drift.
        source = {k: v for k, v in reference.items() if k not in ('provider', 'source_url', 'download_url')}
        result = match_collection({'name': source['title'], 'kind': 'track', 'tracks': [source]},
                                  catalog, excluded_providers=excluded)
        replacement = result['tracks'][0]['match']
    except Exception as exc:
        api.LOGGER.warning('Fallback lookup failed for %s: %s', identifier, type(exc).__name__)
    target = replacement['provider'] if replacement else provider
    if target == 'tidal':
        target = 'tiddl'
    with ExitStack() as stack:
        for p in sorted({provider, target}):
            stack.enter_context(m.provider_lock(p, 'enqueue'))
        if not path.exists() or m.load_job(path) != job:
            return False
        with api.local_db() as db:
            user = db.execute('select approved,blocked from users where id=?', (owner,)).fetchone()
            removed = db.execute('select 1 from library_removed where user_id=? and job_id=?', (owner, identifier)).fetchone()
            if not user or not user['approved'] or user['blocked'] or removed:
                return False
            job['fallback_checked_at'] = finished
            job['last_error'] = cause + ' Aucune correspondance de remplacement disponible.'
            if not replacement:
                m.atomic_write_json(path, job)
                return False
            url = m.validate_url(target, replacement.get('download_url') or replacement['source_url'])
            if not api.worker_is_active(target):
                return False
            pending = m.QUEUE_ROOT / 'pending' / target / path.name
            # Never enqueue an identifier already present on the target worker.
            if any((m.QUEUE_ROOT / state / target / path.name).exists() for state in m.STATES):
                return False
            job.setdefault('fallback_errors', []).append({'provider': provider, 'url': job['urls'][0], 'reason': cause})
            job.update(provider=target, urls=[url], fallback_tried=tried, fallback_reference=reference)
            for key in ('last_error', 'last_finished_at', 'last_exit_code', 'delivery', 'delivered_at'):
                job.pop(key, None)
            db.execute("update download_history set provider=?,source_url=?,state='pending',completed_at=null where job_id=? and user_id=?",
                       (target, url, identifier, owner))
            m.atomic_write_json(pending, job)
            path.unlink()
            return True


def scan(api):
    for provider in api.download_engine.PROVIDERS:
        for path in sorted((api.download_engine.QUEUE_ROOT / 'failed' / provider).glob('*.json'), reverse=True):
            try:
                retry(api, path)
            except (OSError, ValueError, KeyError, TypeError):
                api.LOGGER.warning('Fallback skipped invalid or removed job %s', path.stem)


async def context(api):
    async def loop():
        while True:
            try:
                await asyncio.to_thread(scan, api)
            except Exception as exc:
                api.LOGGER.warning('Download fallback unavailable: %s', type(exc).__name__)
            await asyncio.sleep(10)
    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
