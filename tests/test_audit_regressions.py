"""Failure cases found during the independent-layout and learning audit."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from test_ecosystem import Fixture, ROOT, definition
from learning_ecosystem import sync
from learning_ecosystem import setup
from learning_ecosystem.workspace import EcosystemError, atomic_json, digest, load_json
from learning_app.knowledge_pipeline.ecosystem import pending


class SnapshotAuditTests(Fixture):
    def test_unavailable_method_can_be_inspected_without_inventing_missing_passages(self):
        registry = load_json(self.workspace.registry)
        registry['techniques'].append(definition('missing-method', '6.5'))
        atomic_json(self.workspace.registry, registry)
        sync.synchronize(self.root)
        completed = subprocess.run([sys.executable, '-B', str(ROOT / 'ecosystem.py'),
                                    '--root', str(self.root), 'methods', '--technique', 'missing-method'],
                                   capture_output=True, text=True, check=True, timeout=15)
        value = json.loads(completed.stdout)
        self.assertFalse(value['technique']['available'])
        self.assertEqual(value['technique']['missing_sections'], ['6.5'])
        self.assertEqual(value['passages'], [])

    def test_returning_to_old_content_preserves_the_old_snapshot_files(self):
        first = self.workspace.current()
        directory = self.workspace.methods / 'versions' / first['version']
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        self.publish(self.text.replace('Use hypotheses.', 'Compare relationships.'))
        second = sync.synchronize(self.root)['version']
        self.publish(self.text)
        result = sync.synchronize(self.root)
        self.assertEqual(result['version'], first['version'])
        self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)
        self.assertEqual(self.workspace.current()['changes']['previous_version'], second)
        self.assertEqual(self.workspace.current()['changes']['changed_techniques'], ['priming'])

    def test_interrupted_unpublished_snapshot_can_be_rebuilt(self):
        raw = self.text.replace('Use hypotheses.', 'Updated method.').encode()
        self.publish(raw.decode())
        version = digest(dict(source=digest(raw), registry=digest(load_json(self.workspace.registry)),
                              compiler=digest(Path(sync.__file__).read_bytes())))
        draft = self.workspace.methods / 'versions' / version
        draft.mkdir()
        (draft / 'synthesis.txt').write_bytes(raw[:20])
        self.assertEqual(sync.synchronize(self.root)['status'], 'updated')
        self.assertEqual((draft / 'synthesis.txt').read_bytes(), raw)
        self.assertEqual(self.workspace.current()['version'], version)

    def test_pipeline_detects_damage_to_a_published_snapshot(self):
        (self.root / 'ecosystem.py').write_text('# synthetic integration marker\n')
        compiler = self.root / 'ecosystem/src/learning_ecosystem/sync.py'
        compiler.parent.mkdir(parents=True)
        shutil.copy2(sync.__file__, compiler)
        settings = SimpleNamespace(root=self.root / 'knowledge_pipeline', synthesis=self.workspace.synthesis)
        self.assertFalse(pending(settings))
        snapshot = self.workspace.methods / 'versions' / self.workspace.current()['version'] / 'synthesis.txt'
        snapshot.write_bytes(snapshot.read_bytes() + b'corrupt')
        self.assertTrue(pending(settings))

    def test_damaged_sealed_history_is_retained_for_diagnosis_instead_of_rebuilt(self):
        first = self.workspace.current()['version']
        snapshot = self.workspace.methods / 'versions' / first / 'synthesis.txt'
        self.publish(self.text.replace('Use hypotheses.', 'Updated method.'))
        second = sync.synchronize(self.root)['version']
        snapshot.write_bytes(b'damaged published history')
        self.publish(self.text)
        with self.assertRaises(EcosystemError):
            sync.synchronize(self.root)
        self.assertEqual(self.workspace.current()['version'], second)
        self.assertEqual(snapshot.read_bytes(), b'damaged published history')


class DoctorAuditTests(Fixture):
    def test_registration_and_runtime_are_checked_instead_of_trusting_stale_install_metadata(self):
        atomic_json(self.workspace.data / 'installation.json', dict(mode='project-local', global_registration='registered'))
        with patch.object(Path, 'home', return_value=self.root / 'isolated-home'):
            result = setup.doctor(self.root)
        self.assertEqual(result['global_registration'], 'pending-finalize')
        self.assertFalse(result['runtime_installed'])

    def test_empty_methodology_is_not_reported_as_ready(self):
        for name in ('study-coach', 'study-library', 'study-tutor'):
            path = self.workspace.skills / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text('Synthetic installed skill fixture')
        progress_skill = self.workspace.skills / 'study-progress/SKILL.md'
        progress_skill.write_text('Synthetic installed skill fixture')
        compiler = self.root / 'ecosystem/src/learning_ecosystem/sync.py'
        compiler.parent.mkdir(parents=True)
        shutil.copy2(sync.__file__, compiler)
        self.publish('4. PRIMING\n========\n\n9. RETRIEVAL\n========\n')
        sync.synchronize(self.root)
        site = self.root / 'synthetic-runtime-site'
        site.mkdir()
        (site / 'learning_ecosystem.pth').write_text('# fixture')
        with patch.object(setup.sys, 'prefix', str(self.root / 'knowledge_pipeline/.venv')), \
             patch.object(setup.sysconfig, 'get_paths', return_value={'purelib': str(site)}):
            result = setup.doctor(self.root)
        self.assertEqual(result['available_techniques'], 0)
        self.assertFalse(result['ready'])


class TrainingAuditTests(Fixture):
    def test_cli_training_resumes_from_another_working_directory_without_changing_subject_results(self):
        cwd = self.root / 'unrelated-repository'
        cwd.mkdir()
        before = (self.workspace.study / 'subjects/alpha/state.json').read_bytes()
        def command(*args):
            completed = subprocess.run([sys.executable, '-B', str(ROOT / 'ecosystem.py'),
                                       '--root', str(self.root), *args], cwd=cwd,
                                      env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                      capture_output=True, text=True, check=True, timeout=15)
            return json.loads(completed.stdout)
        self.assertEqual(len(command('subjects')), 2)
        lesson = command('lesson', '--subject', 'alpha', '--technique', 'priming',
                         '--context', 'A distinct actual task', '--domain', 'math')
        record = dict(attempt_id='cli-attempt', lesson_id=lesson['lesson_id'], answer='Synthetic learner explanation',
                      assessment='passed', criteria={key: True for key in lesson['rubric']},
                      assistance='none', solution_seen=False, occurred_at=datetime.now(timezone.utc).isoformat(),
                      review_notes='Checked the issued rubric', reviewer='assistant')
        input_path = cwd / 'attempt.json'
        atomic_json(input_path, record)
        result = command('record', '--subject', 'alpha', '--input', str(input_path))
        self.assertFalse(result['duplicate'])
        resumed = command('next', '--subject', 'alpha')
        self.assertEqual(resumed['stage'], 'guided')
        self.assertEqual((self.workspace.study / 'subjects/alpha/state.json').read_bytes(), before)
        self.assertFalse((cwd / 'studium').exists())

    def test_lesson_remains_consistent_when_a_publication_changes_during_issuance(self):
        original = self.workspace.current()
        replacement = dict(original, version='f' * 64, techniques=[])
        with patch.object(self.trainer.workspace, 'current', side_effect=[original, replacement]):
            lesson = self.trainer.lesson('alpha', context='real task', domain='math', technique='priming')
        self.assertEqual(lesson['method_version'], original['version'])
        self.assertEqual(lesson['technique_id'], 'priming')
        self.assertEqual(lesson['technique_revision'], original['techniques'][0]['revision'])

    def test_saved_failed_criterion_cannot_be_reported_as_a_passed_stage(self):
        self.attempt()
        path = self.trainer.path('alpha') / 'progress.json'
        saved = load_json(path)
        saved['attempts'][0]['criteria']['purpose'] = False
        atomic_json(path, saved)
        before = path.read_bytes()
        with self.assertRaises(EcosystemError):
            self.trainer.status('alpha')
        self.assertEqual(path.read_bytes(), before)

    def test_saved_answer_is_reported_as_recorded_even_if_current_methods_are_unavailable(self):
        lesson = self.trainer.lesson('alpha', context='specific task', domain='math', technique='priming')
        record = dict(attempt_id='actual-attempt', lesson_id=lesson['lesson_id'], answer='Own explanation',
                      assessment='passed', criteria={key: True for key in lesson['rubric']},
                      assistance='none', solution_seen=False, occurred_at=datetime.now(timezone.utc).isoformat(),
                      review_notes='Checked each issued criterion', reviewer='assistant')
        with patch.object(self.trainer.workspace, 'current', side_effect=EcosystemError('Unavailable methods')):
            result = self.trainer.record('alpha', record)
        self.assertFalse(result['duplicate'])
        self.assertIsNone(result['stale_lesson'])
        self.assertEqual(result['method_status'], 'unavailable')
        self.assertEqual(self.trainer.load('alpha')['attempts'][0]['answer'], record['answer'])
