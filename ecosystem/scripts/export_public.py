"""Export committed source into an independent public checkout, without study history."""

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    'repository_check', ROOT / 'knowledge_pipeline/scripts/check_repository.py')
repository_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repository_check)

PUBLIC_IGNORES = {
    '.gitignore': '\n# Personal data stays outside the public source repository.\n'
                  '/ecosystem/data/\n/knowledge_pipeline/data/\n',
    'knowledge_pipeline/.gitignore': '\n# Public source repository.\n/data/\n/config/baseline.json\n',
    'ecosystem/.gitignore': '\n# Public source repository.\n/data/\n',
}


def git(root, *arguments):
    result = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace').strip() or 'Git command failed')
    return result.stdout


def tree(root, revision):
    entries = {}
    for record in git(root, 'ls-tree', '-r', '-z', '--full-tree', revision).split(b'\0'):
        if not record:
            continue
        metadata, name = record.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        entries[name.decode()] = (mode, kind, oid)
    return entries


def audit_public_history(root):
    """Reject a destination that inherited private commits or credential-like blobs."""
    blobs = {}
    for revision in git(root, 'rev-list', '--all').decode().splitlines():
        entries = tree(root, revision)
        for name, (mode, kind, oid) in entries.items():
            reason = repository_check.path_problem(name)
            if reason:
                raise ValueError(f'Destination history contains a non-public path: {name}')
            if kind != 'blob' or mode not in {'100644', '100755'}:
                raise ValueError(f'Unsupported public file: {name}')
            if oid not in blobs:
                blobs[oid] = repository_check.SECRET.search(git(root, 'cat-file', 'blob', oid)) is not None
            if blobs[oid]:
                raise ValueError(f'Destination history contains credential-like content: {name}')


def export_public(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('Public checkout must be outside the private checkout')
    if git(source, 'rev-parse', '--show-toplevel').decode().strip() != str(source):
        raise ValueError('Source must be the repository root')
    if git(source, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Commit source changes before exporting; only committed files are exported')
    revision = git(source, 'rev-parse', 'HEAD').decode().strip()
    entries = tree(source, revision)
    files = {}
    for name, (mode, kind, oid) in entries.items():
        if repository_check.path_problem(name):
            continue
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError(f'Unsupported public file: {name}')
        files[name] = (mode, git(source, 'cat-file', 'blob', oid))
    required = {'ecosystem.py', '.gitignore', 'knowledge_pipeline/.gitignore',
                'knowledge_pipeline/scripts/check_repository.py', '.github/workflows/tests.yml'}
    if not required.issubset(files):
        raise ValueError('Source is missing required project files')
    files[repository_check.PROFILE_FILE] = (
        '100644', b'{\n  "schema_version": 1,\n  "profile": "public"\n}\n')
    for name, suffix in PUBLIC_IGNORES.items():
        if name in files:
            mode, content = files[name]
            addition = suffix.encode()
            if not content.endswith(addition):
                files[name] = (mode, content + addition)
    problems = repository_check.violations(files, lambda name: files[name][1])
    if problems:
        raise ValueError('Public export rejected: ' + '; '.join(f'{name}: {reason}' for name, reason in problems))

    previous = set()
    if destination.exists() and any(destination.iterdir()):
        if git(destination, 'rev-parse', '--show-toplevel').decode().strip() != str(destination):
            raise ValueError('Destination must be an independent public Git checkout')
        if git(destination, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('Public checkout has uncommitted files; commit or resolve them first')
        profile = json.loads(git(destination, 'show', f'HEAD:{repository_check.PROFILE_FILE}'))
        if profile != {'schema_version': 1, 'profile': 'public'}:
            raise ValueError('Destination must declare the public repository profile')
        audit_public_history(destination)
        previous = set(tree(destination, 'HEAD'))
    else:
        destination.mkdir(parents=True, exist_ok=True)
        git(destination, 'init', '--quiet', '--initial-branch=main')

    for name in files:
        path = destination / name
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError(f'Symlink in destination path: {name}')

    # Only formerly tracked public files may be removed; ignored local files stay local.
    for name in previous - files.keys():
        (destination / name).unlink()
    for name, (mode, content) in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(int(mode, 8) & 0o777)
    git(destination, 'add', '--all', '--', '.')
    check = subprocess.run([
        sys.executable, '-B', str(destination / 'knowledge_pipeline/scripts/check_repository.py'),
        '--root', str(destination), '--profile', 'public'], capture_output=True, text=True)
    if check.returncode:
        raise ValueError('Staged public export failed validation: ' + check.stdout + check.stderr)
    return dict(source_commit=revision, public_files=len(files),
                excluded_files=len(entries) - len(files), destination=str(destination),
                status='staged', committed=False, pushed=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = export_public(args.root, args.destination)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
