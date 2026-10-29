"""Recoverable transfer of processed transcripts into the shared archive."""

import csv
import fcntl
import io
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from .downloader import panel_rows
from .storage import atomic as _atomic, digest as sha, json_bytes


class ArchiveError(RuntimeError):
    pass


def safe(path):
    if not path.is_absolute() or path.resolve()!=path:
        raise ArchiveError(f'Archive path must not contain symlinks: {path}')


def atomic(path,data):
    safe(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    _atomic(path,data)


def archived_video_ids(root):
    _,manifest,_=validate_archive(root)
    return {v['id'] for v in manifest['videos']}


def contained(root,relative):
    value=Path(relative)
    if value.is_absolute() or '..' in value.parts:
        raise ArchiveError('An archive reference escapes the archive directory.')
    path=root/value;safe(path)
    return path


def aggregate_text(root,videos):
    chunks=[]
    for v in videos:
        path=contained(root,v['text_file'])
        text=path.read_bytes()
        if sha(text)!=v['text_sha256']:
            raise ArchiveError(f'An archived transcript changed: {v["id"]}')
        chunks.append(v['title']+'\n'+v.get('upload_date','')+'\n'+v['url']+'\n\n'+text.decode('utf-8-sig').rstrip('\r\n'))
    return ('\n\n'+'='*80+'\n\n').join(chunks)+'\n'


def validate_archive(root,pending_video_id=None,verify_panels=False):
    """Check the archive before trusting its IDs; tolerate only the active move."""
    root=Path(root);safe(root)
    index_file=contained(root,'index.csv');manifest_file=contained(root,'manifest.json')
    with index_file.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f);fields=reader.fieldnames;index=list(reader)
    manifest=json.loads(manifest_file.read_text(encoding='utf-8-sig'))
    videos=manifest['videos']
    indexed={r['id']:r for r in index};exported={v['id']:v for v in videos}
    if len(indexed)!=len(index) or len(exported)!=len(videos):
        raise ArchiveError('The archive contains duplicate video IDs.')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{11}',v) for v in indexed.keys()|exported.keys()):
        raise ArchiveError('The archive contains an invalid video ID.')
    if (indexed.keys()^exported.keys())-({pending_video_id} if pending_video_id else set()):
        raise ArchiveError('The archive CSV index and manifest contain different source IDs.')
    if [r['id'] for r in index if r['id']!=pending_video_id]!=[v['id'] for v in videos if v['id']!=pending_video_id]:
        raise ArchiveError('The archive CSV and manifest have inconsistent source ordering.')
    for video_id in indexed.keys()&exported.keys():
        expected={k:'' if exported[video_id].get(k) is None else str(exported[video_id][k]) for k in fields}
        if indexed[video_id]!=expected:
            raise ArchiveError(f'Archive index metadata differs from the manifest: {video_id}')
    for v in videos:
        for key in ('text_file','raw_file','segments_file'):
            if not contained(root,v[key]).is_file():
                raise ArchiveError(f'An archived file is missing: {v[key]}')
        text=contained(root,v['text_file']).read_bytes()
        if sha(text)!=v['text_sha256']:
            raise ArchiveError(f'An archived transcript changed: {v["id"]}')
        if verify_panels:
            rows=panel_rows(json.loads(contained(root,v['raw_file']).read_text(encoding='utf-8-sig')),v['id'])
            segments=json.loads(contained(root,v['segments_file']).read_text(encoding='utf-8-sig'))
            expected_text='\n\n'.join(f'{r["timestamp"]}\n{r["text"]}' for r in rows)+'\n'
            if rows!=segments or expected_text.encode()!=text:
                raise ArchiveError(f'Archived text, segments and original panel differ: {v["id"]}')
            if len(rows)!=v['panel_row_count'] or sum(len(r['text'].split()) for r in rows)!=v['word_count']:
                raise ArchiveError(f'Archived row/word counts differ from the source panel: {v["id"]}')
        if v.get('metadata_file'):
            metadata=json.loads(contained(root,v['metadata_file']).read_text(encoding='utf-8-sig'))
            if metadata.get('id')!=v['id'] or metadata.get('text_sha256')!=v['text_sha256']:
                raise ArchiveError('Archived original metadata identifies different source data.')
    checks={'downloaded_transcript_count':len(videos),'panel_row_count':sum(v['panel_row_count'] for v in videos),
        'word_count':sum(v['word_count'] for v in videos),
        'expected_video_count':len(videos)+manifest.get('missing_transcript_count',0)}
    if any(manifest.get(k)!=value for k,value in checks.items()):
        raise ArchiveError('The archive manifest has inconsistent aggregate counts.')
    if pending_video_id is None:
        aggregate=contained(root,'all_transcripts.txt').read_text(encoding='utf-8-sig')
        if aggregate!=aggregate_text(root,videos):
            raise ArchiveError('The combined transcript does not match the individual archived sources.')
    return index,manifest,fields


