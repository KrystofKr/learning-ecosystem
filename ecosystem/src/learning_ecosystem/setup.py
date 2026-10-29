"""Install the relocatable local runtime and import the existing study library."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig
import tempfile
import uuid

from .workspace import Workspace, EcosystemError, atomic_json, digest, load_json, locked, now


def copy_auxiliary(source, destination, evidence):
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if relative.parts[0] == 'subjects' or '__pycache__' in relative.parts or path.name == '.lock':
            continue
        if path.is_symlink():
            raise EcosystemError('Auxiliary library files cannot be symlinks')
        if not path.is_file():
            continue
        target = destination / relative
        raw = path.read_bytes()
        if target.exists() and target.read_bytes() != raw:
            raise EcosystemError('Conflicting auxiliary library file: ' + str(relative))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(raw)
        evidence[str(relative)] = digest(raw)


def import_library(root, source):
    workspace = Workspace(root)
    source = Path(source).resolve()
    if source == workspace.study.resolve():
        return dict(status='already-local', subjects=len(workspace.store().subjects()))
    if not (source / 'subjects').is_dir():
        raise EcosystemError('Source is not a StudyStore library')
    store_module = workspace.module('study-progress', 'study_store.py')
    original = store_module.StudyStore(source)
    subjects = original.subjects()
    if not subjects:
        raise EcosystemError('No subjects to import')
    workspace.data.mkdir(parents=True, exist_ok=True)
    with locked(workspace.data / '.import.lock'):
        manifest_path = workspace.data / 'library-import.json'
        if manifest_path.exists():
            manifest = load_json(manifest_path)
            if manifest.get('source') != str(source):
                raise EcosystemError('A different library has already been imported')
            copy_auxiliary(source, workspace.study, manifest['files'])
            atomic_json(manifest_path, manifest)
            return dict(status='already-imported', subjects=len(manifest['subjects']))
        if workspace.study.exists() and any(workspace.study.iterdir()):
            raise EcosystemError('Local library is not empty; refusing to merge or replace personal results')
        temporary = workspace.data / ('.library-import-' + uuid.uuid4().hex)
        temporary.mkdir()
        evidence = {}
        try:
            for summary in subjects:
                subject = summary['subject_id']
                directory = original.directory(subject)
                # Reuse the authoritative subject lock while copying its consistent snapshot.
                with locked(directory / '.lock', shared=True):
                    state = original.load(subject)
                    for item in state['sources'].values():
                        original.verify_source(subject, item)
                    destination = temporary / 'subjects' / subject
                    for path in sorted(directory.rglob('*')):
                        if path.is_symlink():
                            raise EcosystemError('Study library contains a symlink; import it explicitly')
                        if path.is_file() and path.name != '.lock':
                            relative = path.relative_to(directory)
                            target = destination / relative
                            target.parent.mkdir(parents=True, exist_ok=True)
                            raw = path.read_bytes()
                            target.write_bytes(raw)
                            evidence[str(Path('subjects') / subject / relative)] = digest(raw)
            copy_auxiliary(source, temporary, evidence)
            copied = store_module.StudyStore(temporary)
            for item in copied.subjects():
                state = copied.load(item['subject_id'])
                for asset in state['sources'].values():
                    copied.verify_source(item['subject_id'], asset)
            if workspace.study.exists():
                workspace.study.rmdir()
            temporary.rename(workspace.study)
            atomic_json(manifest_path, dict(schema_version=1, source=str(source),
                        imported_at=now(), subjects=[item['subject_id'] for item in subjects], files=evidence,
                        mode='verified-snapshot', source_removal='not-performed'))
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return dict(status='imported', subjects=len(subjects), files=len(evidence),
                    study_root=str(workspace.study))


def repair_runtime_paths(root, previous_root, *, environment=None):
    """Rebase installed launchers after a move, preserving packages and file modes."""
    workspace = Workspace(root)
    old = str(Path(previous_root).absolute())
    new = str(workspace.root)
    if old == new:
        return dict(changed=[])
    environment = Path(environment) if environment is not None else workspace.root / 'knowledge_pipeline/.venv'
    scripts = environment / 'bin'
    candidates = [environment / 'pyvenv.cfg']
    if scripts.is_dir():
        candidates.extend(path for path in scripts.iterdir() if path.is_file() and not path.is_symlink())
    for site in (environment / 'lib').glob('python*/site-packages'):
        candidates.extend(site.glob('*.pth'))
        candidates.extend(site.glob('*.dist-info/direct_url.json'))
    changed = []
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        raw = path.read_bytes()
        if path.parent == scripts and not (raw.startswith(b'#!') or path.name.startswith('activate')):
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            continue
        updated = text.replace(old + '/', new + '/')
        updated = updated.replace(Path(old).as_uri() + '/', workspace.root.as_uri() + '/')
        if updated == text:
            continue
        descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        changed.append(str(path.relative_to(workspace.root)))
    return dict(changed=changed)


def registration_status(workspace):
    registered = all((Path.home()/'.codex/skills'/name).is_symlink()
                     and (Path.home()/'.codex/skills'/name).resolve() == (workspace.skills/name).resolve()
                     for name in ('study-library', 'study-progress', 'study-tutor', 'study-coach'))
    return 'registered' if registered else 'pending-finalize'


def install_runtime(root):
    workspace = Workspace(root)
    expected = workspace.root / 'knowledge_pipeline/.venv'
    if Path(sys.prefix).resolve() != expected.resolve():
        raise EcosystemError('Run installation using knowledge_pipeline/.venv/bin/python')
    site = Path(sysconfig.get_paths()['purelib'])
    # Resolve paths from the environment on every interpreter start, including after rename.
    code = "import sys; from pathlib import Path; sys.path[:0] = [str(Path(sys.prefix).resolve().parents[1] / 'ecosystem/src'), str(Path(sys.prefix).resolve().parents[1] / 'knowledge_pipeline/src')]\n"
    (site / 'learning_ecosystem.pth').write_text(code)
    executable = Path(sys.prefix) / 'bin/learning-ecosystem'
    executable.write_text('#!' + str(Path(sys.executable).absolute()) + '\n'
        'from pathlib import Path\nimport sys\n'
        "root = Path(sys.prefix).resolve().parents[1]\n"
        "sys.path[:0] = [str(root / 'ecosystem/src'), str(root / 'knowledge_pipeline/src')]\n"
        'from learning_ecosystem.cli import main\nraise SystemExit(main(root=root))\n')
    executable.chmod(0o755)
    alias = workspace.root / '.venv'
    if not alias.exists() and not alias.is_symlink():
        alias.symlink_to('knowledge_pipeline/.venv', target_is_directory=True)
    registration = registration_status(workspace)
    atomic_json(workspace.data / 'installation.json', dict(schema_version=1,
                installed_at=now(), mode='project-local', skills=['study-library', 'study-progress', 'study-tutor', 'study-coach'],
                routing='AGENTS.md', runtime_relative='knowledge_pipeline/.venv',
                global_registration=registration))
    return dict(status='installed', executable=str(executable), skills='project-local',
                global_registration=registration)


def doctor(root):
    workspace = Workspace(root)
    result = dict(project=str(workspace.root), python=sys.version.split()[0],
        runtime_installed=(Path(sys.prefix).resolve() == (workspace.root / 'knowledge_pipeline/.venv').resolve()
                           and (Path(sysconfig.get_paths()['purelib']) / 'learning_ecosystem.pth').is_file()),
        skills={name: (workspace.skills / name / 'SKILL.md').is_file()
                for name in ('study-library', 'study-progress', 'study-tutor', 'study-coach')},
        source_available=workspace.synthesis.is_file(),
        pdf_text=bool(importlib.util.find_spec('pypdf')) or bool(shutil.which('pdftotext')),
        pdf_visual=bool(importlib.util.find_spec('pypdfium2')) and bool(importlib.util.find_spec('PIL')),
        background_study_notifications=False)
    converter = workspace.root / 'knowledge_pipeline/.venv/bin/soffice'
    result['ppt_converter_installed'] = converter.is_file()
    result['independent_runtime'] = (not converter.is_symlink()
                                     or converter.resolve().is_relative_to(workspace.root / 'knowledge_pipeline/.venv'))
    result['coursework_return_pending'] = (workspace.root / 'projects').exists() or (workspace.root / 'projects').is_symlink()
    try:
        catalog = workspace.current()
        result.update(method_version=catalog['version'], techniques=len(catalog['techniques']),
                      available_techniques=sum(item.get('available', True) for item in catalog['techniques']),
                      source_current=digest(workspace.synthesis.read_bytes()) == catalog['document_sha256'],
                      registry_current=digest(load_json(workspace.registry)) == catalog['registry_sha256'],
                      compiler_current=digest((workspace.root/'ecosystem/src/learning_ecosystem/sync.py').read_bytes()) == catalog.get('compiler_sha256'),
                      source_transaction_pending=(workspace.synthesis.parent/'state/pending.json').exists())
    except (OSError, ValueError) as error:
        result['method_error'] = str(error)
    try:
        result['subjects'] = len(workspace.store().subjects())
    except (OSError, ValueError) as error:
        result['study_error'] = str(error)
    if (workspace.data/'installation.json').is_file():
        result['installation'] = load_json(workspace.data/'installation.json')['mode']
    result['global_registration'] = registration_status(workspace)
    access_path = workspace.root / 'knowledge_pipeline/data/justin_sung/incoming/access_state.json'
    try:
        access = load_json(access_path) if access_path.is_file() else {}
        result['automation_access_last_check'] = {key: access.get(key) for key in
            ('status', 'block_reason', 'checked_at_utc') if key in access}
    except (OSError, ValueError, TypeError) as error:
        result['automation_access_error'] = str(error)
    result['ready_scope'] = 'local-study'
    result['ready'] = (all(result['skills'].values()) and result.get('runtime_installed', False)
                       and result.get('source_current', False) and result.get('registry_current', False)
                       and result.get('compiler_current', False) and result.get('available_techniques', 0) > 0
                       and result['independent_runtime'] and not result['coursework_return_pending']
                       and not result.get('source_transaction_pending', True) and 'study_error' not in result)
    return result
