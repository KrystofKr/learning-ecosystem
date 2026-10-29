#!/usr/bin/env python3
"""Append-only coaching notes beside StudyStore, without changing learning results."""

import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'study-progress' / 'scripts'))
from study_store import StudyStore, StudyError, atomic_json, day, identifier, now, text


KINDS = {'goal', 'plan', 'hypothesis', 'experiment', 'reflection', 'attempt-context'}


def integer(value, label):
    if type(value) is not int or value < 0:
        raise StudyError(f'{label} must be a nonnegative integer')
    return value


def timestamp(value):
    if not isinstance(value, str):
        raise StudyError('Timestamp must be ISO 8601 text with a timezone')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise StudyError('Invalid timestamp') from exc
    if parsed.tzinfo is None:
        raise StudyError('Timestamp requires a timezone')


def effective(records):
    seen, replaced = {}, set()
    for record in records:
        record_id = identifier(record.get('record_id'))
        if record_id in seen:
            raise StudyError('Duplicate coaching record ID')
        if 'replaces' in record:
            previous = record['replaces']
            if previous not in seen or previous in replaced or seen[previous]['kind'] != record['kind']:
                raise StudyError('replaces must refer to a current record of the same kind')
            if record['kind'] == 'attempt-context' and seen[previous]['payload']['checkpoint_id'] != record['payload']['checkpoint_id']:
                raise StudyError('A context revision must retain its original activity')
            replaced.add(previous)
        seen[record_id] = record
    return [record for record in records if record['record_id'] not in replaced]


