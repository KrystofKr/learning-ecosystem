"""Optional integration with the enclosing learning ecosystem; standalone stays independent."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from .errors import SynthesisError


def project(settings):
    root = settings.root.resolve().parent
    if (root / 'ecosystem.py').is_file() and (root / 'ecosystem/config/techniques.json').is_file():
        return root
    return None


def pending(settings):
    root = project(settings)
    if root is None:
        return False
    try:
        pointer = json.loads((root / 'ecosystem/data/methods/current.json').read_text())
        source = json.loads((settings.synthesis.parent / 'state/state.json').read_text())
        registry = json.loads((root / 'ecosystem/config/techniques.json').read_text())
        version = pointer.get('version') if isinstance(pointer, dict) else None
        if not isinstance(version, str) or not re.fullmatch(r'[a-f0-9]{64}', version):
            return True
        directory = root / 'ecosystem/data/methods/versions' / version
        catalog = json.loads((directory / 'catalog.json').read_text())
        catalog_bytes = json.dumps(catalog, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        if (pointer.get('catalog_sha256') != hashlib.sha256(catalog_bytes).hexdigest()
                or catalog.get('document_sha256') != hashlib.sha256((directory / 'synthesis.txt').read_bytes()).hexdigest()):
            return True
        raw = json.dumps(registry, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        return (pointer.get('document_sha256') != source['synthesis_sha256']
                or pointer.get('registry_sha256') != hashlib.sha256(raw).hexdigest()
                or pointer.get('compiler_sha256') != hashlib.sha256(
                    (root/'ecosystem/src/learning_ecosystem/sync.py').read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError):
        return True


def synchronize(settings):
    root = project(settings)
    if root is None:
        return None
    result = subprocess.run([sys.executable, '-B', str(root / 'ecosystem.py'), 'sync',
                             '--synthesis', str(settings.synthesis)],
                            cwd=root, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SynthesisError('Ecosystem synchronization failed; retry is required: ' + result.stderr.strip())
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise SynthesisError('Ecosystem synchronization returned an invalid result') from error
