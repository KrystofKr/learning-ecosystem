from access_fixtures import permit_codex_access
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from learning_app.knowledge_pipeline import downloader as downloader


class FakeSource:
    def __init__(self, entries, failure=None):
        self.entries = entries
        self.calls = []
        self.failure = failure

    def list_videos(self):
        return self.entries

    def get_panel(self, video):
        self.calls.append(video['id'])
        if self.failure:
            raise self.failure
        rows = [{'timestamp': '0:00', 'text': 'Original text & punctuation.'},
                {'timestamp': '0:07', 'text': 'Second panel row.'}]
        raw = {'content': {'watch': {'videoId': video['id']}, 'rows': [
            {'transcriptSegmentViewModel': {'timestamp': item['timestamp'], 'simpleText': item['text']}}
            for item in rows]}}
        metadata = {'id': video['id'], 'title': video['title'], 'published_date': '2026-10-24'}
        return raw, rows, metadata


class DownloadTests(unittest.TestCase):
    def setUp(self):
        permit_codex_access(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'incoming'
        self.output.mkdir()
        self.old = {'id': 'oldVideo001', 'title': 'Already present'}
        self.new = {'id': 'newVideo001', 'title': 'New video'}
        self.baseline = {'channel_id': downloader.CHANNEL_ID, 'baseline_video_ids': [self.old['id']]}
        self.archive=Path(self.temp.name)/'archive';self.archive.mkdir()
        (self.archive/'index.csv').write_text('id\n')
        archive_patch=patch.object(downloader,'ARCHIVE',self.archive)
        archive_patch.start();self.addCleanup(archive_patch.stop)

    def run_main(self, notify):
        with patch('learning_app.knowledge_pipeline.archive.archived_video_ids',return_value=set()):
            return downloader.main(notify, synthesis_updater=lambda: [])

    def test_existing_videos_are_skipped_and_only_new_panel_is_saved(self):
        source = FakeSource([self.old, self.new])
        result = downloader.download_new(source, self.output, self.baseline)
        self.assertEqual(result, [self.new['id']])
        self.assertEqual(source.calls, [self.new['id']])
        folder = self.output / '2026-10-24_newVideo001'
        self.assertEqual((folder / 'transcript.txt').read_text(),
                         '0:00\nOriginal text & punctuation.\n\n0:07\nSecond panel row.\n')
        self.assertEqual({p.name for p in folder.iterdir()}, {'transcript.txt', 'panel.json', 'metadata.json'})
        self.assertEqual(list(self.output.glob('.pending-*')), [])

    def test_rerun_does_not_download_or_modify_existing_files(self):
        source = FakeSource([self.new])
        downloader.download_new(source, self.output, self.baseline)
        before = {str(p): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        source.calls.clear()
        self.assertEqual(downloader.download_new(source, self.output, self.baseline), [])
        self.assertEqual(source.calls, [])
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.output.rglob('*') if p.is_file()})

    def test_archived_new_video_is_not_downloaded_again_after_incoming_folder_is_removed(self):
        source=FakeSource([self.new])
        self.assertEqual(downloader.download_new(source,self.output,self.baseline,archived_ids={self.new['id']}),[])
        self.assertEqual(source.calls,[])
        self.assertEqual(list(self.output.iterdir()),[])

    def test_status_writer_does_not_follow_a_preplanted_temporary_symlink(self):
        outside=Path(self.temp.name)/'outside.txt';outside.write_text('Keep this data.')
        (self.output/'.status.json.tmp').symlink_to(outside)
        downloader.write_json(self.output/'status.json',{'status':'success'})
        self.assertEqual(outside.read_text(),'Keep this data.')
        self.assertEqual(json.loads((self.output/'status.json').read_text()),{'status':'success'})

    def test_status_writer_rejects_a_symlink_destination(self):
        outside=Path(self.temp.name)/'outside.txt';outside.write_text('Keep this data.')
        (self.output/'status.json').symlink_to(outside)
        with self.assertRaises(downloader.DownloadError):
            downloader.write_json(self.output/'status.json',{'status':'success'})
        self.assertEqual(outside.read_text(),'Keep this data.')

    def test_unavailable_transcript_stops_before_later_videos(self):
        source = FakeSource([self.new, {'id': 'nextVideo01', 'title': 'Later'}], failure=RuntimeError('Unavailable'))
        with self.assertRaisesRegex(downloader.DownloadError, 'newVideo001'):
            downloader.download_new(source, self.output, self.baseline)
        self.assertEqual(source.calls, [self.new['id']])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_failure_must_call_notification_and_report_error(self):
        baseline = Path(self.temp.name) / 'baseline.json'
        baseline.write_text(json.dumps(self.baseline))
        source = FakeSource([self.new], failure=RuntimeError('Unavailable'))
        alerts = []
        with patch.object(downloader, 'OUTPUT', self.output), patch.object(downloader, 'BASELINE', baseline), \
                patch.object(downloader, 'Source', return_value=source):
            self.assertEqual(self.run_main(alerts.append), 1)
        self.assertEqual(len(alerts), 1)
        self.assertIn('newVideo001', alerts[0])
        status = json.loads((self.output / 'status.json').read_text())
        self.assertEqual(status['status'], 'failure')
        self.assertTrue(status['notification_delivered'])

    def test_missing_output_directory_still_calls_notification(self):
        alerts = []
        with patch.object(downloader, 'OUTPUT', self.output / 'missing'):
            self.assertEqual(self.run_main(alerts.append), 1)
        self.assertEqual(len(alerts), 1)

    def test_failed_notification_is_not_reported_as_delivered(self):
        alerts = []
        def fail(message):
            alerts.append(message)
            raise RuntimeError('Delivery unavailable')
        with patch.object(downloader, 'OUTPUT', self.output), \
                patch.object(downloader, 'BASELINE', Path(self.temp.name) / 'missing.json'):
            with self.assertRaisesRegex(RuntimeError, 'Delivery unavailable'):
                self.run_main(fail)
        self.assertEqual(len(alerts), 1)
        self.assertFalse(json.loads((self.output / 'status.json').read_text())['notification_delivered'])

    def test_modified_download_is_reported_instead_of_overwritten(self):
        source = FakeSource([self.new])
        downloader.download_new(source, self.output, self.baseline)
        text = self.output / '2026-10-24_newVideo001/transcript.txt'
        text.write_text('User edited this transcript.')
        with self.assertRaisesRegex(downloader.DownloadError, 'changed'):
            downloader.download_new(source, self.output, self.baseline)
        self.assertEqual(text.read_text(), 'User edited this transcript.')

    def test_changed_panel_or_wrong_video_is_rejected(self):
        with self.assertRaises(downloader.DownloadError):
            downloader.panel_rows({'content': {'captions': ['unexpected format']}}, self.new['id'])
        source = FakeSource([self.new])
        raw, rows, metadata = source.get_panel(self.new)
        wrong = copy.deepcopy(raw)
        wrong['content']['watch']['videoId'] = 'different01'
        with self.assertRaises(downloader.DownloadError):
            downloader.panel_rows(wrong, self.new['id'])
        self.assertEqual(downloader.panel_rows(raw, self.new['id']), rows)

    def test_output_symlink_is_rejected_without_writing_through_it(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        link = Path(self.temp.name) / 'linked-output'
        link.symlink_to(outside, target_is_directory=True)
        alerts = []
        with patch.object(downloader, 'OUTPUT', link):
            self.assertEqual(self.run_main(alerts.append), 1)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_local_notification_is_validated_before_any_source_request(self):
        from unittest.mock import Mock
        notifier = Mock()
        notifier.validate.side_effect = RuntimeError('Local notification unavailable')
        with patch.object(downloader, 'OUTPUT', self.output), patch.object(downloader, 'Source') as source:
            self.assertEqual(self.run_main(notifier), 1)
        source.assert_not_called()
        notifier.assert_called_once()

    def test_undelivered_failure_is_retried_before_the_next_check(self):
        baseline = Path(self.temp.name) / 'baseline.json'
        baseline.write_text(json.dumps(self.baseline))
        (self.output / 'status.json').write_text(json.dumps({'status': 'failure',
            'notification_delivered': False, 'error': 'Previous download unavailable'}))
        alerts = []
        source = FakeSource([self.old])
        with patch.object(downloader, 'OUTPUT', self.output), patch.object(downloader, 'BASELINE', baseline), \
                patch.object(downloader, 'Source', return_value=source):
            self.assertEqual(self.run_main(alerts.append), 0)
        self.assertEqual(len(alerts), 1)
        self.assertIn('Previous download unavailable', alerts[0])


if __name__ == '__main__':
    unittest.main()
