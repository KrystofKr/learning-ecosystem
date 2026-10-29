"""Exercise the production argument encoder without launching Windows or WSL."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


POWERSHELL = shutil.which('pwsh') or (
    shutil.which('powershell.exe') if os.name == 'nt' else None
)
LAUNCHER = Path(__file__).resolve().parents[1] / 'scripts/windows/launcher.ps1'
ENCODE_ARGUMENTS = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:LEARNING_TEST_LAUNCHER, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
$function = $ast.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'ConvertTo-WindowsArgument'
}, $true)
if ($null -eq $function) { throw 'Launcher argument encoder not found.' }
# Load only the encoder. The real launcher must never run during a test.
. ([ScriptBlock]::Create($function.Extent.Text))
$values = ConvertFrom-Json -InputObject $env:LEARNING_TEST_ARGUMENTS
$encoded = @($values | ForEach-Object { ConvertTo-WindowsArgument $_ })
ConvertTo-Json -InputObject $encoded -Compress
'''


@unittest.skipUnless(POWERSHELL, 'PowerShell is required to test the Windows launcher.')
class WindowsLauncherTests(unittest.TestCase):
    def encode(self, values):
        environment = dict(os.environ)
        environment['LEARNING_TEST_LAUNCHER'] = str(LAUNCHER)
        environment['LEARNING_TEST_ARGUMENTS'] = json.dumps(values)
        with tempfile.TemporaryDirectory(prefix='learning-launcher-test-') as temporary:
            for name in ('XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
                environment[name] = str(Path(temporary) / name.lower())
            result = subprocess.run(
                [POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                 base64.b64encode(ENCODE_ARGUMENTS.encode('utf-16le')).decode('ascii')],
                env=environment, capture_output=True, text=True, encoding='utf-8',
                timeout=20, check=True,
            )
        return json.loads(result.stdout.lstrip('\ufeff'))

    def test_wsl_options_and_identity_are_unquoted(self):
        root = '/home/test/learning_ecosystem/knowledge_pipeline'
        arguments = [
            '--distribution', 'Ubuntu', '--user', 'test', '--cd', root,
            '--exec', root + '/.venv/bin/python', '-B', root + '/scripts/run_scheduled.py',
        ]
        # WSL compares raw tokens, including quotes, before reaching --exec.
        # Quoting --distribution makes it run as a Linux command (exit 127).
        self.assertEqual(self.encode(arguments), arguments)

    def test_project_paths_with_spaces_keep_windows_argument_boundaries(self):
        root = '/home/test/study project/knowledge_pipeline'
        arguments = [
            '--distribution', 'Ubuntu', '--cd', root,
            '--exec', root + '/.venv/bin/python', '-B', root + '/scripts/run_scheduled.py',
        ]
        self.assertEqual(' '.join(self.encode(arguments)), subprocess.list2cmdline(arguments))

    def test_empty_values_and_quoted_paths_remain_escaped(self):
        self.assertEqual(
            self.encode(['', 'two words', 'a"b', 'C:\\some path\\']),
            ['""', '"two words"', '"a\\"b"', '"C:\\some path\\\\"'],
        )


if __name__ == '__main__':
    unittest.main()
