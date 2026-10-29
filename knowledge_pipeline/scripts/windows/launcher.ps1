param(
  [Parameter(Mandatory=$true)][string]$ProjectRoot,
  [string]$Distribution = 'Ubuntu',
  [string]$LinuxUser = ''
)

function ConvertTo-WindowsArgument([string]$Value) {
  if ([string]::IsNullOrEmpty($Value)) { return '""' }
  # WSL parses its own options before CommandLineToArgvW. Quoted switches
  # are treated as a Linux command, so leave simple arguments unquoted.
  if ($Value -notmatch '[\s"]') { return $Value }
  '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding
function Show-LocalStartupFailure([string]$message) {
$payload = [pscustomobject]@{title="Selhání kontroly přepisů — Justin Sung";message=$message;tag="JSLaunchFail";app_id='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'}

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
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
}

try {
  $info=New-Object System.Diagnostics.ProcessStartInfo
  $info.FileName='C:\WINDOWS\System32\wsl.exe'
  if (-not $ProjectRoot.StartsWith('/')) { throw 'ProjectRoot must be an absolute Linux path.' }
  $wslArguments = @('--distribution', $Distribution)
  if ($LinuxUser) { $wslArguments += @('--user', $LinuxUser) }
  $wslArguments += @('--cd', $ProjectRoot, '--exec', ($ProjectRoot + '/.venv/bin/python'), '-B', ($ProjectRoot + '/scripts/run_scheduled.py'))
  $info.Arguments = ($wslArguments | ForEach-Object { ConvertTo-WindowsArgument $_ }) -join ' '

  $info.WorkingDirectory='C:\WINDOWS\System32'
  $info.UseShellExecute=$false
  $info.CreateNoWindow=$true
  $process=[Diagnostics.Process]::Start($info)
  $process.WaitForExit()
  $code=$process.ExitCode
  if ($code -ne 0 -and $code -ne 10) {
    Show-LocalStartupFailure ("Kontrolu přepisů se nepodařilo dokončit. Kód: " + $code + ". Zkontroluj dostupnost WSL a data/justin_sung/incoming/ALERT.txt v datové složce projektu.")
  }
  exit $code
} catch {
  try { Show-LocalStartupFailure ("Kontrolu přepisů nelze spustit ve WSL: " + $_.Exception.Message) } catch {}
  exit 20
}
