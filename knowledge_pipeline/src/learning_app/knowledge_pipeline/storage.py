"""Durable file operations for knowledge synthesis and editor artifacts."""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .errors import SynthesisError


def digest(data):
    return hashlib.sha256(data).hexdigest()

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def safe(path):
    if not path.is_absolute() or path.resolve() != path:
        raise SynthesisError(f'Unexpected symlink or relative path: {path}')

def atomic(path, data):
    safe(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.'+path.name, delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)

def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8')

def write_json(path, value):
    atomic(path, json_bytes(value))

def document_bytes(text):
    return ('\ufeff'+text.replace('\r\n','\n').replace('\n','\r\n')).encode('utf-8')
