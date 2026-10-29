#!/usr/bin/env python3
"""Finish the authorized project rename and point personal skills to canonical files."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ecosystem/src'), str(ROOT/'knowledge_pipeline/src')]
from learning_ecosystem.workspace import atomic_json, load_json


def relocate(root, target):
    """Move the project and rebase active metadata without creating an old alias."""
    root, target = Path(root).resolve(), Path(target).absolute()
    journal_path = root/'ecosystem/data/relocation.json'
    journal = load_json(journal_path) if journal_path.is_file() else None
    if root != target and (target.exists() or target.is_symlink()):
        raise ValueError('Destination already exists; nothing renamed')
    pending = root/'knowledge_pipeline/data/justin_sung/synthesis/state/pending.json'
    if pending.exists():
        raise ValueError('Complete the pending synthesis transaction before relocation')
    if root != target:
        old = str(root)
        journal = dict(schema_version=1, old_root=old, target=str(target), status='prepared')
        atomic_json(journal_path, journal)
        os.rename(root, target)
    elif journal:
        old = journal['old_root']
    else:
        return root
    # Historical audit files retain their original values. Only active paths change.
    path = target/'knowledge_pipeline/data/justin_sung/synthesis/state/state.json'
    if path.is_file():
        state = load_json(path)
        for source in state.get('sources', {}).values():
            value = source.get('path', '')
            if value == old or value.startswith(old + '/'):
                source['path'] = str(target) + value[len(old):]
        atomic_json(path, state)
    journal['status'] = 'complete'
    atomic_json(target/'ecosystem/data/relocation.json', journal)
    return target


def remove_legacy_alias(root, previous_root):
    """Unlink only an obsolete alias to this project; preserve other contents."""
    root, previous = Path(root).resolve(), Path(previous_root).absolute()
    if previous == root:
        return dict(status='not-needed')
    if not previous.is_symlink():
        if previous.exists():
            raise ValueError('Refusing to remove a previous path that is not a symlink: ' + str(previous))
        return dict(status='absent', path=str(previous))
    if previous.resolve() != root:
        raise ValueError('Refusing to remove a previous alias pointing elsewhere: ' + str(previous))
    previous.unlink()
    return dict(status='removed', path=str(previous))


def register_skills(root, personal, *, names=('study-library','study-progress','study-tutor','study-coach')):
    personal = Path(personal).expanduser()
    personal.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = personal.parent/'skill-backups'/('ecosystem-' + stamp)
    changed = []
    for name in names:
        canonical = root/'skills'/name
        if not (canonical/'SKILL.md').is_file():
            raise ValueError(f'Missing canonical skill: {name}')
        destination = personal/name
        if destination.is_symlink() and destination.resolve() == canonical.resolve():
            continue
        if destination.exists() or destination.is_symlink():
            backup.mkdir(parents=True, exist_ok=True)
            destination.rename(backup/name)
        try:
            destination.symlink_to(canonical, target_is_directory=True)
        except BaseException:
            if (backup/name).exists() or (backup/name).is_symlink():
                (backup/name).rename(destination)
            raise
        changed.append(name)
    return dict(changed=changed, backup=str(backup) if backup.exists() else None)


def link_old_library(root):
    # Kept as a compatibility entrypoint for earlier installers. Finalization
    # must never recreate a link or backup in a separate coursework repository.
    return dict(status='independent', study_root=str(Path(root)/'ecosystem/data/studium'))


def windows_task(root):
    executable = Path('/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
    if not executable.is_file():
        return dict(status='not-available', manual_script='knowledge_pipeline/scripts/windows/install_task.ps1')
    # Invoke the project-owned installer through WSL's Windows path conversion.
    script = root/'knowledge_pipeline/scripts/windows/install_task.ps1'
    converted = subprocess.run(['wslpath','-w',str(script)],capture_output=True,text=True,timeout=10,check=True).stdout.strip()
    command = "& '" + converted.replace("'", "''") + "' -ProjectRoot '" + str(root/'knowledge_pipeline').replace("'", "''") + "' -Distribution '" + os.environ.get('WSL_DISTRO_NAME','Ubuntu').replace("'", "''") + "' -LinuxUser '" + pwd.getpwuid(os.getuid()).pw_name.replace("'", "''") + "' -Replace"
    # Permit this project-owned installer in this process only; leave persistent
    # user/machine execution policies intact. Group Policy still takes precedence.
    result = subprocess.run([str(executable),'-NoLogo','-NoProfile','-NonInteractive',
                             '-ExecutionPolicy','Bypass','-Command',command],
                            capture_output=True,text=True,timeout=45)
    if result.returncode:
        return dict(status='needs-windows-terminal', error=result.stderr.strip(),
                    manual_script=str(script))
    return dict(status='updated', output=result.stdout.strip())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',type=Path,default=ROOT.with_name('learning_ecosystem'))
    parser.add_argument('--skills-root',type=Path,default=Path.home()/'.codex/skills')
    parser.add_argument('--skip-windows',action='store_true')
    parser.add_argument('--local-only',action='store_true',
                        help='Repair a manually moved project without changing directories outside it.')
    args=parser.parse_args(argv)
    try:
        if args.local_only and args.target.absolute() != ROOT:
            raise ValueError('--local-only requires the project to be at its target already')
        # The same lock order as the production scheduler/downloader/updater.
        from contextlib import ExitStack
        with ExitStack() as stack:
            channel=ROOT/'knowledge_pipeline/data/justin_sung'
            for path in (channel/'incoming/.schedule.lock',channel/'incoming/.download.lock',channel/'synthesis/state/.lock'):
                stream=stack.enter_context(path.open('a'))
                fcntl.flock(stream,fcntl.LOCK_EX | fcntl.LOCK_NB)
            root=relocate(ROOT,args.target)
            journal_path=root/'ecosystem/data/relocation.json'
            previous=load_json(journal_path)['old_root'] if journal_path.is_file() else str(ROOT)
            from learning_ecosystem.setup import repair_runtime_paths
            runtime=repair_runtime_paths(root,previous)
        results=dict(root=str(root),previous_root=previous,runtime=runtime,
                     scope='local' if args.local_only else 'full')
        if args.local_only:
            results.update(skills=dict(status='pending-registration'),
                           library=dict(status='original-preserved'),
                           windows=dict(status='not-checked'))
        else:
            results['skills']=register_skills(root,args.skills_root)
            results['library']=link_old_library(root)
        python=root/'knowledge_pipeline/.venv/bin/python'
        subprocess.run([str(python),'-B',str(root/'ecosystem.py'),'install'],check=True)
        if not args.local_only and not args.skip_windows:
            try:
                results['windows']=windows_task(root)
            except (OSError,subprocess.SubprocessError) as error:
                results['windows']=dict(status='needs-windows-terminal',error=str(error))
        if args.local_only:
            results['previous_alias']=dict(status='not-checked', path=previous)
        else:
            # Retire the external alias after repairing the runtime and attempting
            # to update Windows. Never recreate it, including on interrupted retry.
            results['previous_alias']=remove_legacy_alias(root,previous)
        atomic_json(root/'ecosystem/data/finalization.json',results)
        print(json.dumps(results,ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        print('Finalization stopped: '+str(error),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
