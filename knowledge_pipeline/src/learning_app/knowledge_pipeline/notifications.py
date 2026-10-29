"""Local Windows notification and durable local failure record; no mail/network."""

import base64
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .settings import load_settings
from .storage import atomic

OUTPUT = load_settings().incoming
POWERSHELL = Path('/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe')
APP_ID = r'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'

TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotifier, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotificationHistory, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.UI.Notifications.NotificationSetting, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast duration="long"><visual><binding template="ToastGeneric"><text></text><text></text></binding></visual><audio silent="true"/></toast>')
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).InnerText = $payload.title
$texts.Item(1).InnerText = $payload.message
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$toast.Tag = $payload.tag
$toast.Group = 'LearningApp'
$toast.ExpirationTime = [DateTimeOffset]::Now.AddDays(7)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($payload.app_id)
if ($null -ne $notifier.Setting -and $notifier.Setting.ToString() -ne 'Enabled') { throw ('Windows notifications: ' + $notifier.Setting.ToString()) }
$notifier.Show($toast)
Start-Sleep -Milliseconds 500
$found = @([Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory($payload.app_id) | Where-Object { $_.Tag -eq $payload.tag -and $_.Group -eq 'LearningApp' })
if ($found.Count -eq 0) { throw 'Windows did not retain the notification in notification history.' }
@{backend='windows_toast';os_accepted=$true;history_verified=$true;tag=$payload.tag} | ConvertTo-Json -Compress
"""


class LocalNotificationError(RuntimeError):
    pass


def interop_environments():
    """Cron lacks WSL_INTEROP; find an available socket without changing WSL."""
    paths = []
    current = os.environ.get('WSL_INTEROP')
    if current and Path(current).exists():
        paths.append(current)
    candidates = []
    for path in Path('/run/WSL').glob('*_interop'):
        try:
            candidates.append((path.stat().st_mtime, path))
        except FileNotFoundError:
            continue
    for _, path in sorted(candidates, reverse=True):
        if str(path) not in paths:
            paths.append(str(path))
    if not paths:
        raise LocalNotificationError('No running Windows/WSL interop session is available.')
    result = []
    for path in paths[:3]:
        environment = dict(os.environ)
        environment['WSL_INTEROP'] = path
        result.append(environment)
    return result


class LocalNotifier:
    def __init__(self, output=OUTPUT):
        self.output = output

    def validate(self):
        if not POWERSHELL.is_file():
            raise LocalNotificationError('Windows PowerShell is not available for local notifications.')
        if self.output.resolve() != self.output:
            raise LocalNotificationError('Notification output must not be a symlink.')
        interop_environments()

    def show(self, title, message, tag='JSFailure'):
        self.validate()
        payload = base64.b64encode(json.dumps({
            'title': title, 'message': str(message)[:1500], 'tag': tag, 'app_id': APP_ID,
        }, ensure_ascii=False).encode()).decode()
        command = TOAST_SCRIPT.replace('__PAYLOAD__', payload)
        encoded = base64.b64encode(command.encode('utf-16-le')).decode()
        errors = []
        for environment in interop_environments():
            try:
                result = subprocess.run([
                    str(POWERSHELL), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded,
                ], env=environment, capture_output=True, text=True, encoding='utf-8',
                   errors='replace', timeout=20, shell=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(type(exc).__name__)
                continue
            if result.returncode:
                # Retry only failed interop connections, not a real Windows API error.
                if 'WSL' in result.stderr or 'interop' in result.stderr.lower():
                    errors.append('WSL interop unavailable')
                    continue
                raise LocalNotificationError('Windows rejected the local notification: ' + result.stderr[-1200:])
            try:
                delivery = json.loads(result.stdout.strip().lstrip('\ufeff'))
            except ValueError as exc:
                raise LocalNotificationError('Could not verify local notification delivery.') from exc
            if delivery.get('os_accepted') is not True or delivery.get('history_verified') is not True:
                raise LocalNotificationError('Windows did not confirm notification delivery.')
            return delivery
        raise LocalNotificationError('Local notification could not reach Windows: ' + ', '.join(errors))

    def __call__(self, failure_text):
        if self.output.resolve() != self.output:
            raise LocalNotificationError('Notification output must not be a symlink.')
        # A persistent record survives popup dismissal or temporarily unavailable UI.
        if self.output.is_dir():
            record = self.output / 'ALERT.txt'
            if record.resolve()!=record:
                raise LocalNotificationError('The alert file must not be a symlink.')
            data=(
                'POSLEDNÍ CHYBA STAHOVÁNÍ PŘEPISŮ JUSTINA SUNGA\n'
                f'Čas UTC: {datetime.now(timezone.utc).isoformat()}\n\n'
                f'{failure_text}\n\nAktuální stav posledního běhu: status.json\n')
            atomic(record, data.encode('utf-8'))
        return self.show('Selhání stahování přepisů — Justin Sung',
                         str(failure_text)[:1000] + f'\nPodrobnosti: {self.output / "ALERT.txt"}')


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--test', action='store_true', required=True)
    parser.parse_args(argv)
    result = LocalNotifier().show('TEST — Přepisy Justina Sunga',
        'Test místního upozornění. Při nemožném stažení přepisu se zde zobrazí chyba. E-mail se nepoužívá.', 'JSTest')
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
