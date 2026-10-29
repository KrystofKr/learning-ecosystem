"""Download new Show transcript panels and enrich the canonical synthesis.

Exact source files wait in incoming until the synthesis is updated, then
join the shared archive. The content editor also maintains update history.
Failures use the existing local Windows notification command.
"""

import base64
import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# The scheduled task must not discover or execute user-installed yt-dlp plugins.
os.environ['YTDLP_NO_PLUGINS'] = '1'
from yt_dlp import YoutubeDL


CHANNEL_URL = 'https://www.youtube.com/@JustinSung/videos'
CHANNEL_ID = 'UC2Zs9v2hL2qZZ7vsAENsg4w'
PANEL_URL = 'https://www.youtube.com/youtubei/v1/get_panel?prettyPrint=false'
PANEL_ID = 'PAmodern_transcript_view'
from .settings import load_settings
from . import subscription
from .access_reporting import record_access_allowed, report_access_block
from .storage import write_json as _write_json

SETTINGS = load_settings()
OUTPUT = SETTINGS.incoming
BASELINE = SETTINGS.baseline
ARCHIVE = SETTINGS.archive
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
USER_AGENT = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')


class DownloadError(RuntimeError):
    pass


def extract_json(page, pattern, required_key=None):
    for match in re.finditer(pattern, page):
        try:
            data = json.JSONDecoder().raw_decode(page[match.end():])[0]
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and (required_key is None or required_key in data):
            return data
    raise DownloadError('YouTube page did not provide the expected public data.')


def panel_rows(data, video_id):
    rows, referenced_ids = [], set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get('videoId'), str):
                referenced_ids.add(node['videoId'])
            if 'transcriptSegmentViewModel' in node:
                item = node['transcriptSegmentViewModel']
                if not isinstance(item.get('simpleText'), str) or not isinstance(item.get('timestamp'), str):
                    raise DownloadError('The Show transcript panel format has changed.')
                rows.append({'timestamp': item['timestamp'], 'text': item['simpleText']})
                return
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data.get('content', {}))
    if not rows or referenced_ids != {video_id}:
        raise DownloadError(f'No verified Show transcript content for https://www.youtube.com/watch?v={video_id}')
    timestamps = []
    for row in rows:
        if not re.fullmatch(r'\d+:\d{2}(?::\d{2})?', row['timestamp']):
            raise DownloadError('Unexpected timestamp format in Show transcript.')
        seconds = 0
        for value in row['timestamp'].split(':'):
            seconds = seconds * 60 + int(value)
        timestamps.append(seconds)
    if timestamps != sorted(timestamps):
        raise DownloadError('Show transcript returned rows in an unexpected order.')
    return rows


