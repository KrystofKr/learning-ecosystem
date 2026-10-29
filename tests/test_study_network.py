import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SCRIPTS = BASE / 'skills/study-coach/scripts'
sys.path.insert(0, str(SCRIPTS))
from coaching_store import CoachingStore, StudyStore, StudyError
from method_library import MethodLibrary, MethodError


def note(record_id='reflection-1', kind='reflection', payload=None, subject='alpha'):
    return dict(subject_id=subject, record_id=record_id, kind=kind,
                checkpoint_ids=[], method_refs=[],
                payload=payload or dict(what='Own attempt', how='Without notes',
                                       why='Cause is not yet known', next_step='Try another example'))


def experiment(record_id='experiment-1'):
    return note(record_id, 'experiment', dict(change='Explain one relationship',
                expected_effect='Better independent explanation', measure='New comparable problem',
                comparison='Prior attempt with known assistance', status='active'))


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.study = StudyStore(self.root)
        self.coach = CoachingStore(self.root)
        for subject in ('alpha', 'beta'):
            self.study.init(subject, subject, ['Explain and apply a principle'])
            self.study.topic_add(subject, 'topic', 'Topic', ['Apply a principle'], [])

    def checkpoint(self, subject='alpha', checkpoint_id='attempt-1', **changes):
        record = dict(subject_id=subject, checkpoint_id=checkpoint_id, lesson_id='lesson-1',
                      topic_id='topic', activity_kind='diagnostic', activity='Explain a synthetic principle',
                      learner_answer='An actual synthetic answer', evaluation='Meets the stated criterion',
                      result='passed', hints_used=0, difficulty='introductory', misconceptions=[],
                      source_refs=[], next_step='Apply to a new problem')
        record.update(changes)
        self.study.checkpoint(subject, record)
        return record

    def context(self, checkpoint_id='attempt-1', record_id='context-1', **changes):
        payload = dict(checkpoint_id=checkpoint_id, task_mode='apply', assistance='unknown', solution_seen=None)
        payload.update(changes)
        return note(record_id, 'attempt-context', payload)

    def test_coaching_does_not_change_learning_results(self):
        self.checkpoint()
        path = self.root / 'subjects/alpha/state.json'
        before = path.read_bytes()
        status = self.study.status('alpha')
        self.coach.put('alpha', note())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.study.status('alpha'), status)
        self.assertEqual(self.coach.view('beta')['records'], [])

    def test_unknown_subject_is_not_created(self):
        with self.assertRaises(StudyError):
            self.coach.put('unknown', note(subject='unknown'))
        self.assertFalse((self.root / 'subjects/unknown').exists())

    def test_idempotent_retry_and_conflicting_payload(self):
        self.assertFalse(self.coach.put('alpha', note())['duplicate'])
        path = self.root / 'subjects/alpha/coaching.json'
        before = path.read_bytes()
        self.assertTrue(self.coach.put('alpha', note())['duplicate'])
        changed = note()
        changed['payload']['next_step'] = 'Different content'
        with self.assertRaises(StudyError):
            self.coach.put('alpha', changed)
        self.assertEqual(path.read_bytes(), before)

    def test_append_only_revision_keeps_old_plan(self):
        first = experiment()
        self.coach.put('alpha', first)
        second = copy.deepcopy(first)
        second.update(record_id='experiment-2', replaces='experiment-1')
        second['payload'].update(status='completed', result='Too few comparable attempts to conclude')
        self.coach.put('alpha', second)
        self.assertEqual(self.coach.view('alpha')['total'], 1)
        self.assertEqual(self.coach.view('alpha', history=True)['total'], 2)
        third = copy.deepcopy(second)
        third.update(record_id='experiment-3', replaces='experiment-1')
        with self.assertRaises(StudyError):
            self.coach.put('alpha', third)

    def test_only_one_active_experiment(self):
        self.coach.put('alpha', experiment())
        with self.assertRaises(StudyError):
            self.coach.put('alpha', experiment('experiment-2'))

    def test_unknown_and_cross_subject_evidence_is_rejected(self):
        self.checkpoint(subject='beta', checkpoint_id='beta-only')
        record = note()
        record['checkpoint_ids'] = ['beta-only']
        with self.assertRaises(StudyError):
            self.coach.put('alpha', record)
        record['checkpoint_ids'] = ['unknown']
        with self.assertRaises(StudyError):
            self.coach.put('alpha', record)

    def test_context_survives_correction_without_new_attempt(self):
        original = self.checkpoint()
        self.coach.put('alpha', self.context())
        correction = dict(original, checkpoint_id='correction-1', correction_of='attempt-1',
                          result='unassessed', evaluation='Criterion was not verified')
        self.study.checkpoint('alpha', correction)
        self.assertEqual(self.study.status('alpha')['activity_count'], 1)
        self.assertEqual(self.coach.view('alpha')['records'][0]['payload']['checkpoint_id'], 'attempt-1')
        with self.assertRaises(StudyError):
            self.coach.put('alpha', self.context('correction-1', 'context-2'))

    def test_unknown_assistance_is_not_converted_to_none(self):
        self.checkpoint()
        self.coach.put('alpha', self.context())
        payload = self.coach.view('alpha')['records'][0]['payload']
        self.assertEqual(payload['assistance'], 'unknown')
        self.assertIsNone(payload['solution_seen'])
        self.assertNotIn('occurred_at', payload)

    def test_seen_solution_or_hints_prevent_unassisted_label(self):
        self.checkpoint(hints_used=1)
        with self.assertRaises(StudyError):
            self.coach.put('alpha', self.context(assistance='none', solution_seen=False))
        self.checkpoint(checkpoint_id='attempt-2')
        with self.assertRaises(StudyError):
            self.coach.put('alpha', self.context('attempt-2', assistance='none', solution_seen=True))

    def test_context_revision_cannot_change_activity(self):
        self.checkpoint()
        self.checkpoint(checkpoint_id='attempt-2')
        self.coach.put('alpha', self.context())
        revised = self.context('attempt-2', record_id='context-2')
        revised['replaces'] = 'context-1'
        with self.assertRaises(StudyError):
            self.coach.put('alpha', revised)

    def test_duplicate_context_requires_revision(self):
        self.checkpoint()
        self.coach.put('alpha', self.context())
        with self.assertRaises(StudyError):
            self.coach.put('alpha', self.context(record_id='context-2'))

    def test_confidence_requires_pre_feedback_timing_and_real_timestamp(self):
        self.checkpoint()
        for changes in (dict(confidence=0.8), dict(confidence=True, confidence_timing='before-feedback'),
                        dict(confidence=1.1, confidence_timing='before-feedback'),
                        dict(occurred_at='2026-10-27T12:00:00')):
            with self.subTest(changes=changes), self.assertRaises(StudyError):
                self.coach.put('alpha', self.context(**changes))
        self.coach.put('alpha', self.context(confidence=0.8, confidence_timing='before-feedback',
                                           occurred_at='2026-10-27T12:00:00+01:00'))

    def test_capacity_and_reserve_are_enforced_without_changing_capacity(self):
        payload = dict(period_start='2026-10-26', capacity_minutes=60, reserve_minutes=10,
                       blocks=[dict(topic_id='topic', output='Explain without notes', min_minutes=30,
                                    max_minutes=55, estimate_basis='User estimate')])
        record = note('plan-1', 'plan', payload)
        with self.assertRaises(StudyError):
            self.coach.put('alpha', record)
        record['payload']['blocks'][0]['max_minutes'] = 50
        self.coach.put('alpha', record)

    def test_atomic_write_failure_preserves_previous_notes(self):
        self.coach.put('alpha', note())
        path = self.root / 'subjects/alpha/coaching.json'
        before = path.read_bytes()
        with patch('coaching_store.atomic_json', side_effect=OSError('Synthetic disk failure')):
            with self.assertRaises(OSError):
                self.coach.put('alpha', note('reflection-2'))
        self.assertEqual(path.read_bytes(), before)

    def test_parallel_active_experiments_do_not_both_commit(self):
        processes = []
        for number in (1, 2):
            path = self.root / f'input-{number}.json'
            path.write_text(json.dumps(experiment(f'experiment-{number}')))
            processes.append(subprocess.Popen([sys.executable, '-B', str(SCRIPTS/'coaching_store.py'),
                             '--root', str(self.root), '--subject', 'alpha', 'put', '--input', str(path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        codes = []
        for process in processes:
            process.communicate(timeout=15)
            codes.append(process.returncode)
        self.assertEqual(sorted(codes), [0, 1])
        self.assertEqual(self.coach.view('alpha')['total'], 1)

    def test_invalid_schema_is_not_replaced(self):
        path = self.root / 'subjects/alpha/coaching.json'
        path.write_text(json.dumps(dict(schema_version=99, subject_id='alpha', records=[])))
        before = path.read_bytes()
        with self.assertRaises(StudyError):
            self.coach.put('alpha', note())
        self.assertEqual(path.read_bytes(), before)


class MethodTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'synthesis.txt'
        self.path.write_text('\ufeffSource\n1. FIRST\n========\n1.1 First section\n----\nAlpha\n'
                             '1.2 Next section\n----\nBeta\n2. SECOND\n========\n2.1 Other\n----\nGamma\n')

    def test_exact_line_citations_and_hash_follow_the_source(self):
        library = MethodLibrary(self.path)
        section = library.read(section='1.1')
        original = self.path.read_text(encoding='utf-8-sig').splitlines()
        self.assertEqual(section['document_sha256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(section['line_start'], 4)
        self.assertEqual(section['line_end'], 6)
        for line in section['lines']:
            self.assertEqual(line['text'], original[line['line']-1])
        self.assertEqual(len(library.catalog()['chapters']), 2)

    def test_updated_document_gets_a_new_hash(self):
        before = MethodLibrary(self.path).sha256
        self.path.write_text(self.path.read_text() + '\nNew text\n')
        self.assertNotEqual(MethodLibrary(self.path).sha256, before)

    def test_missing_section_and_wrong_chapter_do_not_invent_a_citation(self):
        library = MethodLibrary(self.path)
        for arguments in (dict(section='9.1'), dict(chapter=1, section='2.1')):
            with self.assertRaises(MethodError):
                library.read(**arguments)

    def test_search_is_scoped_and_paginated(self):
        library = MethodLibrary(self.path)
        self.assertEqual(library.search('GAMMA', chapter=1)['total'], 0)
        self.assertEqual(library.search('GAMMA', chapter=2)['total'], 1)
        self.assertEqual(library.search('section', start=1, limit=1)['matches'][0]['lines'][-1]['line'], 12)


if __name__ == '__main__':
    unittest.main()
