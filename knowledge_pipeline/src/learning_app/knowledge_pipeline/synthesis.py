"""Enrich the canonical Czech synthesis after each committed transcript.

The LLM only proposes JSON insertions and a separate review. Python validates
evidence, inserts into existing sections, and commits with recoverable state.
"""

import copy
import csv
import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .archive import Archive, validate_archive
from .editor import CodexEditor
from .errors import SynthesisBusy, SynthesisError
from .storage import atomic, digest, document_bytes, safe, utcnow, write_json
from . import subscription
from .access_reporting import record_access_allowed, report_access_block

from .settings import load_settings

SETTINGS = load_settings()
DOWNLOADS = SETTINGS.incoming
ARCHIVE = SETTINGS.archive
SYNTHESIS = SETTINGS.synthesis
HEAD = re.compile(r'^(\d+\.\d+) [^\n]+\n-{10,}\n', re.M)
BOUNDARY = re.compile(r'^\d+(?:\.\d+)?\.?(?: )[^\n]+\n(?:-{10,}|={20,})\n', re.M)
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')


def sections(text):
    return {m.group(1): m for m in HEAD.finditer(text)}


def normalized(text):
    return ' '.join(text.split())


def transcript_rows(text):
    pattern = r'^(\d+:\d{2}(?::\d{2})?)\s*\n(.*?)(?=\n\s*\n\d+:\d{2}(?::\d{2})?\s*\n|\Z)'
    rows = [(m.group(1), m.group(2).strip()) for m in re.finditer(pattern,text,re.M|re.S)]
    if not rows:
        raise SynthesisError('Transcript has no timestamped source rows.')
    return rows


def evidence(item, rows):
    times = [x[0] for x in rows]
    try:
        a, b = times.index(item['start']), len(times)-1-times[::-1].index(item['end'])
    except (ValueError, KeyError) as exc:
        raise SynthesisError('The proposed evidence timestamp is absent from the transcript.') from exc
    quote = normalized(item.get('quote',''))
    if a > b or len(quote.split()) < 5 or len(quote)>1200 or quote not in normalized(' '.join(t for _,t in rows[a:b+1])):
        raise SynthesisError('The proposed evidence is not an exact quotation in the stated source range.')


def validate_plan(plan, old, transcript):
    if not isinstance(plan,dict) or not isinstance(plan.get('summary'),str) or not plan['summary'].strip():
        raise SynthesisError('Missing consolidation summary.')
    additions, coverage = plan.get('additions'), plan.get('coverage')
    if not isinstance(additions,list) or not isinstance(coverage,list) or not coverage:
        raise SynthesisError('Missing source coverage inventory.')
    if len(additions)>100:
        raise SynthesisError('Unexpectedly large insertion plan.')
    known, rows, seen = sections(old), transcript_rows(transcript), set()
    for item in additions:
        section, text = item.get('section'), item.get('text')
        if section not in known or not 1<=int(section.split('.')[0])<=21:
            raise SynthesisError('Insertion must target an existing teaching section in chapters 1–21.')
        if not isinstance(text,str) or not 20<=len(text.strip())<=10000:
            raise SynthesisError('An insertion has an invalid length.')
        if HEAD.search(text) or re.search(r'^\d+\.\d+ |^#{1,6} |^={10,}|\bS\d{2,}\b',text,re.M):
            raise SynthesisError('An insertion cannot change the document structure or invent source labels.')
        key=normalized(text)
        if key in normalized(old) or key in seen:
            raise SynthesisError('An insertion repeats existing text.')
        seen.add(key)
        evidence(item,rows)
    accounted=set()
    for item in coverage:
        evidence(item,rows)
        if not item.get('topic','').strip() or not item.get('reason','').strip():
            raise SynthesisError('Coverage entries require an explanation.')
        decision, index = item.get('decision'),item.get('addition_index')
        if decision=='added':
            if not isinstance(index,int) or not 0<=index<len(additions) or item.get('section')!=additions[index]['section']:
                raise SynthesisError('Coverage does not identify its corresponding insertion.')
            accounted.add(index)
        elif decision in ('existing','excluded'):
            if index!=-1 or (decision=='existing' and item.get('section') not in known):
                raise SynthesisError('Existing content requires a valid section reference.')
        else:
            raise SynthesisError('Unknown coverage decision.')
    if accounted!=set(range(len(additions))):
        raise SynthesisError('Not all insertions have a corresponding coverage entry.')


