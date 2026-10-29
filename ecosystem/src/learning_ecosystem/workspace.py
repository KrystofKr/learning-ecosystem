"""Project-relative paths and atomic local storage; no model or network dependency."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile


class EcosystemError(ValueError):
    pass


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def atomic_bytes(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


@contextmanager
def locked(path, *, shared=False):
    path = Path(path)
    if not path.parent.exists():
        path.parent.mkdir(parents=True)
    with path.open('rb' if shared and path.is_file() else 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.component = self.root / 'ecosystem'
        self.data = self.component / 'data'
        self.study = self.data / 'studium'
        self.methods = self.data / 'methods'
        self.training = self.data / 'training'
        self.skills = self.root / 'skills'
        self.synthesis = self.root / 'knowledge_pipeline/data/justin_sung/synthesis/main.txt'
        self.registry = self.component / 'config/techniques.json'

    def module(self, skill, filename):
        path = self.skills / skill / 'scripts' / filename
        spec = importlib.util.spec_from_file_location(filename.removesuffix('.py'), path)
        if spec is None or spec.loader is None:
            raise EcosystemError(f'Missing helper: {path}')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def store(self):
        return self.module('study-progress', 'study_store.py').StudyStore(self.study)

    def current(self):
        pointer = self.methods / 'current.json'
        if not pointer.is_file():
            raise EcosystemError('Method library is not initialized. Run ecosystem.py sync.')
        current = load_json(pointer)
        if current.get('schema_version') != 1 or not isinstance(current.get('version'), str):
            raise EcosystemError('Unsupported method pointer')
        version = current['version']
        if len(version) != 64 or any(c not in 'abcdef0123456789' for c in version):
            raise EcosystemError('Invalid method version')
        directory = self.methods / 'versions' / version
        catalog = load_json(directory / 'catalog.json')
        if digest((directory / 'synthesis.txt').read_bytes()) != catalog['document_sha256']:
            raise EcosystemError('The published method snapshot is damaged')
        if digest(catalog) != current['catalog_sha256']:
            raise EcosystemError('The published method catalog is damaged')
        # A content version stays immutable even when it is published again.
        # Transition metadata belongs to the current publication, not its files.
        return dict(catalog, changes=current.get('changes', catalog['changes']))
