import importlib.util
from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ecosystem/src'))
from learning_ecosystem.workspace import Workspace, atomic_json, digest, load_json
from learning_ecosystem.setup import import_library, repair_runtime_paths

spec=importlib.util.spec_from_file_location('ecosystem_finalize',ROOT/'ecosystem/scripts/finalize.py')
finalize=importlib.util.module_from_spec(spec)
spec.loader.exec_module(finalize)


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent=Path(self.tmp.name)
        self.root=self.parent/'learning_app'
        self.root.mkdir()

    def test_rename_updates_only_active_paths_without_leaving_an_alias(self):
        state_path=self.root/'knowledge_pipeline/data/justin_sung/synthesis/state/state.json'
        old_source=self.root/'knowledge_pipeline/data/archive/transcript.txt'
        atomic_json(state_path,dict(sources={'video':dict(path=str(old_source))},synthesis_sha256='a'*64))
        history=state_path.parent/'history/old_state.json'
        atomic_json(history,dict(original_path=str(old_source)))
        before=history.read_bytes()
        target=self.parent/'learning_ecosystem'
        self.assertEqual(finalize.relocate(self.root,target),target)
        self.assertFalse(self.root.exists())
        self.assertFalse(self.root.is_symlink())
        current=load_json(target/state_path.relative_to(self.root))
        self.assertEqual(current['sources']['video']['path'],str(target/old_source.relative_to(self.root)))
        self.assertEqual((target/history.relative_to(self.root)).read_bytes(),before)

    def test_destination_conflict_does_not_rename(self):
        target=self.parent/'learning_ecosystem'
        target.mkdir()
        with self.assertRaises(ValueError):
            finalize.relocate(self.root,target)
        self.assertTrue(self.root.is_dir())
        self.assertFalse(self.root.is_symlink())

    def test_interrupted_metadata_rebase_can_resume_from_the_new_location(self):
        state=self.root/'knowledge_pipeline/data/justin_sung/synthesis/state/state.json'
        atomic_json(state,dict(sources={'video':dict(path=str(self.root/'archive/a.txt'))}))
        target=self.parent/'learning_ecosystem'
        original=finalize.atomic_json
        def fail_state(path,value):
            if Path(path).name=='state.json':
                raise OSError('Synthetic metadata failure')
            return original(path,value)
        with patch.object(finalize,'atomic_json',side_effect=fail_state), self.assertRaises(OSError):
            finalize.relocate(self.root,target)
        self.assertEqual(finalize.relocate(target,target),target)
        self.assertFalse(self.root.exists())
        updated=load_json(target/state.relative_to(self.root))
        self.assertEqual(updated['sources']['video']['path'],str(target/'archive/a.txt'))

    def test_pending_synthesis_prevents_rename(self):
        atomic_json(self.root/'knowledge_pipeline/data/justin_sung/synthesis/state/pending.json',{})
        with self.assertRaises(ValueError):
            finalize.relocate(self.root,self.parent/'learning_ecosystem')
        self.assertFalse(self.root.is_symlink())

    def test_manual_rename_repairs_active_paths_without_writing_a_legacy_link(self):
        state=self.root/'knowledge_pipeline/data/justin_sung/synthesis/state/state.json'
        source=self.root/'archive/a.txt'
        atomic_json(state,dict(sources={'video':dict(path=str(source))}))
        history=state.parent/'history/old.json'
        atomic_json(history,dict(path=str(source)))
        target=self.parent/'learning_ecosystem'
        atomic_json(self.root/'ecosystem/data/relocation.json',
                    dict(schema_version=1,old_root=str(self.root),target=str(target),status='prepared'))
        self.root.rename(target)
        with patch.object(Path,'symlink_to',side_effect=PermissionError('Parent is read-only')):
            self.assertEqual(finalize.relocate(target,target),target)
        updated=load_json(target/state.relative_to(self.root))
        self.assertEqual(updated['sources']['video']['path'],str(target/'archive/a.txt'))
        self.assertEqual(load_json(target/history.relative_to(self.root))['path'],str(source))
        self.assertFalse(self.root.exists())
        self.assertEqual(finalize.relocate(target,target),target)

    def test_installed_launcher_and_activation_work_after_manual_rename(self):
        environment=self.root/'knowledge_pipeline/.venv'
        scripts=environment/'bin'
        scripts.mkdir(parents=True)
        (scripts/'python').symlink_to(Path(sys.executable).resolve())
        launcher=scripts/'example'
        launcher.write_text('#!'+str(scripts/'python')+'\nprint("launcher works")\n')
        launcher.chmod(0o755)
        (scripts/'activate').write_text('export VIRTUAL_ENV='+str(environment)+'\n')
        site=environment/'lib/python3.12/site-packages'
        site.mkdir(parents=True)
        (site/'editable.pth').write_text(str(self.root/'knowledge_pipeline/src')+'\n')
        package=site/'example-1.dist-info'
        package.mkdir()
        atomic_json(package/'direct_url.json',dict(url=self.root.as_uri()+'/knowledge_pipeline'))
        untouched=site/'original.py'
        untouched.write_text('original = '+repr(str(self.root))+'\n')
        before=untouched.read_bytes()
        target=self.parent/'learning_ecosystem'
        self.root.rename(target)
        result=repair_runtime_paths(target,self.root)
        self.assertEqual(len(result['changed']),4)
        moved=target/'knowledge_pipeline/.venv/bin/example'
        completed=subprocess.run([str(moved)],capture_output=True,text=True,check=True)
        self.assertEqual(completed.stdout,'launcher works\n')
        activated=subprocess.run(['bash','-c','source "$1"; test "$VIRTUAL_ENV" = "$2"',
                                  'test',str(moved.with_name('activate')),str(moved.parent.parent)])
        self.assertEqual(activated.returncode,0)
        moved_site=target/site.relative_to(self.root)
        self.assertEqual((moved_site/'editable.pth').read_text(),str(target/'knowledge_pipeline/src')+'\n')
        self.assertEqual(load_json(moved_site/'example-1.dist-info/direct_url.json')['url'],
                         target.as_uri()+'/knowledge_pipeline')
        self.assertEqual((target/untouched.relative_to(self.root)).read_bytes(),before)
        self.assertTrue(moved.stat().st_mode & 0o100)
        self.assertEqual(repair_runtime_paths(target,self.root)['changed'],[])

    def test_rename_does_not_require_permission_to_create_compatibility_links(self):
        target=self.parent/'learning_ecosystem'
        with patch.object(Path,'symlink_to',side_effect=OSError('Synthetic symlink failure')):
            self.assertEqual(finalize.relocate(self.root,target),target)
        self.assertTrue(target.is_dir())
        self.assertFalse(self.root.exists())

    def test_old_alias_is_removed_without_touching_project_and_stays_absent_on_retry(self):
        target=self.parent/'learning_ecosystem'
        finalize.relocate(self.root,target)
        content=target/'existing-work.txt'
        content.write_text('Preserve study work')
        inode=target.stat().st_ino
        self.root.symlink_to(target,target_is_directory=True)
        self.assertEqual(finalize.remove_legacy_alias(target,self.root)['status'],'removed')
        self.assertFalse(self.root.is_symlink())
        self.assertEqual(target.stat().st_ino,inode)
        self.assertEqual(content.read_text(),'Preserve study work')
        self.assertEqual(finalize.relocate(target,target),target)
        self.assertEqual(finalize.remove_legacy_alias(target,self.root)['status'],'absent')
        self.assertFalse(self.root.exists())

    def test_alias_cleanup_refuses_real_directories(self):
        target=self.parent/'learning_ecosystem'
        target.mkdir()
        content=self.root/'other-work.txt'
        content.write_text('Keep')
        with self.assertRaisesRegex(ValueError,'not a symlink'):
            finalize.remove_legacy_alias(target,self.root)
        self.assertEqual(content.read_text(),'Keep')

    def test_alias_cleanup_refuses_links_to_other_targets(self):
        target=self.parent/'learning_ecosystem'
        finalize.relocate(self.root,target)
        other=self.parent/'another-project'
        other.mkdir()
        self.root.symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'pointing elsewhere'):
            finalize.remove_legacy_alias(target,self.root)
        self.assertEqual(self.root.resolve(),other)

    def test_alias_cleanup_permission_failure_preserves_the_project(self):
        target=self.parent/'learning_ecosystem'
        finalize.relocate(self.root,target)
        self.root.symlink_to(target,target_is_directory=True)
        with patch.object(Path,'unlink',side_effect=PermissionError('Read-only parent')):
            with self.assertRaisesRegex(PermissionError,'Read-only'):
                finalize.remove_legacy_alias(target,self.root)
        self.assertEqual(self.root.resolve(),target)
        self.assertTrue(target.is_dir())

    def test_skills_are_links_to_one_canonical_copy_and_old_files_are_backed_up(self):
        personal=self.parent/'personal-skills'
        for name in ('study-library','study-progress','study-tutor','study-coach'):
            canonical=self.root/'skills'/name
            canonical.mkdir(parents=True)
            (canonical/'SKILL.md').write_text('New canonical instructions')
            old=personal/name
            old.mkdir(parents=True)
            (old/'SKILL.md').write_text('Original instructions')
        result=finalize.register_skills(self.root,personal)
        self.assertEqual(len(result['changed']),4)
        self.assertEqual((Path(result['backup'])/'study-tutor/SKILL.md').read_text(),'Original instructions')
        self.assertEqual((personal/'study-tutor').resolve(),self.root/'skills/study-tutor')
        self.assertEqual(finalize.register_skills(self.root,personal)['changed'],[])


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent=Path(temporary.name)
        self.previous=self.parent/'learning_app'
        self.previous.mkdir()
        self.root=self.parent/'learning_ecosystem'
        finalize.relocate(self.previous,self.root)
        self.previous.symlink_to(self.root,target_is_directory=True)
        for name in ('study-library','study-progress','study-tutor','study-coach'):
            skill=self.root/'skills'/name
            skill.mkdir(parents=True)
            (skill/'SKILL.md').write_text('Canonical instructions')
        channel=self.root/'knowledge_pipeline/data/justin_sung'
        (channel/'incoming').mkdir(parents=True)
        (channel/'synthesis/state').mkdir(parents=True)

    def run_finalizer(self, *extra):
        installed=subprocess.CompletedProcess([],0)
        with patch.object(finalize,'ROOT',self.root), \
             patch.object(finalize.subprocess,'run',return_value=installed), \
             redirect_stdout(io.StringIO()):
            return finalize.main(['--skills-root',str(self.parent/'personal-skills'),
                                  '--skip-windows',*extra])

    def test_full_finalization_removes_alias_and_does_not_recreate_it_on_repeat(self):
        self.assertEqual(self.run_finalizer(),0)
        self.assertFalse(self.previous.is_symlink())
        result=load_json(self.root/'ecosystem/data/finalization.json')
        self.assertEqual(result['previous_alias']['status'],'removed')
        self.assertEqual(self.run_finalizer(),0)
        self.assertFalse(self.previous.exists())
        result=load_json(self.root/'ecosystem/data/finalization.json')
        self.assertEqual(result['previous_alias']['status'],'absent')

    def test_local_only_keeps_external_alias_untouched(self):
        with patch.object(finalize,'remove_legacy_alias',side_effect=AssertionError('External write')):
            self.assertEqual(self.run_finalizer('--local-only'),0)
        self.assertEqual(self.previous.resolve(),self.root)
        result=load_json(self.root/'ecosystem/data/finalization.json')
        self.assertEqual(result['previous_alias']['status'],'not-checked')


class LibraryImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'project'
        helper=self.root/'skills/study-progress/scripts/study_store.py'
        helper.parent.mkdir(parents=True)
        shutil.copy2(ROOT/'skills/study-progress/scripts/study_store.py',helper)
        self.workspace=Workspace(self.root)
        self.original=Path(self.tmp.name)/'old-library'
        store_class=self.workspace.module('study-progress','study_store.py').StudyStore
        store_class(self.original).init('subject','Subject',['Real objective'])
        (self.original/'imports').mkdir()
        (self.original/'imports/manifest.json').write_text('{"provenance":"synthetic"}')

    def test_import_preserves_original_state_and_auxiliary_provenance(self):
        source=self.original/'subjects/subject/state.json'
        before=source.read_bytes()
        result=import_library(self.root,self.original)
        self.assertEqual(result['subjects'],1)
        self.assertEqual((self.workspace.study/'subjects/subject/state.json').read_bytes(),before)
        self.assertEqual(source.read_bytes(),before)
        self.assertEqual((self.workspace.study/'imports/manifest.json').read_bytes(),
                         (self.original/'imports/manifest.json').read_bytes())
        self.assertEqual(import_library(self.root,self.original)['status'],'already-imported')

    def test_modified_external_library_is_not_replaced_by_link(self):
        import_library(self.root,self.original)
        source=self.original/'subjects/subject/state.json'
        updated=source.read_text()+'\n'
        source.write_text(updated)
        result=finalize.link_old_library(self.root)
        self.assertEqual(result['status'],'independent')
        self.assertFalse(self.original.is_symlink())
        self.assertEqual(source.read_text(),updated)

    def test_finalization_keeps_library_local_without_creating_external_links_or_backups(self):
        import_library(self.root,self.original)
        result=finalize.link_old_library(self.root)
        self.assertEqual(result['status'],'independent')
        self.assertNotEqual(self.original.resolve(),self.workspace.study.resolve())
        self.assertFalse(self.original.is_symlink())
        self.assertFalse(list(self.original.parent.glob('old-library.before-*')))
        self.assertEqual(finalize.link_old_library(self.root)['status'],'independent')


