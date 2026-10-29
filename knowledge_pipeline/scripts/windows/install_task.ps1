param(
  [Parameter(Mandatory=$true)][string]$ProjectRoot,
  [string]$Distribution = 'Ubuntu',
  [string]$LinuxUser = '',
  [string]$TaskName = 'LearningApp-JustinSung-Transcripts',
  [switch]$Replace
)

$ErrorActionPreference = 'Stop'
if (-not $ProjectRoot.StartsWith('/')) { throw 'ProjectRoot must be an absolute Linux path.' }
if ((Get-TimeZone).Id -ne 'Central Europe Standard Time') {
  throw 'The Monday 09:00 Prague schedule requires the Windows Prague time zone (Central Europe Standard Time).'
}
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and -not $Replace) { throw 'Task exists. Use -Replace to update its definition explicitly.' }
$settings = @{ProjectRoot=$ProjectRoot; Distribution=$Distribution; LinuxUser=$LinuxUser}
$payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($settings | ConvertTo-Json -Compress)))
$source = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'launcher.ps1') -Raw -Encoding UTF8
$script = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($source))
$bootstrap = @"
`$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$payload')) | ConvertFrom-Json
`$s = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$script'))
& ([ScriptBlock]::Create(`$s)) -ProjectRoot `$p.ProjectRoot -Distribution `$p.Distribution -LinuxUser `$p.LinuxUser
"@
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $powershell -Argument ("-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + $encoded)
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -WeeksInterval 1 -At ([DateTime]::Today.AddHours(9))
$weekly.StartBoundary = [DateTime]::Today.AddHours(9).ToString('s')
$logon = New-ScheduledTaskTrigger -AtLogOn -User $sid
$logon.Delay = 'PT1M'
$principal = New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
$options = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$definition = New-ScheduledTask -Action $action -Trigger @($weekly,$logon) -Principal $principal -Settings $options -Description 'Learning App: collect new Show transcript panels, enrich the study synthesis, archive processed sources, and show local failure alerts.'
Register-ScheduledTask -TaskName $TaskName -InputObject $definition -Force:$Replace | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
