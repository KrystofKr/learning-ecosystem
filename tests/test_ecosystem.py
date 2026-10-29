import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'ecosystem/src'), str(ROOT/'knowledge_pipeline/src')]
from learning_ecosystem.workspace import Workspace, EcosystemError, atomic_json, digest, load_json
from learning_ecosystem.sync import synchronize
from learning_ecosystem.training import Trainer, effective


def definition(name, section):
    return dict(id=name, title=name, sections=[section], prerequisites=[],
                purpose='Build a meaningful structure', practice_task='Produce and explain a structure',
                rubric={'relations': 'Explain actual relations', 'check': 'Check one relationship'})


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = Workspace(self.root)
        helper = self.root/'skills/study-progress/scripts/study_store.py'
        helper.parent.mkdir(parents=True)
        shutil.copy2(ROOT/'skills/study-progress/scripts/study_store.py', helper)
        self.workspace.registry.parent.mkdir(parents=True)
        atomic_json(self.workspace.registry, dict(schema_version=1,
                    techniques=[definition('priming', '4'), definition('retrieval', '9')]))
        self.workspace.synthesis.parent.mkdir(parents=True)
        self.text = ('4. PRIMING\n========\n4.1 Structure\n--------\nUse hypotheses.\n'
                     '9. RETRIEVAL\n========\n9.1 Own attempt\n--------\nTry before feedback.\n')
        self.publish(self.text)
        synchronize(self.root)
        store = self.workspace.store()
        for subject in ('alpha', 'beta'):
            store.init(subject, subject, ['Explain and apply'])
        self.trainer = Trainer(self.root)
        self.clock = datetime.now(timezone.utc) - timedelta(days=10)

    def publish(self, text):
        raw = text.encode('utf-8')
        self.workspace.synthesis.write_bytes(raw)
        atomic_json(self.workspace.synthesis.parent/'state/state.json',
                    dict(synthesis_sha256=digest(raw)))

    def attempt(self, *, context='example-a', domain='math', technique='priming',
                assistance='none', seen=False, assessment='passed', exposure_at=None):
        with patch('learning_ecosystem.training.now', return_value=self.clock.isoformat()):
            lesson = self.trainer.lesson('alpha', context=context, domain=domain, technique=technique)
        self.clock += timedelta(minutes=10)
        record = dict(attempt_id='attempt-' + str(len(self.trainer.load('alpha')['attempts']) + 1),
                      lesson_id=lesson['lesson_id'], answer='Actual synthetic learner artifact',
                      assessment=assessment, criteria={key: assessment == 'passed' for key in lesson['rubric']},
                      assistance=assistance, solution_seen=seen, occurred_at=self.clock.isoformat(),
                      review_notes='Checked against the issued criteria', reviewer='assistant')
        if exposure_at is not None:
            record['exposure_at'] = exposure_at
        result = self.trainer.record('alpha', record)
        return lesson, record, result

    def progress(self):
        return next(item for item in self.trainer.status('alpha')['techniques'] if item['technique_id']=='priming')


