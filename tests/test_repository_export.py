"""Public exports must never inherit private study files or their Git history."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('export_public', ROOT / 'ecosystem/scripts/export_public.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / 'private'
        self.destination = self.base / 'public'
        self.source.mkdir()
        self.initialize(self.source)
        self.write('README.md', 'Synthetic public project\n')
        self.write('.gitignore', '*.lock\n')
        self.write('ecosystem.py', '# Synthetic entry point\n')
        self.write('knowledge_pipeline/.gitignore', '# Synthetic ignore rules\n')
        checker = self.source / 'knowledge_pipeline/scripts/check_repository.py'
        checker.parent.mkdir(parents=True)
        shutil.copy2(ROOT / 'knowledge_pipeline/scripts/check_repository.py', checker)
        self.write('.github/workflows/tests.yml', 'name: Tests\n')
        self.write('.github/repository-profile.json', json.dumps({'schema_version': 1, 'profile': 'private'}))
        self.write('ecosystem/data/studium/subjects/alpha/state.json', '{"private": "learner evidence"}\n')
        self.write('ecosystem/data/studium/subjects/alpha/sources/notes.pdf', 'Synthetic private material\n')
        self.commit(self.source)

    def initialize(self, root):
        exporter.git(root, 'init', '--quiet', '--initial-branch=main')
        exporter.git(root, 'config', 'user.name', 'Synthetic test')
        exporter.git(root, 'config', 'user.email', 'test@example.invalid')

    def write(self, name, content):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit(self, root):
        exporter.git(root, 'add', '--all')
        exporter.git(root, 'commit', '--quiet', '--no-gpg-sign', '-m', 'Synthetic snapshot')

    def test_export_has_independent_history_and_excludes_private_files(self):
        result = exporter.export_public(self.source, self.destination)
        self.assertEqual(result['status'], 'staged')
        self.assertFalse((self.destination / 'ecosystem/data').exists())
        self.assertTrue((self.source / 'ecosystem/data/studium/subjects/alpha/state.json').is_file())
        self.assertEqual(json.loads((self.destination / '.github/repository-profile.json').read_text())['profile'],
                         'public')
        self.assertEqual(exporter.git(self.destination, 'rev-list', '--all'), b'')
        self.initialize(self.destination)
        self.commit(self.destination)
        self.assertEqual(len(exporter.git(self.destination, 'rev-list', '--all').splitlines()), 1)
        exporter.audit_public_history(self.destination)
        names = exporter.git(self.destination, 'ls-files').decode()
        self.assertNotIn('ecosystem/data', names)
        self.assertIn('/ecosystem/data/', (self.destination / '.gitignore').read_text())

    def test_existing_private_history_is_rejected_even_after_data_was_deleted(self):
        exporter.git(self.base, 'clone', '--quiet', '--no-hardlinks', str(self.source), str(self.destination))
        self.initialize(self.destination)
        exporter.git(self.destination, 'rm', '-r', '--quiet', 'ecosystem/data')
        (self.destination / '.github/repository-profile.json').write_text(
            json.dumps({'schema_version': 1, 'profile': 'public'}))
        self.commit(self.destination)
        with self.assertRaisesRegex(ValueError, 'history contains a non-public path'):
            exporter.export_public(self.source, self.destination)

    def test_credentials_in_public_code_stop_export_before_creating_destination(self):
        self.write('README.md', 'ghp_' + 'x' * 36)
        self.commit(self.source)
        with self.assertRaisesRegex(ValueError, 'credential-like content'):
            exporter.export_public(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_update_stages_code_changes_and_preserves_ignored_local_data(self):
        exporter.export_public(self.source, self.destination)
        self.initialize(self.destination)
        self.commit(self.destination)
        local = self.destination / 'ecosystem/data/local-note.txt'
        local.parent.mkdir(parents=True)
        local.write_text('Unsynced local file')
        self.write('README.md', 'Updated public project\n')
        self.commit(self.source)
        exporter.export_public(self.source, self.destination)
        self.assertEqual((self.destination / 'README.md').read_text(), 'Updated public project\n')
        self.assertEqual(local.read_text(), 'Unsynced local file')
        self.assertEqual(exporter.git(self.destination, 'diff', '--cached', '--name-only').decode().splitlines(),
                         ['README.md'])

    def test_dirty_destination_and_nested_destination_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'outside the private checkout'):
            exporter.export_public(self.source, self.source / 'public')
        exporter.export_public(self.source, self.destination)
        self.initialize(self.destination)
        self.commit(self.destination)
        (self.destination / 'README.md').write_text('Uncommitted user change')
        with self.assertRaisesRegex(ValueError, 'uncommitted files'):
            exporter.export_public(self.source, self.destination)
        self.assertEqual((self.destination / 'README.md').read_text(), 'Uncommitted user change')


if __name__ == '__main__':
    unittest.main()
