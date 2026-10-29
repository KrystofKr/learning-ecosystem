"""Check the staged public source tree or the private study repository."""

import argparse
import json
import re
import subprocess
from pathlib import Path,PurePosixPath

PRIVATE_ROOTS={'data','reports','automation','youtube','.local','.venv','.agents','.aws','.codex'}
PRIVATE_FILES={'config/local.json','config/knowledge_pipeline.local.json','config/baseline.json'}
PUBLIC_ROOTS={'src','tests','scripts','docs','.github','skills'}
PUBLIC_FILES={'.gitignore','.gitattributes','MANIFEST.in','README.md','pyproject.toml',
              'LICENSE','LICENSE.txt','CONTRIBUTING.md','SECURITY.md',
              'config/knowledge_pipeline.example.json','config/techniques.json','AGENTS.md','ecosystem.py'}
SECRET=re.compile(rb'(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{30,})')
PROFILE_FILE='.github/repository-profile.json'
STUDY_ROOTS=('ecosystem/data/studium/', 'ecosystem/data/training/', 'ecosystem/data/methods/',
             'knowledge_pipeline/data/justin_sung/incoming/',
             'knowledge_pipeline/data/justin_sung/archive/',
             'knowledge_pipeline/data/justin_sung/synthesis/')
STUDY_FILES={'knowledge_pipeline/config/baseline.json'}
RUNTIME_NAMES={'.lock', 'access_state.json', 'pending.json'}


def path_problem(name,profile='public'):
    if profile not in {'public','private'}:
        raise ValueError('Unknown repository profile')
    original=PurePosixPath(name)
    if (not original.parts or original.is_absolute() or '..' in original.parts):
        return 'unexpected repository path'
    path=original
    if path.parts[0] in {'knowledge_pipeline','ecosystem'}:
        path=PurePosixPath(*path.parts[1:])
    if not path.parts:
        return 'unexpected repository path'
    scoped_name=path.as_posix()
    if (any(part in {'.git','.venv','venv','__pycache__','.agents','.aws','.codex'} for part in original.parts)
        or any(part.startswith('.env') for part in original.parts)
        or path.name in RUNTIME_NAMES
        or path.suffix in {'.log','.lock','.pyc','.pem','.key','.pfx','.tmp'}):
        return 'local data/configuration'
    if profile=='private' and (name.startswith(STUDY_ROOTS) or name in STUDY_FILES):
        return None
    if path.parts[0] in PRIVATE_ROOTS or scoped_name in PRIVATE_FILES:
        return 'local data/configuration'
    if path.parts[0] not in PUBLIC_ROOTS and scoped_name not in PUBLIC_FILES:
        return 'unexpected repository path'
    return None


def violations(paths,reader,*,profile='public'):
    problems=[]
    for name in paths:
        reason=path_problem(name,profile)
        if reason:
            problems.append((name,reason))
            continue
        if SECRET.search(reader(name)):
            problems.append((name,'credential-like content'))
    return problems


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    component=Path(__file__).resolve().parents[1]
    default_root=(component.parent if (component.parent/'.github/workflows/tests.yml').is_file()
                  else component)
    parser.add_argument('--root',type=Path,default=default_root)
    parser.add_argument('--git-dir',type=Path,help='Optional isolated Git index for a read-only publishing preview.')
    parser.add_argument('--profile',choices=['public','private'],
                        help='Expected visibility; defaults to the staged profile, then public. CI supplies actual visibility.')
    args=parser.parse_args(argv)
    command=['git']
    if args.git_dir:command+=['--git-dir='+str(args.git_dir),'--work-tree='+str(args.root)]
    result=subprocess.run(command+['ls-files','-z'],cwd=args.root,capture_output=True,check=False)
    if result.returncode:
        print('Git metadata is unavailable. Initialize Git before checking the staged file list.')
        return 1
    paths=[p for p in result.stdout.decode().split('\0') if p]
    def read_staged(name):
        return subprocess.run(command+['show',':'+name],cwd=args.root,capture_output=True,check=True).stdout
    declared=None
    if PROFILE_FILE in paths:
        try:
            metadata=json.loads(read_staged(PROFILE_FILE))
            if metadata.get('schema_version')!=1 or metadata.get('profile') not in {'public','private'}:
                raise ValueError('Unsupported repository profile')
            declared=metadata['profile']
        except (ValueError,AttributeError) as error:
            print(f'REJECT: {PROFILE_FILE}: invalid repository profile ({error})')
            return 1
    if args.profile and declared and args.profile!=declared:
        print(f'REJECT: {PROFILE_FILE}: declared {declared}, expected {args.profile}')
        return 1
    profile=args.profile or declared or 'public'
    problems=violations(paths,read_staged,profile=profile)
    for name,reason in problems:print(f'REJECT: {name}: {reason}')
    print(f'Profile: {profile}. Checked {len(paths)} tracked/staged files; rejected {len(problems)}.')
    return 1 if problems else 0


if __name__=='__main__':
    raise SystemExit(main())