class WindowsInstallerTests(unittest.TestCase):
    def test_installer_can_run_with_restricted_default_without_changing_persistent_policy(self):
        root=Path('/project/learning_ecosystem')
        converted='\\\\wsl.localhost\\Ubuntu\\project\\learning_ecosystem\\knowledge_pipeline\\scripts\\windows\\install_task.ps1'
        conversion=subprocess.CompletedProcess([],0,stdout=converted+'\n',stderr='')
        installed=subprocess.CompletedProcess([],0,stdout='LearningApp-JustinSung-Transcripts Ready\n',stderr='')
        with patch.object(finalize.Path,'is_file',return_value=True), \
             patch.object(finalize.subprocess,'run',side_effect=[conversion,installed]) as run:
            result=finalize.windows_task(root)
        self.assertEqual(result['status'],'updated')
        invocation=run.call_args_list[1].args[0]
        self.assertEqual(invocation[invocation.index('-ExecutionPolicy')+1],'Bypass')
        self.assertLess(invocation.index('-ExecutionPolicy'),invocation.index('-Command'))
        command=invocation[invocation.index('-Command')+1]
        self.assertIn(converted,command)
        self.assertIn("-ProjectRoot '/project/learning_ecosystem/knowledge_pipeline'",command)
        self.assertTrue(command.endswith(' -Replace'))
        self.assertNotIn('Set-ExecutionPolicy',command)
        self.assertEqual(result['output'],'LearningApp-JustinSung-Transcripts Ready')

    def test_group_policy_failure_remains_visible_instead_of_claiming_registration(self):
        conversion=subprocess.CompletedProcess([],0,stdout='C:\\installer.ps1\n',stderr='')
        failed=subprocess.CompletedProcess([],1,stdout='',stderr='Script execution blocked by Group Policy')
        with patch.object(finalize.Path,'is_file',return_value=True), \
             patch.object(finalize.subprocess,'run',side_effect=[conversion,failed]):
            result=finalize.windows_task(Path('/project/learning_ecosystem'))
        self.assertEqual(result['status'],'needs-windows-terminal')
        self.assertIn('Group Policy',result['error'])
        self.assertTrue(result['manual_script'].endswith('knowledge_pipeline/scripts/windows/install_task.ps1'))
        self.assertNotIn('legacy_path_supported',result)


if __name__=='__main__':
    unittest.main()