def validate_review(review):
    if (not isinstance(review,dict) or review.get('approved') is not True
        or review.get('problems')!=[] or review.get('missing_important')!=[]
        or not isinstance(review.get('reason'),str) or not review['reason'].strip()):
        raise SynthesisError('Content review rejected the proposed enrichment: '+json.dumps(review,ensure_ascii=False))


class Updater:
    def __init__(self, downloads=DOWNLOADS, synthesis=SYNTHESIS, archive=ARCHIVE, editor=None,
                 access_checker=None, notifier=None):
        self.downloads,self.synthesis,self.archive=Path(downloads),Path(synthesis),Path(archive)
        self.work=self.synthesis.parent/'state'
        self.state_file=self.work/'state.json'
        self.pending_file=self.work/'pending.json'
        self.editor=editor
        self.require_access = (access_checker if access_checker is not None else
                               subscription.require_codex_subscription)
        self.notifier = notifier

    @contextmanager
    def download_guard(self,already_held=False):
        if already_held:
            yield
            return
        safe(self.downloads)
        lock_file=self.downloads/'.download.lock';safe(lock_file)
        with lock_file.open('a') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SynthesisBusy('Another download/update owns the pipeline lock.') from exc
            yield

    def validate_document(self,state,text):
        sources=state.get('sources')
        if state.get('format_version')!=1 or not isinstance(sources,dict):
            raise SynthesisError('Unexpected synthesis state format.')
        if not re.fullmatch(r'[a-f0-9]{64}',state.get('synthesis_sha256','')):
            raise SynthesisError('Invalid synthesis hash in processing state.')
        labels=[]
        for video_id,record in sources.items():
            if not VIDEO_ID.fullmatch(video_id) or not re.fullmatch(r'[a-f0-9]{64}',record.get('text_sha256','')):
                raise SynthesisError('Invalid source ID/hash in processing state.')
            safe(Path(record['path']))
            labels.append(record.get('source_id'))
        if set(labels)!={f'S{i:02}' for i in range(1,len(sources)+1)} or len(labels)!=len(set(labels)):
            raise SynthesisError('The processing state has duplicate or missing source labels.')
        entries=list(re.finditer(r'^(S\d{2,}) \| ',text,re.M))
        if len(entries)!=len(sources) or {m.group(1) for m in entries}!=set(labels):
            raise SynthesisError('Source catalog and processing state have different source counts.')
        for i,m in enumerate(entries):
            block=text[m.start():entries[i+1].start() if i+1<len(entries) else len(text)]
            video=re.search(r'^Video: https://www.youtube.com/watch\?v=([A-Za-z0-9_-]{11})$',block,re.M)
            path=re.search(r'^Přepis: (.+)$',block,re.M)
            if not video or not path or video.group(1) not in sources:
                raise SynthesisError('A catalog entry does not identify a tracked source.')
            record=sources[video.group(1)]
            if record['source_id']!=m.group(1) or (self.synthesis.parent/path.group(1)).resolve()!=Path(record['path']):
                raise SynthesisError('A catalog reference differs from the tracked source path/label.')

    def validate_sources(self,state,incoming):
        _,manifest,_=validate_archive(self.archive)
        archived={v['id']:v for v in manifest['videos']}
        migrating=set(incoming)&set(state['sources'])
        if set(archived)|migrating!=set(state['sources']):
            raise SynthesisError('The archive and synthesis track different source IDs.')
        for video_id,record in state['sources'].items():
            if video_id in migrating and Path(record['path'])==incoming[video_id]['path']:
                if record['text_sha256']!=incoming[video_id]['sha256']:
                    raise SynthesisError('A previously integrated incoming transcript changed.')
                continue
            if video_id not in archived or record['text_sha256']!=archived[video_id]['text_sha256'] or Path(record['path'])!=self.archive/archived[video_id]['text_file']:
                raise SynthesisError('A tracked source differs from the shared archive.')

    def state(self):
        safe(self.synthesis);safe(self.work);safe(self.state_file);safe(self.pending_file)
        state=json.loads(self.state_file.read_text())
        if digest(self.synthesis.read_bytes())!=state.get('synthesis_sha256'):
            raise SynthesisError('The main synthesis changed outside the tracked update.')
        self.validate_document(state,self.synthesis.read_text(encoding='utf-8-sig'))
        return state

    def discover(self):
        safe(self.downloads)
        found={}
        for directory in sorted(self.downloads.iterdir()):
            safe(directory)
            if not directory.is_dir() or directory.name.startswith('.'):
                continue
            files=[directory/name for name in ('metadata.json','transcript.txt','panel.json')]
            for p in files:
                safe(p)
                if not p.is_file():
                    raise SynthesisError(f'Incomplete transcript: {directory.name}')
            meta=json.loads(files[0].read_text())
            video_id=meta.get('id','')
            if not VIDEO_ID.fullmatch(video_id) or video_id in found:
                raise SynthesisError('Missing or duplicate video ID in downloaded transcripts.')
            sha=digest(files[1].read_bytes())
            if sha!=meta.get('text_sha256'):
                raise SynthesisError(f'Downloaded transcript hash mismatch: {video_id}')
            found[video_id]={'metadata':meta,'path':files[1],'sha256':sha}
        return found

    def has_pending(self):
        try:
            with self.download_guard():
                return self._has_pending_locked()
        except SynthesisBusy:
            return True

    def _has_pending_locked(self):
        if self.pending_file.exists():
            return True
        state=self.state()
        if digest(self.synthesis.read_bytes())!=state['synthesis_sha256']:
            raise SynthesisError('The main synthesis changed outside the tracked update; reconcile it before the next update.')
        incoming=self.discover()
        self.validate_sources(state,incoming)
        for video_id,source in incoming.items():
            if video_id not in state['sources']:
                return True
            if state['sources'][video_id]['text_sha256']!=source['sha256']:
                raise SynthesisError(f'Previously integrated transcript changed: {video_id}')
            # Previously integrated files may still need transferring after an upgrade.
            return True
        return False

    def recover(self):
        if not self.pending_file.exists():
            return
        safe(self.pending_file)
        tx=json.loads(self.pending_file.read_text())
        relative=Path(tx['bundle'])
        if relative.is_absolute() or '..' in relative.parts or relative.parent!=Path('history'):
            raise SynthesisError('The pending transaction references a bundle outside update history.')
        bundle=self.work/tx['bundle']
        safe(bundle)
        candidate=bundle/'after.txt'
        safe(candidate)
        data=candidate.read_bytes()
        if digest(data)!=tx['state']['synthesis_sha256']:
            raise SynthesisError('Pending synthesis transaction is damaged.')
        self.validate_document(tx['state'],data.decode('utf-8-sig').replace('\r\n','\n'))
        current=digest(self.synthesis.read_bytes())
        if current not in (tx['before_sha256'],digest(data)):
            raise SynthesisError('The main synthesis was changed during an incomplete transaction.')
        movement=tx.get('archive_movement')
        archive=Archive(self.archive,self.downloads)
        if movement is not None:
            archive.install(movement)
        atomic(self.synthesis,data)
        write_json(self.state_file,tx['state'])
        if movement is not None:
            archive.cleanup(movement)
        write_json(bundle/'after_state.json',tx['state'])
        write_json(bundle/'completed.json',{'completed_at_utc':utcnow(),
            'synthesis_sha256':tx['state']['synthesis_sha256']})
        self.pending_file.unlink()

    def commit(self, before, after, state, bundle, movement=None):
        if self.synthesis.read_bytes()!=before:
            raise SynthesisError('The main synthesis was changed while the update was being prepared.')
        atomic(bundle/'before.txt',before)
        atomic(bundle/'after.txt',after)
        if self.state_file.exists():
            atomic(bundle/'before_state.json',self.state_file.read_bytes())
        state['synthesis_sha256']=digest(after)
        write_json(self.pending_file,{'bundle':str(bundle.relative_to(self.work)),
            'before_sha256':digest(before),'state':state,'archive_movement':movement})
        self.recover()

    def render(self, old, plan, meta, source_path, label, count):
        text=old
        locations=sections(old)
        grouped={}
        for item in plan['additions']:
            grouped.setdefault(item['section'],[]).append(item)
        # Work backwards so all section offsets still refer to the original.
        for section,items in sorted(grouped.items(),key=lambda pair:locations[pair[0]].start(),reverse=True):
            start=locations[section].end()
            following=BOUNDARY.search(old,start)
            stop=following.start() if following else len(old)
            segment=old[start:stop]
            # Place the new explanation before the chapter-wide source block.
            citation=re.search(r'^Zdrojové opory(?: pro [^:\n]+)?:',segment,re.M)
            insert=start+citation.start() if citation else stop
            # The next chapter's separator belongs to that chapter.
            prefix=text[:insert].rstrip()
            suffix=text[insert:]
            if prefix.endswith('='*72):
                prefix=prefix[:-72].rstrip()
                suffix='='*72+'\n'+suffix
            additions='\n\n'.join(item['text'].strip()+f'\n\nZdrojová opora: {label} {item["start"]}–{item["end"]}.' for item in items)
            text=prefix+'\n\n'+additions+'\n\n'+suffix
        entry=(f'{label} | {meta.get("published_date") or "datum neuvedeno"}\n'
               f'{normalized(meta.get("title",meta["id"]))}\n'
               f'Příspěvek: {normalized(plan["summary"])}\n'
               f'Zařazení: '+(', '.join(sorted(grouped,key=lambda x:tuple(map(int,x.split('.'))))) or 'již pokryto ve výkladu')+'.\n'
               f'Přepis: {os.path.relpath(source_path,self.synthesis.parent)}\n'
               f'Video: https://www.youtube.com/watch?v={meta["id"]}\n\n')
        marker='ZÁZNAM O ROZSAHU\n' if 'ZÁZNAM O ROZSAHU\n' in text else 'OBSAHOVÁ VALIDACE\n'
        if text.count(marker)!=1:
            raise SynthesisError('The synthesis source catalog boundary is missing.')
        text=text.replace(marker,entry+marker)
        text=re.sub(r'SYNTÉZA \d+ PŘEPISŮ',f'SYNTÉZA {count} PŘEPISŮ',text,count=1)
        text=re.sub(r'24\. KATALOG VŠECH \d+ ZDROJOVÝCH VIDEÍ',f'24. KATALOG VŠECH {count} ZDROJOVÝCH VIDEÍ',text,count=1)
        text=re.sub(r'24\. Katalog všech \d+ zdrojových videí',f'24. Katalog všech {count} zdrojových videí',text,count=1)
        marker='Poslední automatické doplnění:'
        line=f'{marker} {utcnow()}; celkem {count} zdrojů.'
        text=re.sub(r'^Poslední automatické doplnění:.*$',line,text,flags=re.M) if marker in text else text.rstrip()+'\n\n'+line+'\n'
        if set(sections(text))!=set(locations):
            raise SynthesisError('The insertion changed section numbering.')
        if len(re.findall(r'^S\d{2,} \| ',text,re.M))!=count:
            raise SynthesisError('The source catalog count does not match the processing state.')
        return text

    def run(self,download_lock_held=False):
        try:
            record_access_allowed(self.downloads, self.require_access(), operation="update")
            with self.download_guard(already_held=download_lock_held):
                return self._run_locked()
        except subscription.CodexAccessBlocked as error:
            report_access_block(self.downloads, error, self.notifier, operation="update")
            raise

    def _run_locked(self):
        safe(self.work)
        lock_file=self.work/'.lock';safe(lock_file)
        with lock_file.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self.recover()
            state=self.state()
            if digest(self.synthesis.read_bytes())!=state['synthesis_sha256']:
                raise SynthesisError('The main synthesis changed outside the tracked update.')
            incoming=self.discover()
            self.validate_sources(state,incoming)
            processed=[]
            for video_id,source in incoming.items():
                if video_id in state['sources']:
                    if state['sources'][video_id]['text_sha256']!=source['sha256']:
                        raise SynthesisError(f'Previously integrated transcript changed: {video_id}')
                    # Finish moving sources integrated by an older updater without
                    # calling the editor or duplicating their catalog entries.
                    archive=Archive(self.archive,self.downloads)
                    movement=archive.describe(source)
                    destination=archive.transcript_path(movement)
                    before=self.synthesis.read_bytes()
                    text=before.decode('utf-8-sig').replace('\r\n','\n')
                    old_reference='Přepis: '+os.path.relpath(source['path'],self.synthesis.parent)
                    new_reference='Přepis: '+os.path.relpath(destination,self.synthesis.parent)
                    text=text.replace(old_reference,new_reference)
                    if new_reference not in text:
                        raise SynthesisError('An integrated source has no matching catalog reference.')
                    next_state=copy.deepcopy(state)
                    next_state['sources'][video_id].update({'path':str(destination),
                        'archived_at_utc':movement['archived_at_utc']})
                    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                    bundle=self.work/'history'/f'{stamp}_{video_id}_archivace'
                    bundle.mkdir(parents=True)
                    self.require_access()
                    self.commit(before,document_bytes(text),next_state,bundle,movement)
                    state=next_state
                    print(f'ARCHIVE: moved already integrated {video_id}.',flush=True)
                    continue
                before=self.synthesis.read_bytes()
                old=before.decode('utf-8-sig').replace('\r\n','\n')
                transcript=source['path'].read_text(encoding='utf-8-sig')
                label=f'S{len(state["sources"])+1:02}'
                stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                bundle=self.work/'history'/f'{stamp}_{video_id}'
                bundle.mkdir(parents=True)
                editor=self.editor or CodexEditor()
                payload={'synthesis':old,'transcript':transcript,'source':source['metadata'],'source_label':label}
                plan=editor('PLAN',payload,bundle)
                validate_plan(plan,old,transcript)
                write_json(bundle/'plan.json',plan)
                review=editor('REVIEW',{**payload,'proposal':plan},bundle)
                write_json(bundle/'review.json',review)
                validate_review(review)
                if digest(source['path'].read_bytes())!=source['sha256']:
                    raise SynthesisError('Transcript changed during content review.')
                archive=Archive(self.archive,self.downloads)
                movement=archive.describe(source)
                destination=archive.transcript_path(movement)
                after=document_bytes(self.render(old,plan,source['metadata'],destination,label,len(state['sources'])+1))
                next_state=copy.deepcopy(state)
                next_state['sources'][video_id]={'source_id':label,'text_sha256':source['sha256'],
                    'path':str(destination),'integrated_at_utc':utcnow(),'summary':plan['summary'],
                    'archived_at_utc':movement['archived_at_utc'],
                    'added_sections':sorted({x['section'] for x in plan['additions']})}
                self.require_access()
                self.commit(before,after,next_state,bundle,movement)
                state=next_state
                processed.append(video_id)
                print(f'SYNTHESIS: integrated {video_id} as {label}; {len(plan["additions"])} additions.',flush=True)
            return processed

    def initialize(self):
        with self.download_guard():
            safe(self.work)
            self.work.mkdir(exist_ok=True)
            lock_file=self.work/'.lock';safe(lock_file)
            with lock_file.open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                return self._initialize_locked()

    def _initialize_locked(self):
        if self.pending_file.exists():
            try:
                record_access_allowed(self.downloads, self.require_access(), operation="init_recovery")
                self.recover()
            except subscription.CodexAccessBlocked as error:
                report_access_block(self.downloads, error, self.notifier, operation="init_recovery")
                raise
        if self.state_file.exists():
            self.recover()
            if digest(self.synthesis.read_bytes())!=self.state()['synthesis_sha256']:
                raise SynthesisError('Cannot reinitialize a modified synthesis.')
            return
        safe(self.synthesis);safe(self.work);safe(self.archive)
        original=self.synthesis.read_bytes()
        text=original.decode('utf-8-sig').replace('\r\n','\n')
        with (self.archive/'index.csv').open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        if len(re.findall(r'^S\d{2,} \| ',text,re.M))!=len(rows):
            raise SynthesisError('The source catalog must match the supplied archive index.')
        sources={}
        relative=os.path.relpath(self.archive,self.synthesis.parent)
        text=text.replace('Přepis: transcripts/','Přepis: '+relative+'/transcripts/')
        for i,row in enumerate(rows,1):
            source=self.archive/row['text_file']
            safe(source)
            sources[row['id']]={'source_id':f'S{i:02}','text_sha256':digest(source.read_bytes()),'path':str(source)}
            if row['url'] not in text:
                raise SynthesisError('The source catalog does not match the archive.')
        self.work.mkdir(exist_ok=True)
        bundle=self.work/'history'/'initialization'
        bundle.mkdir(parents=True)
        state={'format_version':1,'initialized_at_utc':utcnow(),'sources':sources}
        self.commit(original,document_bytes(text),state,bundle)


def update_pending(download_lock_held=False, *, settings=None):
    if settings is None:
        updater = Updater()
    else:
        updater = Updater(settings.incoming, settings.synthesis, settings.archive,
                          editor=CodexEditor(settings.editor_config()))
    return updater.run(download_lock_held=download_lock_held)


def has_pending():
    return Updater().has_pending()


