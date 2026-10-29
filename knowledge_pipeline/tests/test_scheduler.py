from access_fixtures import permit_codex_access
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from learning_app.knowledge_pipeline import scheduler as schedule


def moment(value):
    return datetime.fromisoformat(value)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        permit_codex_access(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)
        checker = patch.object(schedule, 'has_pending', return_value=False)
        checker.start()
        self.addCleanup(checker.stop)

    def save_success(self, when):
        schedule.downloader.write_json(self.output / 'status.json',
            {'status': 'success', 'checked_at_utc': when.isoformat(), 'downloaded_video_ids': []})

    def test_login_before_monday_deadline_does_not_run_early(self):
        now = moment('2026-10-26T08:59:59+01:00')
        self.assertFalse(schedule.is_due(moment('2026-10-24T15:24:55+00:00'), now))

    def test_monday_nine_is_due(self):
        self.assertTrue(schedule.is_due(moment('2026-10-24T15:24:55+00:00'), moment('2026-10-26T09:00:00+01:00')))

    def test_login_after_missed_monday_runs_once(self):
        self.save_success(moment('2026-10-24T15:24:55+00:00'))
        now = moment('2026-10-27T18:00:00+01:00')
        calls = []
        def run(notify):
            calls.append('download')
            self.save_success(now)
            return 0
        self.assertEqual(schedule.main(self.output, run, lambda _: None, now), 0)
        self.assertEqual(schedule.main(self.output, run, lambda _: None, now), 0)
        self.assertEqual(calls, ['download'])
        self.assertEqual(json.loads((self.output / 'schedule_state.json').read_text())['covered_weekly_boundary'],
                         '2026-10-26T09:00:00+01:00')

    def test_multiple_missed_weeks_collapse_to_one_download(self):
        self.save_success(moment('2026-09-01T10:00:00+02:00'))
        now = moment('2026-10-27T18:00:00+01:00')
        calls = []
        def run(notify):
            calls.append(1); self.save_success(now); return 0
        schedule.main(self.output, run, lambda _: None, now)
        schedule.main(self.output, run, lambda _: None, now)
        self.assertEqual(calls, [1])

    def test_successful_week_skips_all_source_and_notification_calls(self):
        self.save_success(moment('2026-10-26T09:02:00+01:00'))
        def forbidden(*args):
            self.fail('Completed period must not perform a download or send an alert.')
        self.assertEqual(schedule.main(self.output, forbidden, forbidden, moment('2026-10-27T10:00:00+01:00')), 0)

    def test_pending_synthesis_is_retried_even_in_completed_week(self):
        now=moment('2026-10-27T10:00:00+01:00')
        self.save_success(moment('2026-10-26T09:02:00+01:00'))
        calls=[]
        def run(notify):
            calls.append('retry')
            self.save_success(now)
            return 0
        self.assertEqual(schedule.main(self.output,run,lambda _:None,now,pending_checker=lambda:True),0)
        self.assertEqual(calls,['retry'])

    def test_failure_is_retried_despite_earlier_success_in_same_week(self):
        now=moment('2026-10-27T10:00:00+01:00')
        schedule.downloader.write_json(self.output/'schedule_state.json',{'last_success_at_utc':'2026-10-26T08:02:00+00:00'})
        schedule.downloader.write_json(self.output/'status.json',{'status':'failure','notification_delivered':True})
        calls=[]
        def run(notify):
            calls.append('retry')
            self.save_success(now)
            return 0
        self.assertEqual(schedule.main(self.output,run,lambda _:None,now),0)
        self.assertEqual(calls,['retry'])

    def test_pending_check_error_is_recorded_as_failure(self):
        self.save_success(moment('2026-10-26T09:02:00+01:00'))
        alerts=[]
        def failed_check():raise RuntimeError('Main synthesis changed')
        result=schedule.main(self.output,lambda _:self.fail('Must stop before downloading'),alerts.append,
                             moment('2026-10-27T10:00:00+01:00'),pending_checker=failed_check)
        self.assertEqual(result,schedule.REPORTED_FAILURE)
        self.assertEqual(len(alerts),1)
        status=json.loads((self.output/'status.json').read_text())
        self.assertEqual(status['status'],'failure')
        self.assertIn('Main synthesis changed',status['error'])

    def test_failure_does_not_mark_period_complete(self):
        now = moment('2026-10-27T18:00:00+01:00')
        def run(notify):
            schedule.downloader.write_json(self.output / 'status.json', {'status': 'failure', 'notification_delivered': True})
            return 1
        self.assertEqual(schedule.main(self.output, run, lambda _: None, now), schedule.REPORTED_FAILURE)
        self.assertFalse((self.output / 'schedule_state.json').exists())
        self.assertTrue(schedule.is_due(schedule.last_success(self.output), now))

    def test_busy_downloader_does_not_mark_completion(self):
        self.save_success(moment('2026-10-24T15:24:55+00:00'))
        self.assertEqual(schedule.main(self.output, lambda _: 0, lambda _: None, moment('2026-10-27T18:00:00+01:00')), 0)
        self.assertFalse((self.output / 'schedule_state.json').exists())

    def test_dst_change_keeps_monday_nine_prague_time(self):
        self.assertEqual(schedule.weekly_boundary(moment('2026-10-20T12:00:00+02:00')).isoformat(),
                         '2026-10-19T09:00:00+02:00')
        self.assertEqual(schedule.weekly_boundary(moment('2026-10-27T12:00:00+01:00')).isoformat(),
                         '2026-10-26T09:00:00+01:00')
        self.assertEqual(schedule.weekly_boundary(moment('2027-03-30T12:00:00+02:00')).isoformat(),
                         '2027-03-29T09:00:00+02:00')

    def test_missing_history_is_due(self):
        self.assertTrue(schedule.is_due(schedule.last_success(self.output), moment('2026-10-27T12:00:00+01:00')))

    def test_unreported_failure_has_distinct_exit_code(self):
        def run(notify):
            schedule.downloader.write_json(self.output / 'status.json', {'status': 'failure', 'notification_delivered': False})
            return 1
        self.assertEqual(schedule.main(self.output, run, lambda _: None, moment('2026-10-27T12:00:00+01:00')),
                         schedule.UNREPORTED_FAILURE)

    def test_no_timezone_or_invalid_state_is_reported_locally(self):
        (self.output / 'schedule_state.json').write_text('invalid JSON')
        alerts = []
        self.assertEqual(schedule.main(self.output, lambda _: 0, alerts.append,
                                       moment('2026-10-27T12:00:00+01:00')), schedule.REPORTED_FAILURE)
        self.assertEqual(len(alerts), 1)
        with self.assertRaises(ValueError):
            schedule.weekly_boundary(datetime(2026, 10, 27, 12))


if __name__ == '__main__':
    unittest.main()