class Archive:
    def __init__(self,root,incoming=None):
        self.root=Path(root)
        self.incoming=Path(incoming) if incoming is not None else self.root.parent/'incoming'

    def validate_movement(self,movement):
        safe(self.root);safe(self.incoming)
        source=Path(movement['source_dir']);safe(source)
        video_id=movement['video_id'];stamp=movement['stamp']
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',stamp):
            raise ArchiveError('Invalid video ID or date in an archive transaction.')
        date.fromisoformat(stamp)
        stem=stamp+'_'+video_id
        expected={'transcript.txt':f'transcripts/{stem}.txt','panel.json':f'raw/{stem}.panel.json',
            'metadata.json':f'metadata/{stem}.json'}
        if source.parent!=self.incoming or source.name!=stem or movement['files']!=expected:
            raise ArchiveError('An archive transaction escapes the configured incoming/archive paths.')
        if set(movement['sha256'])!=set(expected) or any(not re.fullmatch(r'[a-f0-9]{64}',v) for v in movement['sha256'].values()):
            raise ArchiveError('Invalid source hashes in an archive transaction.')

    def describe(self,source):
        """Freeze hashes and the final paths; do not move anything before review."""
        folder=source['path'].parent
        safe(self.root);safe(folder)
        expected={'transcript.txt','panel.json','metadata.json'}
        if {p.name for p in folder.iterdir()}!=expected:
            raise ArchiveError('Unexpected files in a transcript folder; refusing an incomplete transfer.')
        content={}
        for name in sorted(expected):
            path=folder/name;safe(path)
            content[name]=path.read_bytes()
        meta=json.loads(content['metadata.json'])
        video_id=meta.get('id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
            raise ArchiveError('Invalid archived video ID.')
        if meta!=source['metadata'] or sha(content['transcript.txt'])!=source['sha256'] or meta.get('text_sha256')!=source['sha256']:
            raise ArchiveError('Transcript metadata changed during processing.')
        stamp=meta.get('published_date') or folder.name[:10]
        date.fromisoformat(stamp)
        stem=stamp+'_'+video_id
        files={
            'transcript.txt':f'transcripts/{stem}.txt',
            'panel.json':f'raw/{stem}.panel.json',
            'metadata.json':f'metadata/{stem}.json',
        }
        rows=panel_rows(json.loads(content['panel.json']),video_id)
        reconstructed='\n\n'.join(f'{r["timestamp"]}\n{r["text"]}' for r in rows)+'\n'
        if reconstructed.encode()!=content['transcript.txt']:
            raise ArchiveError('The saved transcript does not match its original panel.')
        for target in files.values():
            safe(self.root/target)
        movement={'source_dir':str(folder),'video_id':video_id,'stamp':stamp,'files':files,
            'sha256':{name:sha(data) for name,data in content.items()},
            'archived_at_utc':datetime.now(timezone.utc).isoformat()}
        self.validate_movement(movement)
        return movement

    def transcript_path(self,movement):
        return self.root/movement['files']['transcript.txt']

    def _content(self,movement):
        self.validate_movement(movement)
        source=Path(movement['source_dir']);safe(source)
        content={}
        for name,relative in movement['files'].items():
            origin=source/name;destination=self.root/relative
            safe(origin);safe(destination)
            path=origin if origin.exists() else destination
            data=path.read_bytes()
            if sha(data)!=movement['sha256'][name]:
                raise ArchiveError(f'A file changed during archiving: {path}')
            if destination.exists() and destination.read_bytes()!=data:
                raise ArchiveError(f'The archive already contains different content: {destination}')
            content[name]=data
        return content

    def install(self,movement):
        """Copy verified data and refresh indexes; safe to repeat after a crash."""
        safe(self.root)
        lock_file=self.root/'.archive.lock';safe(lock_file)
        with lock_file.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self.validate_movement(movement)
            index,manifest,fields=validate_archive(self.root,pending_video_id=movement['video_id'])
            content=self._content(movement)
            meta=json.loads(content['metadata.json'])
            video_id=movement['video_id']
            if meta['id']!=video_id:
                raise ArchiveError('The archived metadata identifies another video.')
            rows=panel_rows(json.loads(content['panel.json']),video_id)
            stem=movement['stamp']+'_'+video_id
            segments_file=f'segments/{stem}.json'
            safe(self.root/segments_file)
            segments=json_bytes(rows)
            if (self.root/segments_file).exists() and (self.root/segments_file).read_bytes()!=segments:
                # Existing JSON may differ only in whitespace, never in panel rows.
                if json.loads((self.root/segments_file).read_text(encoding='utf-8-sig'))!=rows:
                    raise ArchiveError('The archive has different timestamped segments.')
            for name,relative in movement['files'].items():
                path=self.root/relative
                if not path.exists():atomic(path,content[name])
            if not (self.root/segments_file).exists():atomic(self.root/segments_file,segments)

            index_file=self.root/'index.csv';manifest_file=self.root/'manifest.json'
            safe(index_file);safe(manifest_file)
            videos=manifest['videos']
            if len({x['id'] for x in index})!=len(index) or len({x['id'] for x in videos})!=len(videos):
                raise ArchiveError('The archive contains duplicate index records.')
            count=sum(len(row['text'].split()) for row in rows)
            entry={
                'id':video_id,'title':meta['title'],'upload_date':meta.get('published_date') or '',
                'url':f'https://www.youtube.com/watch?v={video_id}',
                'duration_seconds':meta.get('duration_seconds'),
                'source':'YouTube Show transcript panel / PAmodern_transcript_view',
                'endpoint':'https://www.youtube.com/youtubei/v1/get_panel',
                'panel_row_count':len(rows),'first_timestamp':rows[0]['timestamp'],
                'last_timestamp':rows[-1]['timestamp'],'word_count':count,
                'text_file':movement['files']['transcript.txt'],'raw_file':movement['files']['panel.json'],
                'segments_file':segments_file,'metadata_file':movement['files']['metadata.json'],
                'text_sha256':movement['sha256']['transcript.txt'],'status':'downloaded',
                'archived_at_utc':movement['archived_at_utc'],
            }
            existing=next((v for v in videos if v['id']==video_id),None)
            if existing is None:
                videos.append(entry)
            elif any(existing.get(k)!=entry[k] for k in ('text_sha256','text_file','raw_file','segments_file')):
                raise ArchiveError('The existing archive record differs from the processed source.')
            row={k:'' if entry.get(k) is None else str(entry[k]) for k in fields}
            existing_row=next((v for v in index if v['id']==video_id),None)
            if existing_row is None:
                index.append(row)
            elif any(existing_row.get(k)!=row[k] for k in ('text_file','raw_file')):
                raise ArchiveError('The existing index points to different archived files.')

            manifest.update({
                'expected_video_count':len(videos)+manifest.get('missing_transcript_count',0),
                'downloaded_transcript_count':len(videos),
                'panel_row_count':sum(v['panel_row_count'] for v in videos),
                'word_count':sum(v['word_count'] for v in videos),
                'archive_updated_at_utc':movement['archived_at_utc'],
            })
            dates=[v['upload_date'] for v in videos if v.get('upload_date')]
            if dates:
                manifest['end_date_inclusive']=max([manifest.get('end_date_inclusive','')]+dates)
                manifest['start_date_inclusive']=min([manifest.get('start_date_inclusive',min(dates))]+dates)
            manifest.setdefault('dom_verification_scope','The original browser comparison applies to the initial archive; incremental sources are verified against their saved Show transcript panels.')
            csv_data=io.StringIO(newline='')
            writer=csv.DictWriter(csv_data,fieldnames=fields,lineterminator='\r\n')
            writer.writeheader();writer.writerows(index)
            aggregate=aggregate_text(self.root,videos)
            atomic(index_file,('\ufeff'+csv_data.getvalue()).encode())
            atomic(manifest_file,json_bytes(manifest))
            aggregate_file=self.root/'all_transcripts.txt';safe(aggregate_file)
            encoding='utf-8-sig' if aggregate_file.read_bytes().startswith(b'\xef\xbb\xbf') else 'utf-8'
            atomic(aggregate_file,aggregate.encode(encoding))

    def cleanup(self,movement):
        """Remove the incoming copy only after publication and all archive writes."""
        source=Path(movement['source_dir']);safe(source)
        if not source.exists():return
        expected=set(movement['files'])
        if {p.name for p in source.iterdir()}-expected:
            raise ArchiveError('Unexpected files appeared in the incoming folder; preserving them.')
        self._content(movement)
        for relative in movement['files'].values():
            target=self.root/relative;safe(target)
            if not target.is_file():
                raise ArchiveError('An archived file is missing; keeping the incoming copy.')
        # Verify every remaining original before deleting any of them.
        for name in sorted(expected):
            path=source/name;safe(path)
            if path.exists():
                if sha(path.read_bytes())!=movement['sha256'][name]:
                    raise ArchiveError('Incoming files changed before cleanup.')
        for name in sorted(expected):
            (source/name).unlink(missing_ok=True)
        source.rmdir()
        descriptor=os.open(source.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
