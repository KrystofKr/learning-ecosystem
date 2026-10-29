"""Evidence-based technique progression; immutable lessons and corrected attempts."""
from datetime import datetime, timedelta, timezone
import re
import uuid

from .workspace import Workspace, EcosystemError, atomic_json, digest, load_json, locked, now


STAGES = ('orientation', 'guided', 'independent', 'transfer', 'retained')
ORIENTATION_RUBRIC = {
    'purpose': 'Vlastními slovy vysvětlí účel techniky a vhodnou situaci.',
    'mechanism': 'Vysvětlí konkrétní činnost a proč má pomoci, bez pouhého názvu rámce.',
    'boundary': 'Uvede omezení nebo situaci, kdy je potřeba jiný postup.',
}


def identifier(value):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', value):
        raise EcosystemError('Invalid identifier')
    return value


def moment(value):
    if not isinstance(value, str):
        raise EcosystemError('A real attempt timestamp with timezone is required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise EcosystemError('Attempt time requires a timezone')
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise EcosystemError('Attempt time cannot be in the future')
    return parsed


def effective(records):
    seen, replaced, roots, current = {}, set(), {}, {}
    for record in records:
        identity = identifier(record['attempt_id'])
        if identity in seen:
            raise EcosystemError('Duplicate training attempt ID')
        if 'correction_of' in record:
            previous = record['correction_of']
            if previous not in seen or previous in replaced:
                raise EcosystemError('Correction must refer to the effective earlier attempt')
            for field in ('lesson_id', 'answer', 'occurred_at'):
                if record[field] != seen[previous][field]:
                    raise EcosystemError(f'Correction must preserve {field}')
            replaced.add(previous)
            root = roots[previous]
        else:
            root = identity
        seen[identity] = record
        roots[identity] = root
        current[root] = record
    return list(current.values())


def validate_attempt(record, lesson=None):
    fields = {'attempt_id', 'lesson_id', 'answer', 'assessment', 'criteria', 'assistance',
              'solution_seen', 'occurred_at', 'review_notes', 'reviewer'}
    optional = {'correction_of', 'exposure_at', 'checkpoint_ids'}
    if not isinstance(record, dict) or not fields.issubset(record) or set(record) - fields - optional:
        raise EcosystemError('Training attempt has missing or unknown fields')
    identifier(record['attempt_id'])
    identifier(record['lesson_id'])
    if 'correction_of' in record:
        identifier(record['correction_of'])
    for field in ('answer', 'review_notes'):
        if not isinstance(record[field], str) or not record[field].strip():
            raise EcosystemError('An actual learner output and reasoned assessment are required')
    occurred = moment(record['occurred_at'])
    if record.get('exposure_at') is not None and moment(record['exposure_at']) > occurred:
        raise EcosystemError('Exposure cannot happen after the attempt')
    if record['assessment'] not in ('passed', 'failed', 'unassessed') or record['assistance'] not in ('none', 'hint', 'solution', 'unknown'):
        raise EcosystemError('Invalid assessment or assistance')
    if record['solution_seen'] is not None and type(record['solution_seen']) is not bool:
        raise EcosystemError('solution_seen must be boolean or null')
    if record['reviewer'] not in ('assistant', 'user', 'external'):
        raise EcosystemError('Assessment origin must be recorded')
    criteria = record['criteria']
    if not isinstance(criteria, dict) or not criteria:
        raise EcosystemError('Assessment requires observable criteria')
    if any(value is not None and type(value) is not bool for value in criteria.values()):
        raise EcosystemError('Criteria are true, false or null for unknown')
    if record['assessment'] == 'passed' and not all(value is True for value in criteria.values()):
        raise EcosystemError('Passed requires evidence for every criterion')
    if record['assessment'] == 'failed' and not any(value is False for value in criteria.values()):
        raise EcosystemError('Failure requires a specific failed criterion')
    if lesson is not None:
        if occurred < moment(lesson['issued_at']) - timedelta(minutes=5):
            raise EcosystemError('An attempt cannot predate the issued lesson')
        if set(criteria) != set(lesson['rubric']):
            raise EcosystemError('Assessment must cover the exact issued rubric')
    evidence = record.get('checkpoint_ids', [])
    if not isinstance(evidence, list):
        raise EcosystemError('Evidence checkpoints must be a list')
    for reference in evidence:
        identifier(reference)


class Trainer:
    def __init__(self, root):
        self.workspace = Workspace(root)

    def path(self, subject):
        identifier(subject)
        self.workspace.store().load(subject)
        return self.workspace.training / subject

    def load(self, subject):
        directory = self.path(subject)
        path = directory / 'progress.json'
        if not path.exists():
            return dict(schema_version=1, subject_id=subject, lessons={}, attempts=[])
        state = load_json(path)
        if (state.get('schema_version') != 1 or type(state['schema_version']) is not int
                or state.get('subject_id') != subject or not isinstance(state.get('lessons'), dict)
                or not isinstance(state.get('attempts'), list)):
            raise EcosystemError('Invalid training state; do not replace it with empty progress')
        for lesson_id, lesson in state['lessons'].items():
            identifier(lesson_id)
            if (not isinstance(lesson, dict) or lesson.get('lesson_id') != lesson_id
                    or lesson.get('subject_id') != subject or lesson.get('stage') not in STAGES
                    or not isinstance(lesson.get('rubric'), dict) or not lesson['rubric']):
                raise EcosystemError('Invalid saved training lesson')
            identifier(lesson.get('technique_id'))
            for field in ('context', 'domain'):
                if not isinstance(lesson.get(field), str) or not lesson[field].strip():
                    raise EcosystemError('Saved lesson has no concrete task context')
            for field in ('method_version', 'technique_revision'):
                if not isinstance(lesson.get(field), str) or not re.fullmatch(r'[a-f0-9]{64}', lesson[field]):
                    raise EcosystemError('Invalid saved method version')
            moment(lesson.get('issued_at'))
        for record in state['attempts']:
            if (not isinstance(record, dict) or not isinstance(record.get('lesson_id'), str)
                    or record['lesson_id'] not in state['lessons'] or 'recorded_at' not in record):
                raise EcosystemError('Saved attempt has no issued lesson or recording time')
            moment(record['recorded_at'])
            validate_attempt({key: value for key, value in record.items() if key != 'recorded_at'},
                             state['lessons'][record['lesson_id']])
        effective(state['attempts'])
        return state

    def _status(self, subject, state, catalog):
        records = effective(state['attempts'])
        result = []
        for method in catalog['techniques']:
            history = [(record, state['lessons'][record['lesson_id']]) for record in records
                       if state['lessons'][record['lesson_id']]['technique_id'] == method['id']]
            valid = [(record, lesson) for record, lesson in history
                     if lesson['technique_revision'] == method['revision']]
            last_failure = {lesson['stage']: index for index, (record, lesson) in enumerate(valid)
                            if record['assessment'] == 'failed'}
            successful = [(record, lesson) for index, (record, lesson) in enumerate(valid)
                          if record['assessment'] == 'passed' and index > last_failure.get(lesson['stage'], -1)]
            independent = [(record, lesson) for record, lesson in successful
                           if lesson['stage'] == 'independent' and record['assistance'] == 'none'
                           and record['solution_seen'] is False]
            orientation = any(lesson['stage'] == 'orientation' for _, lesson in successful)
            guided = any(lesson['stage'] == 'guided' for _, lesson in successful)
            independent_ok = len({lesson['context'] for _, lesson in independent}) >= 2
            transfer = [(record, lesson) for record, lesson in successful
                        if lesson['stage'] == 'transfer' and record['assistance'] == 'none'
                        and record['solution_seen'] is False
                        and lesson['domain'] not in {item['domain'] for _, item in independent}]
            # Operational review threshold, not a claimed optimal memory interval.
            retained = []
            for record, lesson in successful:
                if (lesson['stage'] != 'retained' or record['assistance'] != 'none'
                        or record['solution_seen'] is not False or record.get('exposure_at') is None):
                    continue
                occurred = moment(record['occurred_at'])
                known_exposures = [moment(other['occurred_at']) for other, _ in valid
                                   if other['lesson_id'] != record['lesson_id']
                                   and moment(other['occurred_at']) <= occurred]
                last_exposure = max([moment(record['exposure_at'])] + known_exposures)
                if occurred - last_exposure >= timedelta(days=1):
                    retained.append((record, lesson))
            gates = (orientation, guided, independent_ok, bool(transfer), bool(retained))
            completed = []
            for stage, passed in zip(STAGES, gates):
                if not passed:
                    break
                completed.append(stage)
            next_stage = STAGES[len(completed)] if len(completed) < len(STAGES) else 'maintenance'
            old_evidence = len(history) - len(valid)
            result.append(dict(technique_id=method['id'], title=method['title'],
                available=method.get('available', True), missing_sections=method.get('missing_sections', []),
                revision=method['revision'], completed_stages=completed, next_stage=next_stage,
                independent_contexts=len({lesson['context'] for _, lesson in independent}),
                effective_attempts=len(history), current_revision_attempts=len(valid),
                old_revision_attempts=old_evidence, needs_revalidation=old_evidence > 0 and not valid))
        return dict(subject_id=subject, method_version=catalog['version'], techniques=result,
                    attempt_count=len(records), correction_count=len(state['attempts']) - len(records))

    def status(self, subject):
        return self._status(subject, self.load(subject), self.workspace.current())

    def next_step(self, subject, technique=None):
        catalog = self.workspace.current()
        return self._next_step(subject, self.load(subject), catalog, technique)

    def _next_step(self, subject, state, catalog, technique=None):
        status = self._status(subject, state, catalog)
        states = {item['technique_id']: item for item in status['techniques']}
        definitions = {item['id']: item for item in catalog['techniques']}
        if technique is not None and technique not in definitions:
            raise EcosystemError('Unknown technique')
        if technique is not None and not definitions[technique].get('available', True):
            raise EcosystemError('This technique has no complete source content yet; add the methodology first')
        if technique is None:
            ongoing = [item for item in status['techniques'] if item['available'] and item['effective_attempts'] and item['next_stage'] != 'maintenance']
            available = [item for item in catalog['techniques'] if states[item['id']]['next_stage'] != 'maintenance'
                         and item.get('available', True)
                         and all(states[name]['completed_stages'] for name in item['prerequisites'])]
            maintainable = [item for item in catalog['techniques'] if item.get('available', True)]
            if not maintainable:
                raise EcosystemError('The source methodology is empty; import or populate the synthesis first')
            technique = (ongoing[0]['technique_id'] if ongoing else available[0]['id'] if available else maintainable[0]['id'])
        method = definitions[technique]
        current = states[technique]
        return dict(subject_id=subject, technique_id=technique, title=method['title'],
                    stage=current['next_stage'], purpose=method['purpose'],
                    needs_revalidation=current['needs_revalidation'],
                    preparation=[name for name in method['prerequisites'] if not states[name]['completed_stages']],
                    action='Vyber konkrétní materiál a kontext, potom vystav lekci příkazem lesson.',
                    source_refs=method['source_refs'])

    def lesson(self, subject, *, context, domain, technique=None):
        if not isinstance(context, str) or not context.strip() or not isinstance(domain, str) or not domain.strip():
            raise EcosystemError('A concrete task context and domain are required')
        directory = self.path(subject)
        with locked(directory / '.lock'):
            catalog = self.workspace.current()
            state = self.load(subject)
            recommendation = self._next_step(subject, state, catalog, technique)
            method = next(item for item in catalog['techniques'] if item['id'] == recommendation['technique_id'])
            stage = recommendation['stage']
            if stage == 'maintenance':
                stage = 'retained'
            instructions = {
                'orientation': 'Vlastními slovy vysvětli účel, vhodné použití, konkrétní postup a jedno omezení techniky.',
                'guided': method['practice_task'] + ' Učitel může ukázat krok a cíleně napovídat; výstup vytváří uživatel.',
                'independent': method['practice_task'] + ' Proveď bez nápovědy a bez předem viděného řešení. Použij jiný konkrétní případ než minule.',
                'transfer': method['practice_task'] + ' Použij jiný kontext či druh materiálu a vysvětli nutné přizpůsobení.',
                'retained': method['practice_task'] + ' Proveď s odstupem bez předchozího opětovného čtení řešení. Zaznamenej poslední relevantní expozici.',
            }[stage]
            lesson_id = 'lesson-' + uuid.uuid4().hex
            value = dict(lesson_id=lesson_id, subject_id=subject, technique_id=method['id'],
                         technique_revision=method['revision'], method_version=catalog['version'],
                         stage=stage, context=context, domain=domain, issued_at=now(),
                         task=instructions, rubric=ORIENTATION_RUBRIC if stage == 'orientation' else method['rubric'],
                         source_refs=method['source_refs'], source_sections=method['sections'],
                         source_update_review=recommendation['needs_revalidation'],
                         preparation=recommendation['preparation'])
            state['lessons'][lesson_id] = value
            atomic_json(directory / 'progress.json', state)
            return value

    def record(self, subject, record):
        validate_attempt(record)
        directory = self.path(subject)
        with locked(directory / '.lock'):
            state = self.load(subject)
            if record['lesson_id'] not in state['lessons']:
                raise EcosystemError('Attempt must refer to a lesson issued in this subject')
            lesson = state['lessons'][record['lesson_id']]
            validate_attempt(record, lesson)
            study = self.workspace.store().load(subject)
            known = {item['checkpoint_id'] for item in study['checkpoints']}
            evidence = record.get('checkpoint_ids', [])
            if not isinstance(evidence, list) or any(item not in known for item in evidence):
                raise EcosystemError('Evidence checkpoint does not exist in this subject')
            for saved in state['attempts']:
                if saved['attempt_id'] == record['attempt_id']:
                    if {key: value for key, value in saved.items() if key != 'recorded_at'} != record:
                        raise EcosystemError('Attempt ID conflicts with saved content')
                    return dict(attempt_id=record['attempt_id'], duplicate=True)
            if 'correction_of' not in record and any(item['lesson_id'] == record['lesson_id'] for item in state['attempts']):
                raise EcosystemError('Issue a new lesson for a new attempt; use correction_of for a grading correction')
            candidate = state['attempts'] + [dict(record, recorded_at=now())]
            effective(candidate)
            state['attempts'] = candidate
            atomic_json(directory / 'progress.json', state)
            try:
                active = next((item for item in self.workspace.current()['techniques']
                               if item['id'] == lesson['technique_id']), None)
            except (OSError, ValueError, KeyError, TypeError) as error:
                return dict(attempt_id=record['attempt_id'], duplicate=False, stale_lesson=None,
                            method_status='unavailable', method_error=str(error))
            return dict(attempt_id=record['attempt_id'], duplicate=False,
                        stale_lesson=active is None or lesson['technique_revision'] != active['revision'])
