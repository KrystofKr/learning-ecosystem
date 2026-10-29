"""Publish immutable curriculum snapshots after a committed synthesis update."""
import difflib
import json
from pathlib import Path
import re

from .workspace import Workspace, EcosystemError, atomic_bytes, atomic_json, digest, load_json, locked, now


def sections(text):
    lines = text.splitlines()
    headings = []
    for i, line in enumerate(lines[:-1]):
        match = re.fullmatch(r'([1-9][0-9]?(?:\.[1-9][0-9]?)?)\.?(?: )(.+)', line)
        divider = lines[i + 1].strip()
        if match and len(divider) >= 8 and set(divider) in ({'='}, {'-'}):
            headings.append((match[1], match[2], i + 1))
    if not headings or len({item[0] for item in headings}) != len(headings):
        raise EcosystemError('Missing or ambiguous synthesis structure')
    result = {}
    for i, (section, title, start) in enumerate(headings):
        chapter = section.split('.')[0]
        later = [item[2] for item in headings[i + 1:]
                 if '.' in section or item[0].split('.')[0] != chapter]
        end = later[0] - 1 if later else len(lines)
        body = '\n'.join(lines[start - 1:end]).strip()
        result[section] = dict(section=section, title=title, line_start=start, line_end=end,
                               text=body, content_sha256=digest(body.encode()))
    return result


def validate_registry(registry):
    if not isinstance(registry, dict) or registry.get('schema_version') != 1 or not isinstance(registry.get('techniques'), list):
        raise EcosystemError('Invalid curriculum schema')
    found = set()
    for item in registry['techniques']:
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', item.get('id', '')) or item['id'] in found:
            raise EcosystemError('Invalid or duplicate technique identity')
        found.add(item['id'])
        for field in ('title', 'purpose', 'practice_task'):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise EcosystemError(f'Missing technique {field}')
        if not isinstance(item.get('sections'), list) or not item['sections']:
            raise EcosystemError('Technique requires source sections')
        if not isinstance(item.get('rubric'), dict) or len(item['rubric']) < 2 or not all(isinstance(value, str) and value.strip() for value in item['rubric'].values()):
            raise EcosystemError('Technique requires observable criteria')
    graph = {item['id']: item.get('prerequisites', []) for item in registry['techniques']}
    visited, active = set(), set()
    def visit(name):
        if name not in graph or name in active:
            raise EcosystemError('Missing or cyclic technique prerequisite')
        if name in visited:
            return
        active.add(name)
        for dependency in graph[name]:
            visit(dependency)
        active.remove(name)
        visited.add(name)
    for name in graph:
        visit(name)


def contains_content(passage):
    lines = passage['text'].splitlines()
    for index, line in enumerate(lines):
        value = line.strip()
        if not value or set(value) in ({'='}, {'-'}):
            continue
        following = lines[index + 1].strip() if index + 1 < len(lines) else ''
        if (re.match(r'^[1-9][0-9]?(?:\.[1-9][0-9]?)?\.? ', value)
                and len(following) >= 8 and set(following) in ({'='}, {'-'})):
            continue
        return True
    return False


def synchronize(root, *, synthesis=None):
    workspace = Workspace(root)
    source = Path(synthesis).resolve() if synthesis else workspace.synthesis
    state_dir = source.parent / 'state'
    with locked(workspace.data / '.sync.lock'):
        with locked(state_dir / '.lock', shared=True):
            if (state_dir / 'pending.json').exists():
                raise EcosystemError('Synthesis transaction is incomplete; retaining the last published methods')
            raw = source.read_bytes()
            source_hash = digest(raw)
            state = load_json(state_dir / 'state.json')
            if state.get('synthesis_sha256') != source_hash:
                raise EcosystemError('Synthesis does not match its committed state')
            registry = load_json(workspace.registry)
            validate_registry(registry)
            document = sections(raw.decode('utf-8-sig').replace('\r\n', '\n'))
        compiler_hash = digest(Path(__file__).read_bytes())
        version = digest(dict(source=source_hash, registry=digest(registry), compiler=compiler_hash))
        pointer = workspace.methods / 'current.json'
        old = workspace.current() if pointer.exists() else None
        if old and old['version'] == version:
            return dict(status='current', version=version, changed_techniques=[])
        old_methods = {item['id']: item for item in old['techniques']} if old else {}
        techniques = []
        for definition in registry['techniques']:
            missing = set(definition['sections']) - set(document)
            if missing and old_methods.get(definition['id'], {}).get('available', True) and definition['id'] in old_methods:
                raise EcosystemError(f'Method source missing: {definition["id"]}: {sorted(missing)}')
            passages = [document[section] for section in definition['sections'] if section in document]
            available = not missing and all(contains_content(passage) for passage in passages)
            revision = digest(dict(definition=definition,
                content=[item['content_sha256'] for item in passages], missing=sorted(missing)))
            techniques.append(dict(definition, revision=revision,
                available=available, missing_sections=sorted(missing),
                source_refs=[dict(document_sha256=source_hash, section=item['section'],
                                  line_start=item['line_start'], line_end=item['line_end']) for item in passages]))
        changed = [item['id'] for item in techniques
                   if item['id'] not in old_methods or old_methods[item['id']]['revision'] != item['revision']]
        removed = sorted(set(old_methods) - {item['id'] for item in techniques})
        covered = {section.split('.')[0] for item in techniques for section in item['sections']}
        unmapped = [key for key in document if '.' not in key and key not in covered and key not in {'23', '24'}]
        catalog = dict(schema_version=1, version=version, document_sha256=source_hash,
                       registry_sha256=digest(registry), compiler_sha256=compiler_hash, created_at=now(),
                       techniques=techniques, sections=document,
                       changes=dict(previous_version=old['version'] if old else None,
                                    changed_techniques=changed, removed_techniques=removed,
                                    unmapped_chapters=unmapped))
        destination = workspace.methods / 'versions' / version
        destination.mkdir(parents=True, exist_ok=True)
        snapshot = destination / 'synthesis.txt'
        changes = catalog['changes']
        catalog_path = destination / 'catalog.json'
        if catalog_path.exists():
            existing = load_json(catalog_path)
            immutable_fields = ('schema_version', 'version', 'document_sha256', 'registry_sha256',
                                'compiler_sha256', 'techniques', 'sections')
            if (not snapshot.is_file() or snapshot.read_bytes() != raw
                    or any(existing.get(key) != catalog[key] for key in immutable_fields)):
                raise EcosystemError('Published snapshot collision or corruption; existing files retained')
            catalog = existing
        else:
            # An unsealed draft is safe to rebuild after interruption. The catalog
            # seals it only after every supporting file is durable.
            atomic_bytes(snapshot, raw)
            atomic_json(destination / 'changes.json', changes)
            if old:
                previous = (workspace.methods / 'versions' / old['version'] / 'synthesis.txt').read_text(encoding='utf-8-sig')
                difference = ''.join(difflib.unified_diff(previous.splitlines(keepends=True),
                                   raw.decode('utf-8-sig').splitlines(keepends=True),
                                   fromfile=old['version'], tofile=version))
                atomic_bytes(destination / 'source.diff', difference.encode('utf-8'))
            atomic_json(catalog_path, catalog)
        atomic_json(pointer, dict(schema_version=1, version=version,
                                 document_sha256=source_hash, registry_sha256=digest(registry),
                                 compiler_sha256=compiler_hash,
                                 changes=changes,
                                 catalog_sha256=digest(catalog), published_at=now()))
        return dict(status='updated', version=version, changed_techniques=changed,
                    removed_techniques=removed, unmapped_chapters=unmapped)
