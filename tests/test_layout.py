"""Check separation of coursework, runtime relocation, and safe retry."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ecosystem/src'))
from learning_ecosystem.layout import restore_coursework
from learning_ecosystem.workspace import EcosystemError, atomic_json


class IndependentLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'learning_ecosystem'
        self.projects = self.root / 'projects'
        self.projects.mkdir(parents=True)
        self.destination = self.root.parent / 'osu_repository'
        self.destination.symlink_to(self.projects, target_is_directory=True)
        (self.projects / 'XTILO').mkdir()
        (self.projects / 'XTILO/current-work.py').write_text('current student work')
        (self.projects / '.git').mkdir()
        (self.projects / '.git/HEAD').write_text('ref: refs/heads/main\n')
        environment = self.projects / '.venv'
        (environment / 'bin').mkdir(parents=True)
        (environment / 'bin/python').symlink_to(Path(sys.executable).resolve())
        launcher = environment / 'bin/check'
        launcher.write_text('#!' + str(environment / 'bin/python') + '\nprint("coursework runtime works")\n')
        launcher.chmod(0o755)
        self.progress = self.root / 'ecosystem/data/studium/subjects/tilo/state.json'
        atomic_json(self.progress, dict(checkpoints=[dict(answer='existing answer')]))
        self.progress_before = self.progress.read_bytes()

    def test_projects_become_a_real_separate_repository_without_a_link_back(self):
        inode = self.projects.stat().st_ino
        result = restore_coursework(self.root)
        self.assertEqual(result['status'], 'complete')
        self.assertFalse(self.projects.exists())
        self.assertFalse(self.projects.is_symlink())
        self.assertFalse(self.destination.is_symlink())
        self.assertEqual(self.destination.stat().st_ino, inode)
        self.assertEqual((self.destination / 'XTILO/current-work.py').read_text(), 'current student work')
        self.assertEqual((self.destination / '.git/HEAD').read_text(), 'ref: refs/heads/main\n')
        self.assertEqual(self.progress.read_bytes(), self.progress_before)
        for name in ('studium', 'study-skills', 'docs'):
            self.assertFalse((self.destination / name).exists())
        launched = subprocess.run([str(self.destination / '.venv/bin/check')],
                                  capture_output=True, text=True, check=True)
        self.assertEqual(launched.stdout, 'coursework runtime works\n')
        self.assertEqual(restore_coursework(self.root)['status'], 'complete')

    def test_existing_real_destination_is_not_replaced(self):
        self.destination.unlink()
        self.destination.mkdir()
        (self.destination / 'other-work').write_text('keep')
        with self.assertRaisesRegex(EcosystemError, 'already a real directory'):
            restore_coursework(self.root)
        self.assertEqual((self.destination / 'other-work').read_text(), 'keep')
        self.assertTrue(self.projects.is_dir())

    def test_failed_move_restores_the_previous_destination_alias(self):
        with patch('learning_ecosystem.layout.os.rename', side_effect=OSError('Synthetic rename failure')):
            with self.assertRaisesRegex(OSError, 'rename failure'):
                restore_coursework(self.root)
        self.assertEqual(self.destination.resolve(), self.projects)
        self.assertTrue(self.projects.is_dir())

    def test_parent_write_rejection_keeps_current_work_available(self):
        original = Path.unlink
        def reject_parent(path, *args, **kwargs):
            if path == self.destination:
                raise PermissionError('Read-only parent')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', reject_parent):
            with self.assertRaisesRegex(PermissionError, 'Read-only'):
                restore_coursework(self.root)
        self.assertEqual((self.destination / 'XTILO/current-work.py').read_text(), 'current student work')
        self.assertEqual(self.progress.read_bytes(), self.progress_before)

    def test_interruption_after_directory_move_can_resume_runtime_repair(self):
        with patch('learning_ecosystem.layout.repair_runtime_paths', side_effect=OSError('Interrupted repair')):
            with self.assertRaisesRegex(OSError, 'Interrupted'):
                restore_coursework(self.root)
        self.assertFalse(self.destination.is_symlink())
        self.assertFalse(self.projects.exists())
        self.assertEqual(restore_coursework(self.root)['status'], 'complete')
        subprocess.run([str(self.destination / '.venv/bin/check')], capture_output=True, check=True)

    def test_unexpected_materials_are_not_deleted_as_obsolete_links(self):
        (self.projects / 'studium').mkdir()
        (self.projects / 'studium/new-work').write_text('preserve')
        with self.assertRaisesRegex(EcosystemError, 'obsolete repository entry'):
            restore_coursework(self.root)
        self.assertEqual((self.projects / 'studium/new-work').read_text(), 'preserve')
        self.assertTrue(self.destination.is_symlink())


if __name__ == '__main__':
    unittest.main()
