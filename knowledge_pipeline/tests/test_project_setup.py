from access_fixtures import permit_codex_access
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from learning_app.knowledge_pipeline.settings import load_settings
from learning_app.knowledge_pipeline.bootstrap import initialize_storage
from learning_app.knowledge_pipeline.synthesis import Updater,digest,write_json
from learning_app.knowledge_pipeline.archive import validate_archive

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('repository_check',ROOT/'scripts/check_repository.py')
repository_check=importlib.util.module_from_spec(spec);spec.loader.exec_module(repository_check)


class SetupTests(unittest.TestCase):
    def setUp(self):
        permit_codex_access(self)

    def test_default_paths_are_relative_to_checkout_and_not_a_personal_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);settings=load_settings(root=root)
            self.assertEqual(settings.incoming,root/'data/justin_sung/incoming')
            self.assertEqual(settings.synthesis,root/'data/justin_sung/synthesis/main.txt')
            self.assertEqual(settings.baseline,root/'config/baseline.json')

    def test_local_data_location_is_configurable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'local.json'
            config.write_text(json.dumps({'data_dir':'private_materials'}))
            settings=load_settings(root=root,config_file=config)
            self.assertEqual(settings.archive,root/'private_materials/justin_sung/archive')

    def test_empty_installation_needs_no_bundled_transcripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=load_settings(root=Path(tmp))
            result=initialize_storage(settings)
            self.assertTrue(result['baseline_required'])
            self.assertFalse(settings.baseline.exists())
            self.assertEqual(validate_archive(settings.archive,verify_panels=True)[0],[])
            updater=Updater(settings.incoming,settings.synthesis,settings.archive)
            self.assertFalse(updater.has_pending())
            before=settings.synthesis.read_bytes()
            initialize_storage(settings)
            self.assertEqual(settings.synthesis.read_bytes(),before)

    def test_first_transcript_can_enrich_an_empty_installation(self):
        from test_synthesis import TRANSCRIPT,panel,plan,review
        with tempfile.TemporaryDirectory() as tmp:
            settings=load_settings(root=Path(tmp));initialize_storage(settings)
            folder=settings.incoming/'2026-10-25_newVideo001';folder.mkdir()
            (folder/'transcript.txt').write_text(TRANSCRIPT)
            (folder/'panel.json').write_text(json.dumps(panel('newVideo001',TRANSCRIPT)))
            write_json(folder/'metadata.json',{'id':'newVideo001','title':'Synthetic example',
                'published_date':'2026-10-25','text_sha256':digest(TRANSCRIPT.encode())})
            editor=lambda task,*args:plan() if task=='PLAN' else review()
            updater=Updater(settings.incoming,settings.synthesis,settings.archive,editor=editor)
            self.assertEqual(updater.run(),['newVideo001'])
            self.assertFalse(folder.exists())
            self.assertEqual(len(validate_archive(settings.archive,verify_panels=True)[0]),1)
            self.assertIn('S01 |',settings.synthesis.read_text(encoding='utf-8-sig'))

    def test_cli_runs_from_a_clean_directory_with_no_personal_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ,LEARNING_KNOWLEDGE_ROOT=tmp,PYTHONPATH=str(ROOT/'src'))
            env.pop('LEARNING_KNOWLEDGE_CONFIG',None)
            for command in ['init','check','validate']:
                result=subprocess.run([sys.executable,'-B','-m','learning_app.knowledge_pipeline',command],cwd=tmp,env=env,
                    capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                json.loads(result.stdout)


class RepositoryTests(unittest.TestCase):
    def test_private_profile_allows_portable_data_and_checks_it_for_credentials(self):
        names=['ecosystem/data/studium/subjects/alpha/state.json',
               'ecosystem/data/studium/subjects/alpha/sources/notes.pdf',
               'ecosystem/data/training/alpha/progress.json',
               'ecosystem/data/methods/versions/example/synthesis.txt',
               'knowledge_pipeline/data/justin_sung/synthesis/main.txt',
               'knowledge_pipeline/data/justin_sung/archive/example/transcript.txt',
               'knowledge_pipeline/config/baseline.json']
        self.assertEqual(repository_check.violations(names,lambda _:b'ordinary source',profile='private'),[])
        self.assertEqual(len(repository_check.violations(names,lambda _:b'ordinary source')),len(names))
        secret=b'ghp_'+b'x'*36
        self.assertEqual(repository_check.violations(names[:1],lambda _:secret,profile='private'),
                         [(names[0],'credential-like content')])

    def test_private_profile_still_rejects_machine_state_and_incomplete_transactions(self):
        names=['ecosystem/data/installation.json', 'ecosystem/data/studium/subjects/alpha/.lock',
               'ecosystem/data/studium/subjects/alpha/.env',
               'ecosystem/data/studium/subjects/alpha/secret.pem',
               'ecosystem/data/renders/alpha/page.png',
               'knowledge_pipeline/data/justin_sung/incoming/access_state.json',
               'knowledge_pipeline/data/justin_sung/synthesis/state/pending.json',
               'knowledge_pipeline/config/knowledge_pipeline.local.json',
               'knowledge_pipeline/.venv/package.py']
        self.assertEqual({name for name,_ in repository_check.violations(
            names,lambda _:b'ordinary source',profile='private')},set(names))

    def test_public_visibility_cannot_use_a_private_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            subprocess.run(['git','init','--quiet',str(root)],check=True,capture_output=True)
            profile=root/'.github/repository-profile.json'
            profile.parent.mkdir()
            profile.write_text(json.dumps({'schema_version':1,'profile':'private'}))
            subprocess.run(['git','add','.github'],cwd=root,check=True,capture_output=True)
            result=subprocess.run([sys.executable,str(ROOT/'scripts/check_repository.py'),'--root',str(root),
                                   '--profile','public'],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,1)
            self.assertIn('declared private, expected public',result.stdout)

    def test_ecosystem_data_is_private_while_skills_and_curriculum_are_public(self):
        private=['ecosystem/data/studium/subjects/tilo/state.json',
                 'ecosystem/data/methods/versions/version/synthesis.txt',
                 'ecosystem/reports/audit.md','ecosystem/config/local.json']
        public=['ecosystem/config/techniques.json','ecosystem/src/learning_ecosystem/cli.py',
                'skills/study-coach/SKILL.md','ecosystem.py','AGENTS.md']
        rejected=repository_check.violations(private+public,lambda _:b'ordinary source')
        self.assertEqual({name for name,_ in rejected},set(private))

    def test_nested_component_keeps_runtime_data_and_credentials_private(self):
        names=['knowledge_pipeline/data/source.txt',
               'knowledge_pipeline/reports/audit.md',
               'knowledge_pipeline/config/knowledge_pipeline.local.json',
               'knowledge_pipeline/.venv/package.py',
               'knowledge_pipeline/src/learning_app/knowledge_pipeline/cli.py',
               'knowledge_pipeline/config/knowledge_pipeline.example.json']
        rejected=repository_check.violations(names,lambda _:b'ordinary source')
        self.assertEqual({name for name,_ in rejected},set(names[:4]))

    def test_private_paths_are_rejected_and_source_code_is_allowed(self):
        names=['data/source.txt','reports/audit.md','config/local.json','config/baseline.json','src/learning_app/knowledge_pipeline/cli.py']
        rejected=repository_check.violations(names,lambda _:b'ordinary source')
        self.assertEqual({name for name,_ in rejected},set(names[:-1]))

    def test_data_in_a_nonstandard_root_directory_is_also_rejected(self):
        rejected=repository_check.violations(['private_materials/transcript.txt'],lambda _:b'ordinary text')
        self.assertEqual(rejected,[('private_materials/transcript.txt','unexpected repository path')])

    def test_checker_reads_the_staged_content_even_if_the_worktree_was_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            subprocess.run(['git','init','--quiet',str(root)],check=True,capture_output=True)
            file=root/'README.md';file.write_text('ghp_'+'x'*36)
            subprocess.run(['git','add','README.md'],cwd=root,check=True,capture_output=True)
            file.write_text('Safe working copy.')
            result=subprocess.run([sys.executable,str(ROOT/'scripts/check_repository.py'),'--root',str(root)],
                capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,1)
            self.assertIn('credential-like content',result.stdout)
            self.assertNotIn('ghp_'+'x'*36,result.stdout)