class Source:
    def __init__(self):
        self.session = requests.Session()
        # Avoid reading ~/.netrc and inheriting unrelated proxy/auth settings.
        self.session.trust_env = False
        retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=frozenset(['GET', 'POST']))
        self.session.mount('https://www.youtube.com/', HTTPAdapter(max_retries=retry))
        self.session.headers.update({'User-Agent': USER_AGENT, 'Accept-Language': 'en-US,en;q=0.9'})

    def list_videos(self):
        options = {'extract_flat': True, 'skip_download': True, 'quiet': True,
                   'no_warnings': True, 'socket_timeout': 30, 'retries': 2,
                   'extractor_retries': 2, 'cachedir': False,
                   'allowed_extractors': ['^youtube:tab$'], 'proxy': '',
                   'usenetrc': False, 'cookiefile': None, 'cookiesfrombrowser': None,
                   'postprocessors': [], 'progress_hooks': [], 'post_hooks': []}
        with YoutubeDL(options) as youtube:
            listing = youtube.extract_info(CHANNEL_URL, download=False)
        if not listing or listing.get('channel_id') != CHANNEL_ID:
            raise DownloadError('Could not verify the requested YouTube channel.')
        entries = listing.get('entries')
        if not isinstance(entries, list) or not entries:
            raise DownloadError('Could not obtain the Videos tab inventory.')
        videos = []
        for entry in entries:
            if not entry or not VIDEO_ID.fullmatch(entry.get('id', '')):
                raise DownloadError('The Videos tab inventory is incomplete.')
            videos.append({'id': entry['id'], 'title': entry.get('title', entry['id'])})
        return videos

    def get_panel(self, video):
        video_id = video['id']
        if not VIDEO_ID.fullmatch(video_id):
            raise DownloadError('Invalid video identifier.')
        url = f'https://www.youtube.com/watch?v={video_id}&hl=en'
        response = self.session.get(url, timeout=(15, 45), allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise DownloadError('YouTube did not return the requested public video page.')
        config = extract_json(response.text, r'ytcfg\.set\(\s*(?=\{)', 'INNERTUBE_CONTEXT')
        player = extract_json(response.text, r'var ytInitialPlayerResponse\s*=\s*')
        if player.get('videoDetails', {}).get('channelId') != CHANNEL_ID:
            raise DownloadError(f'Could not verify video ownership: {video_id}')
        params = base64.urlsafe_b64encode(b'\xaa\x09\x0f\x0a\x0b' + video_id.encode() + b'\x18\x02').decode().rstrip('=')
        result = self.session.post(PANEL_URL, json={
            'context': copy.deepcopy(config['INNERTUBE_CONTEXT']),
            'panelId': PANEL_ID, 'params': params,
        }, headers={
            'X-Youtube-Client-Name': str(config.get('INNERTUBE_CONTEXT_CLIENT_NAME', 1)),
            'X-Youtube-Client-Version': config['INNERTUBE_CLIENT_VERSION'],
            'Origin': 'https://www.youtube.com', 'Referer': url,
        }, timeout=(15, 45), allow_redirects=False)
        result.raise_for_status()
        if result.status_code != 200:
            raise DownloadError('YouTube did not return the requested Show transcript panel.')
        data = result.json()
        rows = panel_rows(data, video_id)
        micro = player.get('microformat', {}).get('playerMicroformatRenderer', {})
        published = micro.get('publishDate') or micro.get('uploadDate')
        published = published.split('T')[0] if isinstance(published, str) else None
        if published and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', published):
            raise DownloadError(f'Unexpected publication date: {video_id}')
        metadata = {
            'id': video_id, 'title': player.get('videoDetails', {}).get('title', video['title']),
            'url': f'https://www.youtube.com/watch?v={video_id}', 'published_date': published,
            'source': 'YouTube Show transcript', 'panel_id': PANEL_ID,
            'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
            'duration_seconds': int(player.get('videoDetails', {}).get('lengthSeconds') or 0) or None,
        }
        return data, rows, metadata


def write_json(path, value):
    assert_safe_output(path)
    _write_json(path, value)


def assert_safe_output(output):
    if not output.is_absolute() or output.resolve() != output:
        raise DownloadError('Download destination must be an absolute path without symlinks.')


def existing_ids(output, baseline, archived_ids=()):
    known = set(baseline['baseline_video_ids']) | set(archived_ids)
    for directory in output.iterdir():
        if directory.is_symlink():
            raise DownloadError('Unexpected symlink in the download destination.')
        if not directory.is_dir() or directory.name.startswith('.'):
            continue
        metadata_file = directory / 'metadata.json'
        text_file = directory / 'transcript.txt'
        panel_file = directory / 'panel.json'
        if not all(path.is_file() for path in [metadata_file, text_file, panel_file]):
            raise DownloadError(f'Incomplete previous download: {directory.name}')
        metadata = json.loads(metadata_file.read_text())
        if hashlib.sha256(text_file.read_bytes()).hexdigest() != metadata.get('text_sha256'):
            raise DownloadError(f'Previously downloaded transcript changed: {directory.name}')
        known.add(metadata['id'])
    return known


def download_new(source, output, baseline, on_download=None, archived_ids=(), before_download=None):
    assert_safe_output(output)
    known = existing_ids(output, baseline, archived_ids)
    downloaded = []
    for video in source.list_videos():
        video_id = video['id']
        if video_id in known:
            continue
        try:
            if before_download is not None:
                before_download()
            data, rows, metadata = source.get_panel(video)
            text = '\n\n'.join(f'{row["timestamp"]}\n{row["text"]}' for row in rows) + '\n'
            metadata['row_count'] = len(rows)
            metadata['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
            stamp = metadata['published_date'] or datetime.now(timezone.utc).date().isoformat()
            destination = output / f'{stamp}_{video_id}'
            if destination.exists():
                raise DownloadError(f'Output directory already exists: {destination.name}')
            with tempfile.TemporaryDirectory(dir=output, prefix='.pending-') as pending:
                temporary = Path(pending)
                (temporary / 'transcript.txt').write_text(text, encoding='utf-8')
                (temporary / 'panel.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                (temporary / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                temporary.rename(destination)
            known.add(video_id)
            downloaded.append(video_id)
        except subscription.CodexAccessBlocked:
            raise
        except Exception as exc:
            raise DownloadError(f'Stazeni prepisu selhalo: {video["title"]}\nhttps://www.youtube.com/watch?v={video_id}\n{type(exc).__name__}: {exc}') from exc
        if on_download is not None:
            on_download()
    return downloaded


def main(notify_failure, synthesis_updater=None, *, settings=None, access_checker=None):
    """The approved delivery adapter is passed explicitly by the entry point."""
    output = settings.incoming if settings is not None else OUTPUT
    baseline_file = settings.baseline if settings is not None else BASELINE
    archive = settings.archive if settings is not None else ARCHIVE
    checker = (access_checker if access_checker is not None else
               lambda: subscription.require_codex_subscription(
                   (settings if settings is not None else load_settings()).editor_config()))
    try:
        assert_safe_output(output)
        record_access_allowed(output, checker(), operation="run")
        with (output / '.download.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            if hasattr(notify_failure, 'validate'):
                notify_failure.validate()
            previous_status = output / 'status.json'
            if previous_status.is_file():
                previous = json.loads(previous_status.read_text())
                if previous.get('status') == 'failure' and not previous.get('notification_delivered'):
                    notify_failure('Upozornění z předchozího běhu nebylo doručeno:\n' + previous.get('error', 'Neznámá chyba'))
                    previous['notification_delivered'] = True
                    write_json(previous_status, previous)
            baseline = json.loads(baseline_file.read_text())
            if baseline['channel_id'] != CHANNEL_ID:
                raise DownloadError('Unexpected channel baseline.')
            if synthesis_updater is None:
                from .synthesis import update_pending
                synthesis_updater = lambda: update_pending(download_lock_held=True, settings=settings)
            enriched = []
            def enrich():
                enriched.extend(synthesis_updater())
            # Retry previously saved transcripts before making another source request.
            enrich()
            from .archive import archived_video_ids
            added = download_new(Source(), output, baseline, on_download=enrich,
                                 archived_ids=archived_video_ids(archive), before_download=checker)
            write_json(output / 'status.json', {
                'checked_at_utc': datetime.now(timezone.utc).isoformat(),
                'status': 'success', 'downloaded_video_ids': added,
                'synthesis_processed_video_ids': enriched,
            })
            print(f'OK: downloaded {len(added)} new Show transcript panels.', flush=True)
            return 0
    except subscription.CodexAccessBlocked as error:
        return report_access_block(output, error, notify_failure, operation="run")
    except Exception as exc:
        message = f'Stahovani prepisu nebo obohaceni syntezy Justin Sung selhalo.\n{exc}'
        status = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
                  'status': 'failure', 'error': str(exc), 'notification_delivered': False}
        try:
            delivery = notify_failure(message)
            status['notification_delivered'] = True
            status['notification_channel'] = 'local'
            if isinstance(delivery, dict):
                status['notification_delivery'] = delivery
        finally:
            if output.is_dir() and output.resolve() == output:
                write_json(output / 'status.json', status)
            print(message, flush=True)
        return 1