class CoachingStore:
    def __init__(self, root):
        self.study = StudyStore(root)

    def load(self, subject):
        self.study.load(subject)
        path = self.study.directory(subject) / 'coaching.json'
        if path.is_symlink():
            raise StudyError('Coaching records cannot be symlinks')
        if not path.exists():
            return dict(schema_version=1, subject_id=subject, records=[])
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
                or value['schema_version'] != 1 or value.get('subject_id') != subject
                or not isinstance(value.get('records'), list)):
            raise StudyError('Invalid coaching schema or subject identity')
        for record in value['records']:
            if not isinstance(record, dict) or record.get('subject_id') != subject or record.get('kind') not in KINDS:
                raise StudyError('Invalid coaching record identity')
        effective(value['records'])
        return value

    def validate(self, subject, record, state):
        required = {'subject_id', 'record_id', 'kind', 'payload', 'checkpoint_ids', 'method_refs'}
        if not isinstance(record, dict) or not required.issubset(record) or set(record) - required - {'replaces', 'observations'}:
            raise StudyError('Coaching record has missing or unknown fields')
        if record['subject_id'] != subject or record['kind'] not in KINDS:
            raise StudyError('Wrong subject or coaching kind')
        identifier(record['record_id'])
        if 'replaces' in record:
            identifier(record['replaces'])
        if not isinstance(record['checkpoint_ids'], list):
            raise StudyError('checkpoint_ids must be a list')
        checkpoints = {item['checkpoint_id']: item for item in state['checkpoints']}
        for item in record['checkpoint_ids']:
            if identifier(item) not in checkpoints:
                raise StudyError('Evidence checkpoint does not exist in this subject')
        observations = record.get('observations', [])
        if not isinstance(observations, list):
            raise StudyError('observations must be a list')
        for observation in observations:
            text(observation, 'observation')
        if not isinstance(record['method_refs'], list):
            raise StudyError('method_refs must be a list')
        for reference in record['method_refs']:
            if not isinstance(reference, dict) or set(reference) != {'document_sha256', 'section', 'line_start', 'line_end'}:
                raise StudyError('Method reference needs document hash, section and line range')
            if not isinstance(reference['document_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', reference['document_sha256']):
                raise StudyError('Invalid method document hash')
            if not isinstance(reference['section'], str) or not re.fullmatch(r'[1-9][0-9]?(?:\.[1-9][0-9]?)?', reference['section']):
                raise StudyError('Invalid method section')
            if integer(reference['line_start'], 'line_start') < 1 or integer(reference['line_end'], 'line_end') < reference['line_start']:
                raise StudyError('Invalid method line range')
        payload = record['payload']
        if not isinstance(payload, dict):
            raise StudyError('payload must be an object')
        mandatory = {
            'goal': {'outcome', 'criterion', 'scope'},
            'plan': {'period_start', 'capacity_minutes', 'reserve_minutes', 'blocks'},
            'hypothesis': {'claim', 'alternative', 'next_test'},
            'experiment': {'change', 'expected_effect', 'measure', 'comparison', 'status'},
            'reflection': {'what', 'how', 'why', 'next_step'},
            'attempt-context': {'checkpoint_id', 'task_mode', 'assistance', 'solution_seen'},
        }[record['kind']]
        optional = {
            'goal': {'deadline'}, 'plan': set(), 'hypothesis': set(), 'experiment': {'result'},
            'reflection': set(),
            'attempt-context': {'occurred_at', 'confidence', 'confidence_timing', 'review_of', 'exposure_at', 'duration_minutes'},
        }[record['kind']]
        if not mandatory.issubset(payload) or set(payload) - mandatory - optional:
            raise StudyError('Payload has missing or unknown fields')
        for key in mandatory - {'scope', 'capacity_minutes', 'reserve_minutes', 'blocks', 'solution_seen'}:
            text(payload[key], key)
        kind = record['kind']
        if kind == 'goal':
            if not isinstance(payload['scope'], list) or not payload['scope']:
                raise StudyError('Goal scope must be a nonempty list')
            for item in payload['scope']:
                text(item, 'scope')
            if payload.get('deadline') is not None:
                day(payload['deadline'])
        elif kind == 'plan':
            day(payload['period_start'])
            capacity = integer(payload['capacity_minutes'], 'capacity_minutes')
            reserve = integer(payload['reserve_minutes'], 'reserve_minutes')
            if not isinstance(payload['blocks'], list):
                raise StudyError('blocks must be a list')
            total = reserve
            for block in payload['blocks']:
                if not isinstance(block, dict) or set(block) != {'topic_id', 'output', 'min_minutes', 'max_minutes', 'estimate_basis'}:
                    raise StudyError('Invalid plan block')
                self.study.get(state, 'topics', block['topic_id'])
                text(block['output'], 'output')
                text(block['estimate_basis'], 'estimate_basis')
                lower = integer(block['min_minutes'], 'min_minutes')
                upper = integer(block['max_minutes'], 'max_minutes')
                if upper < lower:
                    raise StudyError('Invalid estimate range')
                total += upper
            if total > capacity:
                raise StudyError('Plan exceeds capacity including reserve; prioritize or reduce scope')
        elif kind == 'experiment':
            if payload['status'] not in {'active', 'completed', 'abandoned'}:
                raise StudyError('Invalid experiment status')
            if payload['status'] == 'completed' or 'result' in payload:
                text(payload.get('result'), 'result')
        elif kind == 'attempt-context':
            activity = checkpoints.get(identifier(payload['checkpoint_id']))
            if activity is None or 'correction_of' in activity:
                raise StudyError('Context must refer to the original activity, not a correction')
            if payload['task_mode'] not in {'recognition', 'recall', 'explain', 'apply', 'transfer'}:
                raise StudyError('Invalid task_mode')
            if payload['assistance'] not in {'none', 'hint', 'solution', 'unknown'}:
                raise StudyError('Invalid assistance')
            if payload['solution_seen'] is not None and type(payload['solution_seen']) is not bool:
                raise StudyError('solution_seen must be boolean or null for unknown')
            if payload['assistance'] == 'none' and (activity['hints_used'] > 0 or payload['solution_seen'] is True):
                raise StudyError('Known hints or a seen solution cannot establish unassisted performance')
            for field in ('occurred_at', 'exposure_at'):
                if payload.get(field) is not None:
                    timestamp(payload[field])
            if payload.get('confidence') is not None:
                value = payload['confidence']
                if type(value) not in (int, float) or not 0 <= value <= 1 or payload.get('confidence_timing') != 'before-feedback':
                    raise StudyError('Confidence must be 0..1 and recorded before feedback')
            elif 'confidence_timing' in payload:
                raise StudyError('confidence_timing requires a confidence value')
            if payload.get('review_of') is not None:
                previous = checkpoints.get(identifier(payload['review_of']))
                if previous is None or 'correction_of' in previous or previous['checkpoint_id'] == activity['checkpoint_id']:
                    raise StudyError('review_of must refer to another original activity in this subject')
            if payload.get('duration_minutes') is not None:
                integer(payload['duration_minutes'], 'duration_minutes')

    def put(self, subject, record):
        directory = self.study.directory(subject)
        if not directory.is_dir():
            raise StudyError('Subject must already exist in StudyStore')
        with (directory / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self.study.load(subject)
            self.validate(subject, record, state)
            value = self.load(subject)
            for previous in value['records']:
                if previous['record_id'] == record['record_id']:
                    if {key: item for key, item in previous.items() if key != 'created_at'} != record:
                        raise StudyError('Coaching ID already has different content')
                    return dict(record_id=record['record_id'], duplicate=True)
            candidate = value['records'] + [dict(record, created_at=now())]
            current = effective(candidate)
            active = [item for item in current if item['kind'] == 'experiment' and item['payload']['status'] == 'active']
            if len(active) > 1:
                raise StudyError('Finish or revise the current experiment before starting another')
            contexts = [item['payload']['checkpoint_id'] for item in current if item['kind'] == 'attempt-context']
            if len(contexts) != len(set(contexts)):
                raise StudyError('Use replaces to revise the context of an existing activity')
            value['records'] = candidate
            atomic_json(directory / 'coaching.json', value)
            return dict(record_id=record['record_id'], duplicate=False)

    def view(self, subject, kind=None, history=False):
        value = self.load(subject)
        records = value['records'] if history else effective(value['records'])
        records = [item for item in records if kind is None or item['kind'] == kind]
        return dict(subject_id=subject, total=len(records), records=records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--subject', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    put = commands.add_parser('put')
    put.add_argument('--input', required=True)
    view = commands.add_parser('view')
    view.add_argument('--kind', choices=sorted(KINDS))
    view.add_argument('--history', action='store_true')
    args = parser.parse_args(argv)
    try:
        store = CoachingStore(args.root)
        if args.command == 'put':
            result = store.put(args.subject, json.loads(Path(args.input).read_text(encoding='utf-8')))
        else:
            result = store.view(args.subject, args.kind, args.history)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'Coaching operation failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
