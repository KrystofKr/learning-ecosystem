"""Run only an outstanding Monday 09:00 Prague transcript check."""

import contextlib
import fcntl
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import downloader
from .notifications import LocalNotifier
from .synthesis import has_pending
from . import subscription
from .access_reporting import record_access_allowed, report_access_block

OUTPUT = downloader.OUTPUT
PRAGUE = ZoneInfo('Europe/Prague')
REPORTED_FAILURE = 10
UNREPORTED_FAILURE = 20


def weekly_boundary(now):
    if now.tzinfo is None:
        raise ValueError('A timezone-aware clock is required.')
    local = now.astimezone(PRAGUE)
    boundary = local.replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=local.weekday())
    if local < boundary:
        boundary -= timedelta(days=7)
    return boundary


def parse_time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('A saved completion timestamp has no timezone.')
    return result


def last_success(output):
    values = []
    saved = output / 'schedule_state.json'
    if saved.is_file():
        value = json.loads(saved.read_text()).get('last_success_at_utc')
        if value:
            values.append(parse_time(value))
    status_file = output / 'status.json'
    if status_file.is_file():
        status = json.loads(status_file.read_text())
        if status.get('status') == 'success':
            values.append(parse_time(status['checked_at_utc']))
    return max(values) if values else None


def is_due(completed, now):
    return completed is None or completed < weekly_boundary(now)


def main(output=OUTPUT, runner=None, notifier=None, now=None, pending_checker=None,
         access_checker=None):
    now = now or datetime.now(timezone.utc)
    runner = runner or downloader.main
    notifier = notifier or LocalNotifier(output)
    pending_checker = pending_checker or has_pending
    checker = access_checker if access_checker is not None else subscription.require_codex_subscription
    try:
        downloader.assert_safe_output(output)
        record_access_allowed(output, checker(), operation="scheduled")
        with (output / '.schedule.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            with (output / 'run.log').open('a', encoding='utf-8') as log:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    print(f'[{now.isoformat()}] Scheduled/login check.', flush=True)
                    status_file = output / 'status.json'
                    previous = json.loads(status_file.read_text()) if status_file.is_file() else {}
                    retry = previous.get('status') in {'failure', 'blocked'} or pending_checker()
                    if not retry and not is_due(last_success(output), now):
                        print('SKIP: the current weekly period was already checked successfully.', flush=True)
                        return 0
                    print('DUE: completing the outstanding weekly transcript check.', flush=True)
                    before = status_file.read_bytes() if status_file.exists() else None
                    result = runner(notifier)
                    if result:
                        status = json.loads(status_file.read_text()) if status_file.is_file() else {}
                        return REPORTED_FAILURE if status.get('notification_delivered') else UNREPORTED_FAILURE
                    after = status_file.read_bytes() if status_file.exists() else None
                    if before == after:
                        print('SKIP: another downloader owns the active run; completion was not recorded.', flush=True)
                        return 0
                    status = json.loads(after)
                    if status.get('status') != 'success':
                        raise RuntimeError('The downloader did not confirm a successful check.')
                    downloader.write_json(output / 'schedule_state.json', {
                        'last_success_at_utc': status['checked_at_utc'],
                        'covered_weekly_boundary': weekly_boundary(now).isoformat(),
                    })
                    return 0
    except subscription.CodexAccessBlocked as error:
        return report_access_block(output, error, notifier, operation="scheduled")
    except Exception as exc:
        status = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
                  'status': 'failure', 'error': str(exc), 'notification_delivered': False}
        try:
            notifier('Týdenní kontrolu přepisů se nepodařilo spustit:\n' + str(exc))
            status['notification_delivered'] = True
            return REPORTED_FAILURE
        except Exception:
            return UNREPORTED_FAILURE
        finally:
            if output.is_dir() and output.resolve() == output:
                downloader.write_json(output / 'status.json', status)


