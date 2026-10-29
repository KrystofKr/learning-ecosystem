import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from learning_app.knowledge_pipeline import notifications as local
SUCCESS = {'backend': 'windows_toast', 'os_accepted': True, 'history_verified': True, 'tag': 'JSFailure'}


class LocalNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)

    def test_failure_is_recorded_locally_and_confirmed_by_windows(self):
        notifier = local.LocalNotifier(self.output)
        with patch.object(notifier, 'show', return_value=SUCCESS) as show:
            self.assertEqual(notifier('TEST: video unavailable'), SUCCESS)
        self.assertIn('TEST: video unavailable', (self.output / 'ALERT.txt').read_text())
        show.assert_called_once()
        self.assertIn('ALERT.txt', show.call_args.args[1])

    def test_record_survives_if_windows_notification_fails(self):
        notifier = local.LocalNotifier(self.output)
        with patch.object(notifier, 'show', side_effect=local.LocalNotificationError('Windows unavailable')):
            with self.assertRaises(local.LocalNotificationError):
                notifier('A transcript could not be downloaded.')
        self.assertIn('could not be downloaded', (self.output / 'ALERT.txt').read_text())

    def test_shell_is_disabled_and_message_is_passed_as_encoded_data(self):
        notifier = local.LocalNotifier(self.output)
        untrusted = 'Video title: $(Remove-Item *) <script> & "quotes"'
        result = subprocess.CompletedProcess([], 0, json.dumps(SUCCESS), '')
        with patch.object(notifier, 'validate'), patch.object(local, 'interop_environments', return_value=[{}]), \
                patch.object(local.subprocess, 'run', return_value=result) as run:
            self.assertEqual(notifier.show('Title', untrusted), SUCCESS)
        self.assertIs(run.call_args.kwargs['shell'], False)
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(local.POWERSHELL))
        script = base64.b64decode(command[-1]).decode('utf-16-le')
        self.assertNotIn(untrusted, script)
        self.assertIn('InnerText', script)
        self.assertIn('History.GetHistory', script)

    def test_successful_process_without_history_confirmation_is_not_delivery(self):
        notifier = local.LocalNotifier(self.output)
        result = subprocess.CompletedProcess([], 0, json.dumps({'os_accepted': True, 'history_verified': False}), '')
        with patch.object(notifier, 'validate'), patch.object(local, 'interop_environments', return_value=[{}]), \
                patch.object(local.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(local.LocalNotificationError, 'confirm'):
                notifier.show('Title', 'Message')

    def test_hung_notification_has_a_bounded_timeout(self):
        notifier = local.LocalNotifier(self.output)
        with patch.object(notifier, 'validate'), patch.object(local, 'interop_environments', return_value=[{}]), \
                patch.object(local.subprocess, 'run', side_effect=subprocess.TimeoutExpired('PowerShell', 20)) as run:
            with self.assertRaises(local.LocalNotificationError):
                notifier.show('Title', 'Message')
        self.assertEqual(run.call_args.kwargs['timeout'], 20)

    def test_record_cannot_write_through_a_symlink(self):
        actual = self.output / 'actual'
        actual.mkdir()
        link = self.output / 'link'
        link.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(local.LocalNotificationError):
            local.LocalNotifier(link)('Failure')
        self.assertEqual(list(actual.iterdir()), [])

    def test_alert_writer_does_not_follow_a_preplanted_temporary_symlink(self):
        outside=self.output/'outside.txt';outside.write_text('Keep this data.')
        (self.output/'.ALERT.txt.tmp').symlink_to(outside)
        notifier=local.LocalNotifier(self.output)
        with patch.object(notifier,'show',return_value=SUCCESS):notifier('Audit test.')
        self.assertEqual(outside.read_text(),'Keep this data.')
        self.assertIn('Audit test.',(self.output/'ALERT.txt').read_text())

    def test_alert_writer_rejects_a_symlink_destination(self):
        outside=self.output/'outside.txt';outside.write_text('Keep this data.')
        (self.output/'ALERT.txt').symlink_to(outside)
        notifier=local.LocalNotifier(self.output)
        with patch.object(notifier,'show') as show:
            with self.assertRaises(local.LocalNotificationError):notifier('Audit test.')
        show.assert_not_called()
        self.assertEqual(outside.read_text(),'Keep this data.')


if __name__ == '__main__':
    unittest.main()