class SyncTests(Fixture):
    def test_empty_skeleton_is_not_claimed_as_usable_methodology(self):
        shutil.rmtree(self.workspace.methods)
        self.publish('4. PRIMING\n========\n4.1 Structure\n--------\n\n9. RETRIEVAL\n========\n9.1 Own attempt\n--------\n')
        synchronize(self.root)
        self.assertFalse(any(item['available'] for item in self.workspace.current()['techniques']))
        with self.assertRaises(EcosystemError):
            self.trainer.next_step('alpha')

    def test_new_unavailable_section_is_explicit_and_does_not_break_existing_methods(self):
        registry=load_json(self.workspace.registry)
        registry['techniques'].append(definition('new-method','6.5'))
        atomic_json(self.workspace.registry,registry)
        synchronize(self.root)
        method=next(item for item in self.workspace.current()['techniques'] if item['id']=='new-method')
        self.assertFalse(method['available'])
        self.assertEqual(method['missing_sections'],['6.5'])
        self.assertEqual(self.trainer.next_step('alpha')['technique_id'],'priming')

    def test_unchanged_input_is_idempotent(self):
        before = (self.workspace.methods/'current.json').read_bytes()
        self.assertEqual(synchronize(self.root)['status'], 'current')
        self.assertEqual((self.workspace.methods/'current.json').read_bytes(), before)

    def test_only_changed_technique_requires_revalidation(self):
        self.attempt()
        before = self.trainer.load('alpha')
        old = self.workspace.current()
        self.publish(self.text.replace('Use hypotheses.', 'Use hypotheses and compare relationships.'))
        result = synchronize(self.root)
        self.assertEqual(result['changed_techniques'], ['priming'])
        self.assertEqual(before, self.trainer.load('alpha'))
        self.assertTrue(self.progress()['needs_revalidation'])
        old_snapshot = self.workspace.methods/'versions'/old['version']/'synthesis.txt'
        self.assertEqual(old_snapshot.read_text(), self.text)

    def test_line_moves_refresh_citations_without_losing_unchanged_method(self):
        old = self.workspace.current()
        self.publish('Preface\n' + self.text)
        result = synchronize(self.root)
        self.assertEqual(result['changed_techniques'], [])
        current = self.workspace.current()
        self.assertEqual(current['techniques'][0]['source_refs'][0]['line_start'],
                         old['techniques'][0]['source_refs'][0]['line_start'] + 1)

    def test_incomplete_source_transaction_retains_previous_publication(self):
        pointer = self.workspace.methods/'current.json'
        before = pointer.read_bytes()
        atomic_json(self.workspace.synthesis.parent/'state/pending.json', {})
        with self.assertRaises(EcosystemError):
            synchronize(self.root)
        self.assertEqual(pointer.read_bytes(), before)

    def test_untracked_source_change_is_rejected(self):
        self.workspace.synthesis.write_text(self.text + 'Untracked edit')
        with self.assertRaises(EcosystemError):
            synchronize(self.root)

    def test_registry_cycle_rejected_without_replacing_catalog(self):
        registry = load_json(self.workspace.registry)
        registry['techniques'][0]['prerequisites']=['retrieval']
        registry['techniques'][1]['prerequisites']=['priming']
        atomic_json(self.workspace.registry,registry)
        before = self.workspace.current()['version']
        with self.assertRaises(EcosystemError):
            synchronize(self.root)
        self.assertEqual(self.workspace.current()['version'],before)

    def test_failed_publication_keeps_old_pointer_and_can_be_retried(self):
        before = self.workspace.current()['version']
        self.publish(self.text.replace('Use hypotheses.', 'Changed valid method.'))
        import learning_ecosystem.sync as module
        original = module.atomic_json
        def fail_pointer(path,value):
            if Path(path).name=='current.json':
                raise OSError('Synthetic publish failure')
            return original(path,value)
        with patch.object(module,'atomic_json',side_effect=fail_pointer), self.assertRaises(OSError):
            synchronize(self.root)
        self.assertEqual(self.workspace.current()['version'],before)
        self.assertEqual(synchronize(self.root)['status'],'updated')


class TrainingTests(Fixture):
    def test_lesson_alone_does_not_establish_a_skill(self):
        self.trainer.lesson('alpha', context='task', domain='math', technique='priming')
        self.assertEqual(self.progress()['completed_stages'],[])
        self.assertEqual(self.progress()['effective_attempts'],0)

    def test_guided_output_and_two_distinct_unassisted_contexts(self):
        self.attempt()
        self.assertEqual(self.progress()['next_stage'],'guided')
        self.attempt(assistance='hint',seen=None)
        self.assertEqual(self.progress()['next_stage'],'independent')
        self.attempt(context='same',assistance='hint')
        self.assertEqual(self.progress()['independent_contexts'],0)
        self.attempt(context='same',seen=None)
        self.assertEqual(self.progress()['independent_contexts'],0)
        self.attempt(context='same')
        self.attempt(context='same')
        self.assertEqual(self.progress()['next_stage'],'independent')
        self.attempt(context='different')
        self.assertEqual(self.progress()['next_stage'],'transfer')

    def test_transfer_requires_another_domain(self):
        for context in ('orientation','guided','independent-a','independent-b'):
            self.attempt(context=context)
        self.attempt(context='new-task',domain='math')
        self.assertEqual(self.progress()['next_stage'],'transfer')
        self.attempt(context='different-type',domain='programming')
        self.assertEqual(self.progress()['next_stage'],'retained')

    def test_retention_respects_known_recent_exposure(self):
        for context,domain in [('a','math'),('b','math'),('c','math'),('d','math'),('e','programming')]:
            self.attempt(context=context,domain=domain)
        earlier = self.clock - timedelta(days=3)
        self.attempt(context='f',exposure_at=earlier.isoformat())
        self.assertEqual(self.progress()['next_stage'],'retained')
        last_exposure = self.clock
        self.clock += timedelta(days=2)
        self.attempt(context='g',exposure_at=last_exposure.isoformat())
        self.assertEqual(self.progress()['next_stage'],'maintenance')

    def test_failure_requires_new_demonstration_of_that_stage(self):
        self.attempt()
        lesson, record, _ = self.attempt(assessment='failed')
        self.assertEqual(self.progress()['next_stage'],'guided')
        self.attempt()
        self.assertEqual(self.progress()['next_stage'],'independent')

    def test_idempotent_retry_and_conflict_do_not_duplicate_attempts(self):
        _,record,_=self.attempt()
        self.assertTrue(self.trainer.record('alpha',record)['duplicate'])
        changed=dict(record,answer='Changed output')
        with self.assertRaises(EcosystemError):
            self.trainer.record('alpha',changed)
        self.assertEqual(self.trainer.status('alpha')['attempt_count'],1)

    def test_correction_preserves_original_activity_and_order(self):
        _, first, _ = self.attempt()
        _, second, _ = self.attempt()
        corrected=dict(first,attempt_id='corrected-1',correction_of=first['attempt_id'],
                       assessment='unassessed',criteria={key:None for key in first['criteria']})
        self.trainer.record('alpha',corrected)
        records=effective(self.trainer.load('alpha')['attempts'])
        self.assertEqual([item['attempt_id'] for item in records],['corrected-1',second['attempt_id']])
        self.assertEqual(self.progress()['next_stage'],'orientation')
        self.assertEqual(self.trainer.status('alpha')['attempt_count'],2)

    def test_real_answer_and_exact_criteria_are_required(self):
        lesson=self.trainer.lesson('alpha',context='a',domain='math',technique='priming')
        record=dict(attempt_id='attempt-1',lesson_id=lesson['lesson_id'],answer='',assessment='passed',
                    criteria={key:True for key in lesson['rubric']},assistance='none',solution_seen=False,
                    occurred_at=self.clock.isoformat(),review_notes='Observed criteria',reviewer='assistant')
        with self.assertRaises(EcosystemError):
            self.trainer.record('alpha',record)
        record['answer']='Own actual response'
        record['criteria']={'other':True}
        with self.assertRaises(EcosystemError):
            self.trainer.record('alpha',record)

    def test_grading_correction_cannot_fabricate_another_answer(self):
        _,record,_=self.attempt()
        corrected=dict(record,attempt_id='correction-1',correction_of=record['attempt_id'],answer='New answer')
        with self.assertRaises(EcosystemError):
            self.trainer.record('alpha',corrected)

    def test_technique_progress_does_not_modify_subject_results(self):
        path=self.workspace.study/'subjects/alpha/state.json'
        before=path.read_bytes()
        self.attempt()
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.trainer.status('beta')['attempt_count'],0)


class PipelineIntegrationTests(unittest.TestCase):
    def test_committed_synthesis_retries_failed_publication_without_another_model_call(self):
        sys.path.insert(0,str(ROOT/'knowledge_pipeline/tests'))
        from learning_app.knowledge_pipeline.service import KnowledgePipeline
        from learning_app.knowledge_pipeline.settings import load_settings
        from learning_app.knowledge_pipeline.subscription import CodexAccess
        from learning_app.knowledge_pipeline import synthesis
        from learning_app.knowledge_pipeline import ecosystem as integration
        from learning_app.knowledge_pipeline.errors import SynthesisError
        from test_synthesis import TRANSCRIPT, panel, plan, review
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            shutil.copy2(ROOT/'ecosystem.py',root/'ecosystem.py')
            shutil.copytree(ROOT/'ecosystem/src',root/'ecosystem/src',ignore=shutil.ignore_patterns('__pycache__'))
            registry=root/'ecosystem/config/techniques.json'
            atomic_json(registry,dict(schema_version=1,techniques=[definition('relationships','6')]))
            component=root/'knowledge_pipeline'
            component.mkdir()
            settings=load_settings(root=component)
            calls=[]
            def editor(task,*arguments):
                calls.append(task)
                return plan() if task=='PLAN' else review()
            pipeline=KnowledgePipeline(settings,editor=editor,access_checker=lambda:CodexAccess('pro','2026-10-27T10:00:00+00:00'))
            pipeline.initialize()
            folder=settings.incoming/'2026-10-25_newVideo001'
            folder.mkdir()
            (folder/'transcript.txt').write_text(TRANSCRIPT)
            atomic_json(folder/'panel.json',panel('newVideo001',TRANSCRIPT))
            atomic_json(folder/'metadata.json',dict(id='newVideo001',title='Synthetic example',
                        published_date='2026-10-25',text_sha256=synthesis.digest(TRANSCRIPT.encode())))
            actual_sync=integration.synchronize
            sync_calls=[]
            def fail_once(settings):
                sync_calls.append(settings)
                if len(sync_calls)==1:
                    raise SynthesisError('Synthetic publication failure after source commit')
                return actual_sync(settings)
            with patch.object(integration,'synchronize',side_effect=fail_once):
                with self.assertRaises(SynthesisError):
                    pipeline.update()
                self.assertEqual(pipeline.validate(panels=True)['sources'],1)
                self.assertTrue(pipeline.check()['pending'])
                self.assertEqual(pipeline.update()['processed'],[])
            catalog=Workspace(root).current()
            self.assertIn('Porovnej dva příklady',catalog['sections']['6']['text'])
            self.assertEqual(calls,['PLAN','REVIEW'])
            self.assertFalse(pipeline.check()['pending'])
            self.assertEqual(pipeline.update()['processed'],[])
            self.assertEqual(calls,['PLAN','REVIEW'])


if __name__=='__main__':
    unittest.main()
