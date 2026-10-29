from access_fixtures import permit_codex_access
import json
import csv
import fcntl
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from learning_app.knowledge_pipeline import synthesis as synth
from learning_app.knowledge_pipeline import downloader
from learning_app.knowledge_pipeline import archive as archiving
from learning_app.knowledge_pipeline import editor as editor_module
from learning_app.knowledge_pipeline import storage as storage_module


def panel(video_id, transcript):
    rows=[{'timestamp':ts,'text':text} for ts,text in synth.transcript_rows(transcript)]
    return {'content':{'watch':{'videoId':video_id},'rows':[
        {'transcriptSegmentViewModel':{'timestamp':r['timestamp'],'simpleText':r['text']}} for r in rows]}}


def seed_archive(root):
    root.mkdir()
    for name in ['transcripts','raw','segments']:(root/name).mkdir()
    text='0:00\nEarlier study method and its necessary conditions.\n'
    stem='2026-10-24_oldVideo001'
    (root/'transcripts'/f'{stem}.txt').write_text(text)
    entry={'id':'oldVideo001','title':'Původní video','upload_date':'2026-10-24',
        'url':'https://www.youtube.com/watch?v=oldVideo001','duration_seconds':10,
        'panel_row_count':1,'word_count':7,'first_timestamp':'0:00','last_timestamp':'0:00',
        'text_file':f'transcripts/{stem}.txt','raw_file':f'raw/{stem}.panel.json',
        'segments_file':f'segments/{stem}.json','text_sha256':synth.digest(text.encode()),'status':'downloaded'}
    (root/entry['raw_file']).write_text(json.dumps(panel('oldVideo001',text)))
    (root/entry['segments_file']).write_text(json.dumps([{'timestamp':'0:00','text':'Earlier study method and its necessary conditions.'}]))
    fields=['upload_date','title','url','id','duration_seconds','panel_row_count','word_count','first_timestamp','last_timestamp','text_file','raw_file']
    with (root/'index.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerow({k:entry[k] for k in fields})
    synth.write_json(root/'manifest.json',{'videos':[entry],'expected_video_count':1,
        'downloaded_transcript_count':1,'missing_transcript_count':0,'word_count':7,'panel_row_count':1,
        'end_date_inclusive':'2026-10-24','dom_verification':'Original sample.'})
    (root/'all_transcripts.txt').write_text(archiving.aggregate_text(root,[entry]))


QUOTE='A useful method starts by comparing two examples.'
TRANSCRIPT='0:00\n'+QUOTE+'\n\n0:10\nThen check the differences and explain their causes.\n'
TEXT='Porovnej dva příklady, vysvětli rozdíly a ověř, které příčiny je mohou vysvětlit.'


def plan(add=True):
    proof={'start':'0:00','end':'0:10','quote':QUOTE}
    return {'summary':'Porovnání příkladů s kontrolou příčin.',
        'coverage':[{'topic':'Porovnání příkladů','decision':'added' if add else 'existing',
            'section':'6.1','reason':'Konkrétní postup porovnání.','addition_index':0 if add else -1,**proof}],
        'additions':[{'section':'6.1','text':TEXT,**proof}] if add else []}


def review(approved=True):
    return {'approved':approved,'reason':'Obsah byl porovnán s přepisem a syntézou.',
            'problems':[] if approved else ['Nepodložené zobecnění.'],'missing_important':[]}


class SynthesisTests(unittest.TestCase):
    def setUp(self):
        permit_codex_access(self)
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.downloads=self.root/'incoming';self.downloads.mkdir()
        self.main=self.root/'hlavni/synteza.txt';self.main.parent.mkdir()
        self.archive=self.root/'archive'
        seed_archive(self.archive)
        self.main.write_bytes(synth.document_bytes(self.document()))
        self.calls=[]
        def editor(task,payload,bundle):
            self.calls.append(task)
            return plan() if task=='PLAN' else review()
        self.updater=synth.Updater(self.downloads,self.main,self.archive,editor=editor)
        self.updater.work.mkdir()
        synth.write_json(self.updater.state_file,{'format_version':1,
            'sources':{'oldVideo001':{'source_id':'S01',
                'text_sha256':synth.digest((self.archive/'transcripts/2026-10-24_oldVideo001.txt').read_bytes()),
                'path':str(self.archive/'transcripts/2026-10-24_oldVideo001.txt')}},
            'synthesis_sha256':synth.digest(self.main.read_bytes())})

    def document(self):
        text='KONSOLIDOVANÁ SYNTÉZA 1 PŘEPISŮ\n24. Katalog všech 1 zdrojových videí\n\n'
        for i in range(1,24):
            text+='='*72+f'\n{i}. KAPITOLA\n'+'='*72+f'\n\n{i}.1 Podkapitola\n'+'-'*40+'\nPůvodní podstatný postup a jeho podmínky.\n\n'
            if i==6:
                text+='Zdrojové opory: S01 0:00–0:10.\n\n'
        return text+'='*72+'\n24. KATALOG VŠECH 1 ZDROJOVÝCH VIDEÍ\n'+'='*72+'\n\nS01 | 2026-10-24\nPůvodní video\nPřepis: ../archive/transcripts/2026-10-24_oldVideo001.txt\nVideo: https://www.youtube.com/watch?v=oldVideo001\n\nOBSAHOVÁ VALIDACE\n-----------------\nPůvodní protokol.\n'

    def add_source(self,video_id='newVideo001'):
        folder=self.downloads/('2026-10-25_'+video_id);folder.mkdir()
        (folder/'transcript.txt').write_text(TRANSCRIPT)
        (folder/'panel.json').write_text(json.dumps(panel(video_id,TRANSCRIPT)))
        synth.write_json(folder/'metadata.json',{'id':video_id,'title':'Nový postup',
            'published_date':'2026-10-25','duration_seconds':20,'text_sha256':synth.digest(TRANSCRIPT.encode())})
        return folder

    def test_no_pending_transcript_never_calls_model(self):
        before=self.main.read_bytes()
        self.assertFalse(self.updater.has_pending())
        self.assertEqual(self.updater.run(),[])
        self.assertEqual(self.calls,[])
        self.assertEqual(self.main.read_bytes(),before)

    def test_new_transcript_enriches_existing_section_and_is_not_repeated(self):
        folder=self.add_source()
        self.assertTrue(self.updater.has_pending())
        before=self.main.read_text(encoding='utf-8-sig')
        self.assertEqual(self.updater.run(),['newVideo001'])
        after=self.main.read_text(encoding='utf-8-sig')
        self.assertEqual(self.calls,['PLAN','REVIEW'])
        self.assertIn(TEXT,after)
        self.assertEqual(after.count(TEXT),1)
        self.assertLess(after.index(TEXT),after.index('Zdrojové opory: S01'))
        self.assertIn('Zdrojová opora: S02 0:00–0:10.',after)
        self.assertIn('S02 | 2026-10-25',after)
        self.assertIn('SYNTÉZA 2 PŘEPISŮ',after)
        self.assertEqual(set(synth.sections(before)),set(synth.sections(after)))
        self.assertEqual(after.count('Původní podstatný postup a jeho podmínky.'),23)
        self.assertTrue(self.main.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n',self.main.read_bytes().replace(b'\r\n',b''))
        published=self.main.read_bytes()
        self.assertFalse(self.updater.has_pending())
        self.assertEqual(self.updater.run(),[])
        self.assertEqual(self.calls,['PLAN','REVIEW'])
        self.assertEqual(self.main.read_bytes(),published)
        self.assertFalse(folder.exists())
        archived=self.archive/'transcripts/2026-10-25_newVideo001.txt'
        self.assertEqual(archived.read_text(),TRANSCRIPT)
        self.assertIn('Přepis: ../archive/transcripts/2026-10-25_newVideo001.txt',after)
        self.assertEqual(self.updater.state()['sources']['newVideo001']['path'],str(archived))

    def test_already_covered_content_adds_catalog_entry_without_repeating_teaching(self):
        self.add_source()
        self.updater.editor=lambda task,*args:plan(False) if task=='PLAN' else review()
        self.updater.run()
        text=self.main.read_text(encoding='utf-8-sig')
        self.assertNotIn(TEXT,text)
        self.assertIn('S02 |',text)

    def test_failed_content_review_keeps_main_and_state_and_retry_uses_saved_source(self):
        folder=self.add_source()
        before=self.main.read_bytes();state=self.updater.state_file.read_bytes()
        self.updater.editor=lambda task,*args:plan() if task=='PLAN' else review(False)
        with self.assertRaisesRegex(synth.SynthesisError,'rejected'):
            self.updater.run()
        self.assertEqual(self.main.read_bytes(),before)
        self.assertEqual(self.updater.state_file.read_bytes(),state)
        self.assertTrue(folder.exists())
        self.assertNotIn('newVideo001',archiving.archived_video_ids(self.archive))
        self.assertTrue(self.updater.has_pending())
        self.updater.editor=lambda task,*args:plan() if task=='PLAN' else review()
        self.assertEqual(self.updater.run(),['newVideo001'])

    def test_invented_quote_rejected_before_review_or_publish(self):
        self.add_source();before=self.main.read_bytes()
        p=plan();p['additions'][0]['quote']='This quotation does not occur in the original.'
        self.updater.editor=lambda *args:p
        with self.assertRaisesRegex(synth.SynthesisError,'exact quotation'):
            self.updater.run()
        self.assertEqual(self.main.read_bytes(),before)

    def test_wrong_timestamp_and_target_section_are_rejected(self):
        for field,value in [('start','99:00'),('section','22.1')]:
            p=plan();p['additions'][0][field]=value
            with self.assertRaises(synth.SynthesisError):
                synth.validate_plan(p,self.document(),TRANSCRIPT)

    def test_unaccounted_insertion_and_duplicate_text_are_rejected(self):
        p=plan();p['coverage'][0]['decision']='existing';p['coverage'][0]['addition_index']=-1
        with self.assertRaisesRegex(synth.SynthesisError,'Not all insertions'):
            synth.validate_plan(p,self.document(),TRANSCRIPT)
        p=plan();p['additions'][0]['text']='Původní podstatný postup a jeho podmínky.'
        with self.assertRaisesRegex(synth.SynthesisError,'repeats'):
            synth.validate_plan(p,self.document(),TRANSCRIPT)

    def test_changed_download_is_rejected(self):
        folder=self.add_source()
        (folder/'transcript.txt').write_text('changed')
        with self.assertRaisesRegex(synth.SynthesisError,'hash mismatch'):
            self.updater.run()
        self.assertEqual(self.calls,[])

    def test_manual_main_edit_is_preserved(self):
        self.add_source()
        self.main.write_text('Uživatelská změna.')
        with self.assertRaisesRegex(synth.SynthesisError,'outside'):
            self.updater.run()
        self.assertEqual(self.main.read_text(),'Uživatelská změna.')

    def test_transcript_changed_during_review_is_not_committed(self):
        folder=self.add_source();before=self.main.read_bytes()
        def editor(task,*args):
            if task=='PLAN':return plan()
            (folder/'transcript.txt').write_text('changed during review')
            return review()
        self.updater.editor=editor
        with self.assertRaisesRegex(synth.SynthesisError,'during content review'):
            self.updater.run()
        self.assertEqual(self.main.read_bytes(),before)

    def test_recovery_handles_crash_after_publishing_main(self):
        self.add_source()
        real=storage_module.atomic
        def crash(path,data):
            if path==self.updater.state_file:
                raise OSError('Simulated crash after main publication')
            real(path,data)
        with patch.object(storage_module,'atomic',side_effect=crash):
            with self.assertRaisesRegex(OSError,'Simulated crash'):
                self.updater.run()
        self.assertTrue(self.updater.pending_file.exists())
        self.assertIn(TEXT,self.main.read_text(encoding='utf-8-sig'))
        self.updater.editor=lambda *args:self.fail('Recovery must not call the model again.')
        self.assertEqual(self.updater.run(),[])
        self.assertFalse(self.updater.pending_file.exists())
        self.assertIn('newVideo001',self.updater.state()['sources'])
        self.assertFalse([p for p in self.downloads.iterdir() if p.is_dir()])

    def test_recovery_handles_crash_before_publishing_main(self):
        self.add_source();before=self.main.read_bytes()
        real=synth.atomic
        def crash(path,data):
            if path==self.main:
                raise OSError('Simulated crash before main publication')
            real(path,data)
        with patch.object(synth,'atomic',side_effect=crash):
            with self.assertRaisesRegex(OSError,'Simulated crash'):
                self.updater.run()
        self.assertTrue(self.updater.has_pending())
        self.assertEqual(self.main.read_bytes(),before)
        self.updater.editor=lambda *args:self.fail('Recovery must reuse the reviewed result.')
        self.assertEqual(self.updater.run(),[])
        self.assertIn(TEXT,self.main.read_text(encoding='utf-8-sig'))
        self.assertIn('newVideo001',self.updater.state()['sources'])
        self.assertFalse([p for p in self.downloads.iterdir() if p.is_dir()])

    def test_archived_data_index_segments_and_aggregate_are_complete(self):
        folder=self.add_source()
        originals={p.name:p.read_bytes() for p in folder.iterdir()}
        original_old=(self.archive/'transcripts/2026-10-24_oldVideo001.txt').read_bytes()
        self.updater.run()
        self.assertEqual((self.archive/'transcripts/2026-10-25_newVideo001.txt').read_bytes(),originals['transcript.txt'])
        self.assertEqual((self.archive/'raw/2026-10-25_newVideo001.panel.json').read_bytes(),originals['panel.json'])
        self.assertEqual((self.archive/'metadata/2026-10-25_newVideo001.json').read_bytes(),originals['metadata.json'])
        self.assertEqual(json.loads((self.archive/'segments/2026-10-25_newVideo001.json').read_text()),
            [{'timestamp':ts,'text':text} for ts,text in synth.transcript_rows(TRANSCRIPT)])
        with (self.archive/'index.csv').open(encoding='utf-8-sig',newline='') as f:
            index=list(csv.DictReader(f))
        self.assertEqual([row['id'] for row in index],['oldVideo001','newVideo001'])
        manifest=json.loads((self.archive/'manifest.json').read_text())
        self.assertEqual(manifest['downloaded_transcript_count'],2)
        self.assertEqual(manifest['panel_row_count'],3)
        self.assertEqual(manifest['word_count'],23)
        self.assertEqual(manifest['end_date_inclusive'],'2026-10-25')
        self.assertIn('Nový postup\n2026-10-25\nhttps://www.youtube.com/watch?v=newVideo001',
            (self.archive/'all_transcripts.txt').read_text(encoding='utf-8-sig'))
        self.assertEqual((self.archive/'transcripts/2026-10-24_oldVideo001.txt').read_bytes(),original_old)

    def test_download_enrich_archive_cycle_does_not_redownload_on_the_next_run(self):
        class Source:
            def __init__(self):self.calls=[]
            def list_videos(self):return [{'id':'newVideo001','title':'Nový postup'}]
            def get_panel(self,video):
                self.calls.append(video['id'])
                return panel(video['id'],TRANSCRIPT),[
                    {'timestamp':ts,'text':text} for ts,text in synth.transcript_rows(TRANSCRIPT)],{
                        'id':video['id'],'title':video['title'],'published_date':'2026-10-25','duration_seconds':20}
        source=Source()
        baseline=self.root/'baseline.json'
        baseline.write_text(json.dumps({'channel_id':downloader.CHANNEL_ID,'baseline_video_ids':['oldVideo001']}))
        alerts=[]
        with patch.object(downloader,'OUTPUT',self.downloads),patch.object(downloader,'BASELINE',baseline),\
                patch.object(downloader,'ARCHIVE',self.archive),patch.object(downloader,'Source',return_value=source):
            self.assertEqual(downloader.main(alerts.append,synthesis_updater=lambda:self.updater.run(download_lock_held=True)),0)
            published=self.main.read_bytes()
            self.assertEqual(downloader.main(alerts.append,synthesis_updater=lambda:self.updater.run(download_lock_held=True)),0)
        self.assertEqual(source.calls,['newVideo001'])
        self.assertEqual(self.calls,['PLAN','REVIEW'])
        self.assertEqual(alerts,[])
        self.assertEqual(self.main.read_bytes(),published)
        self.assertFalse([p for p in self.downloads.iterdir() if p.is_dir()])

    def test_failure_during_archive_install_keeps_pending_and_recovers_without_model(self):
        folder=self.add_source();before=self.main.read_bytes()
        real=archiving.atomic
        def fail(path,data):
            if path==self.archive/'manifest.json':raise OSError('Simulated archive failure')
            real(path,data)
        with patch.object(archiving,'atomic',side_effect=fail):
            with self.assertRaisesRegex(OSError,'Simulated archive failure'):self.updater.run()
        self.assertTrue(folder.exists())
        self.assertEqual(self.main.read_bytes(),before)
        self.assertTrue(self.updater.pending_file.exists())
        self.assertFalse(list((self.updater.work/'history').glob('*/completed.json')))
        self.updater.editor=lambda *args:self.fail('Archive recovery must not call the editor.')
        self.assertEqual(self.updater.run(),[])
        self.assertFalse(folder.exists())
        self.assertEqual(archiving.archived_video_ids(self.archive),{'oldVideo001','newVideo001'})

    def test_archive_conflict_preserves_existing_file_and_incoming_transcript(self):
        folder=self.add_source();before=self.main.read_bytes()
        conflict=self.archive/'transcripts/2026-10-25_newVideo001.txt'
        conflict.write_text('Existing different archive content.')
        with self.assertRaisesRegex(archiving.ArchiveError,'different content'):self.updater.run()
        self.assertEqual(self.main.read_bytes(),before)
        self.assertEqual(conflict.read_text(),'Existing different archive content.')
        self.assertTrue(folder.exists())

    def test_older_integrated_source_is_moved_without_another_model_call(self):
        folder=self.add_source()
        state=self.updater.state()
        old=self.main.read_text(encoding='utf-8-sig')
        updated=self.updater.render(old,plan(False),{'id':'newVideo001','title':'Nový postup','published_date':'2026-10-25'},folder/'transcript.txt','S02',2)
        self.main.write_bytes(synth.document_bytes(updated))
        state['sources']['newVideo001']={'source_id':'S02','text_sha256':synth.digest(TRANSCRIPT.encode()),'path':str(folder/'transcript.txt')}
        state['synthesis_sha256']=synth.digest(self.main.read_bytes())
        synth.write_json(self.updater.state_file,state)
        self.updater.editor=lambda *args:self.fail('A processed source must not be reviewed again.')
        self.assertTrue(self.updater.has_pending())
        self.assertEqual(self.updater.run(),[])
        self.assertFalse(folder.exists())
        text=self.main.read_text(encoding='utf-8-sig')
        self.assertEqual(text.count('S02 |'),1)
        self.assertIn('Přepis: ../archive/transcripts/2026-10-25_newVideo001.txt',text)

    def test_interruption_during_incoming_cleanup_resumes_without_losing_files(self):
        folder=self.add_source()
        real=Path.unlink
        def interrupt(path,*args,**kwargs):
            if path==folder/'panel.json':raise OSError('Simulated cleanup interruption')
            return real(path,*args,**kwargs)
        with patch.object(Path,'unlink',interrupt):
            with self.assertRaisesRegex(OSError,'Simulated cleanup interruption'):self.updater.run()
        self.assertTrue(self.updater.pending_file.exists())
        self.assertIn('newVideo001',self.updater.state()['sources'])
        self.updater.editor=lambda *args:self.fail('Cleanup recovery must not call the editor.')
        self.assertEqual(self.updater.run(),[])
        self.assertFalse(folder.exists())
        self.assertEqual((self.archive/'transcripts/2026-10-25_newVideo001.txt').read_text(),TRANSCRIPT)

    def test_insertion_at_chapter_boundary_preserves_heading_separators(self):
        p=plan()
        p['additions'][0]['section']='21.1';p['coverage'][0]['section']='21.1'
        result=self.updater.render(self.document(),p,{'id':'newVideo001','title':'Nový'},self.root/'source.txt','S02',2)
        self.assertIn('Zdrojová opora: S02 0:00–0:10.\n\n'+'='*72+'\n22. KAPITOLA',result)
        self.assertEqual(result.count('='*72),48)

    def test_two_new_videos_keep_stable_ids(self):
        self.add_source('newVideo001');self.add_source('newVideo002')
        self.updater.editor=lambda task,*args:plan(False) if task=='PLAN' else review()
        self.assertEqual(self.updater.run(),['newVideo001','newVideo002'])
        state=self.updater.state()['sources']
        self.assertEqual(state['oldVideo001']['source_id'],'S01')
        self.assertEqual(state['newVideo001']['source_id'],'S02')
        self.assertEqual(state['newVideo002']['source_id'],'S03')

    def test_failed_model_call_leaves_published_synthesis_unchanged(self):
        self.add_source();before=self.main.read_bytes()
        def fail(*args):raise synth.SynthesisError('Unavailable model')
        self.updater.editor=fail
        with self.assertRaisesRegex(synth.SynthesisError,'Unavailable model'):
            self.updater.run()
        self.assertEqual(self.main.read_bytes(),before)
        self.assertTrue(self.updater.has_pending())

    def test_symlink_source_is_rejected(self):
        folder=self.add_source()
        (folder/'transcript.txt').unlink()
        (folder/'transcript.txt').symlink_to(self.main)
        with self.assertRaisesRegex(synth.SynthesisError,'symlink'):
            self.updater.run()

    def test_corrupt_archive_index_cannot_silently_skip_a_video(self):
        index=self.archive/'index.csv'
        index.write_text(index.read_text(encoding='utf-8-sig').replace('oldVideo001','newVideo999'))
        before=self.main.read_bytes()
        with self.assertRaisesRegex(archiving.ArchiveError,'different source IDs'):
            self.updater.has_pending()
        with self.assertRaises(archiving.ArchiveError):self.updater.run()
        self.assertEqual(self.main.read_bytes(),before)
        self.assertEqual(self.calls,[])

    def test_missing_original_is_detected_without_new_incoming_videos(self):
        (self.archive/'transcripts/2026-10-24_oldVideo001.txt').unlink()
        with self.assertRaisesRegex(archiving.ArchiveError,'missing'):
            self.updater.has_pending()
        self.assertEqual(self.calls,[])

    def test_inconsistent_state_labels_are_rejected_before_a_model_call(self):
        self.add_source()
        state=json.loads(self.updater.state_file.read_text())
        state['sources']['oldVideo001']['source_id']='S02'
        synth.write_json(self.updater.state_file,state)
        with self.assertRaisesRegex(synth.SynthesisError,'source labels'):
            self.updater.run()
        self.assertEqual(self.calls,[])

    def test_catalog_cannot_point_to_a_different_file_than_processing_state(self):
        state=self.updater.state()
        state['sources']['oldVideo001']['path']=str(self.root/'other.txt')
        synth.write_json(self.updater.state_file,state)
        with self.assertRaisesRegex(synth.SynthesisError,'catalog reference'):
            self.updater.has_pending()

    def test_shared_lock_prevents_manual_and_scheduled_updates_overlapping(self):
        self.add_source();before=self.main.read_bytes()
        with (self.downloads/'.download.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(synth.SynthesisBusy):self.updater.run()
            self.assertTrue(self.updater.has_pending())
        self.assertEqual(self.calls,[])
        self.assertEqual(self.main.read_bytes(),before)
        self.assertEqual(self.updater.run(),['newVideo001'])

    def test_pending_bundle_cannot_escape_history(self):
        synth.write_json(self.updater.pending_file,{'bundle':'../outside','state':{}})
        before=self.main.read_bytes()
        with self.assertRaisesRegex(synth.SynthesisError,'outside update history'):
            self.updater.run()
        self.assertEqual(self.main.read_bytes(),before)

    def test_pending_move_cannot_delete_files_outside_new_videos(self):
        folder=self.add_source()
        archive=archiving.Archive(self.archive,self.downloads)
        movement=archive.describe(self.updater.discover()['newVideo001'])
        outside=self.root/'outside'/folder.name
        outside.mkdir(parents=True)
        for p in folder.iterdir():(outside/p.name).write_bytes(p.read_bytes())
        movement['source_dir']=str(outside)
        with self.assertRaisesRegex(archiving.ArchiveError,'escapes'):
            archive.install(movement)
        with self.assertRaisesRegex(archiving.ArchiveError,'escapes'):
            archive.cleanup(movement)
        self.assertTrue((outside/'transcript.txt').is_file())

    def test_pending_move_cannot_write_an_absolute_destination(self):
        self.add_source()
        archive=archiving.Archive(self.archive,self.downloads)
        movement=archive.describe(self.updater.discover()['newVideo001'])
        target=self.root/'outside.txt'
        movement['files']['transcript.txt']=str(target)
        with self.assertRaisesRegex(archiving.ArchiveError,'escapes'):
            archive.install(movement)
        self.assertFalse(target.exists())

    def test_full_archive_check_detects_altered_segments(self):
        (self.archive/'segments/2026-10-24_oldVideo001.json').write_text('[]')
        with self.assertRaisesRegex(archiving.ArchiveError,'differ'):
            archiving.validate_archive(self.archive,verify_panels=True)

    def test_aggregate_preserves_trailing_spaces_in_panel_text(self):
        folder=self.add_source()
        text=TRANSCRIPT.rstrip('\n')+'  \n'
        (folder/'transcript.txt').write_text(text)
        raw=json.loads((folder/'panel.json').read_text())
        raw['content']['rows'][-1]['transcriptSegmentViewModel']['simpleText']+='  '
        (folder/'panel.json').write_text(json.dumps(raw))
        meta=json.loads((folder/'metadata.json').read_text())
        meta['text_sha256']=synth.digest(text.encode())
        synth.write_json(folder/'metadata.json',meta)
        self.updater.run()
        self.assertTrue((self.archive/'all_transcripts.txt').read_text().endswith('causes.  \n'))
        archiving.validate_archive(self.archive,verify_panels=True)

    def test_archive_period_expands_for_an_older_newly_processed_video(self):
        folder=self.add_source()
        meta=json.loads((folder/'metadata.json').read_text());meta['published_date']='2020-01-01'
        synth.write_json(folder/'metadata.json',meta)
        folder.rename(self.downloads/'2020-01-01_newVideo001')
        self.updater.run()
        manifest=json.loads((self.archive/'manifest.json').read_text())
        self.assertEqual(manifest['start_date_inclusive'],'2020-01-01')

    def test_duplicate_catalog_entries_cannot_hide_another_tracked_source(self):
        self.add_source();self.updater.run()
        state=self.updater.state()
        text=self.main.read_text(encoding='utf-8-sig')
        first=text.index('S01 |');second=text.index('S02 |')
        stop=text.index('OBSAHOVÁ VALIDACE\n',second)
        text=text[:second]+text[first:second]+text[stop:]
        self.main.write_bytes(synth.document_bytes(text))
        state['synthesis_sha256']=synth.digest(self.main.read_bytes())
        synth.write_json(self.updater.state_file,state)
        with self.assertRaisesRegex(synth.SynthesisError,'source counts'):
            self.updater.has_pending()

    def test_archive_index_order_must_match_the_manifest(self):
        self.add_source();self.updater.run()
        path=self.archive/'index.csv'
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(reversed(rows))
        with self.assertRaisesRegex(archiving.ArchiveError,'source ordering'):
            self.updater.has_pending()

    def test_archive_index_word_count_drift_is_detected(self):
        path=self.archive/'index.csv'
        with path.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
        rows[0]['word_count']='999'
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        with self.assertRaisesRegex(archiving.ArchiveError,'metadata differs'):
            self.updater.has_pending()

    def test_committed_history_can_restore_state_without_calling_the_model(self):
        self.add_source();self.updater.run()
        completed=list((self.updater.work/'history').glob('*/completed.json'))
        self.assertEqual(len(completed),1)
        saved=completed[0].parent/'after_state.json'
        expected=self.updater.state_file.read_bytes()
        self.updater.state_file.unlink()
        synth.atomic(self.updater.state_file,saved.read_bytes())
        self.assertEqual(self.updater.state_file.read_bytes(),expected)
        self.assertEqual(self.updater.run(),[])
        self.assertEqual(self.calls,['PLAN','REVIEW'])


class DownloadPipelineTests(unittest.TestCase):
    def setUp(self):
        permit_codex_access(self)

    def test_saved_transcript_is_enriched_immediately_and_failure_keeps_download(self):
        from test_downloader import FakeSource
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)
            calls=[]
            def enrich():
                calls.append(list(output.glob('*/transcript.txt')))
                raise synth.SynthesisError('Review failed')
            source=FakeSource([{'id':'newVideo001','title':'A'},{'id':'newVideo002','title':'B'}])
            with self.assertRaisesRegex(synth.SynthesisError,'Review failed'):
                downloader.download_new(source,output,{'baseline_video_ids':[]},on_download=enrich)
            self.assertEqual(source.calls,['newVideo001'])
            self.assertEqual(len(calls[0]),1)
            self.assertTrue((output/'2026-10-24_newVideo001/metadata.json').is_file())

    def test_failed_enrichment_is_reported_and_not_a_successful_download_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp);baseline=output/'baseline.json'
            baseline.write_text(json.dumps({'channel_id':downloader.CHANNEL_ID,'baseline_video_ids':[]}))
            def fail():raise synth.SynthesisError('Content review rejected new video')
            alerts=[]
            with patch.object(downloader,'OUTPUT',output),patch.object(downloader,'BASELINE',baseline),patch.object(downloader,'Source') as source:
                result=downloader.main(alerts.append,synthesis_updater=fail)
            self.assertEqual(result,1)
            source.assert_not_called()
            self.assertEqual(len(alerts),1)
            self.assertEqual(json.loads((output/'status.json').read_text())['status'],'failure')


class EditorCommandTests(unittest.TestCase):
    def setUp(self):
        permit_codex_access(self)

    def test_structured_read_only_invocation_and_answer_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config.json'
            config.write_text(json.dumps({'codex_binary':sys.executable,'model':'test-model',
                'reasoning_effort':'high','timeout_seconds':10}))
            def run(command,**kwargs):
                self.assertIn('--ephemeral',command)
                self.assertIn('--ignore-user-config',command)
                self.assertIn('--ignore-rules',command)
                self.assertEqual(command[command.index('--sandbox')+1],'read-only')
                self.assertIn('features.shell_tool=false',command)
                self.assertIn('features.apps=false',command)
                self.assertIn('features.multi_agent=false',command)
                self.assertIn('web_search="disabled"',command)
                self.assertNotIn('shell',kwargs)
                self.assertIn('REVIEW',kwargs['input'].decode())
                self.assertTrue(Path(kwargs['cwd']).is_dir())
                answer=Path(command[command.index('-o')+1])
                answer.write_text(json.dumps(review()))
                return subprocess.CompletedProcess(command,0)
            with patch.object(editor_module.subprocess,'run',side_effect=run):
                self.assertEqual(synth.CodexEditor(config)('REVIEW',{'data':'Test'},root),review())

    def test_timeout_is_a_failure_instead_of_publishing_a_partial_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config.json'
            config.write_text(json.dumps({'codex_binary':sys.executable,'model':'test-model',
                'reasoning_effort':'high','timeout_seconds':10}))
            with patch.object(editor_module.subprocess,'run',side_effect=subprocess.TimeoutExpired('codex',10)):
                with self.assertRaisesRegex(synth.SynthesisError,'timed out'):
                    synth.CodexEditor(config)('PLAN',{'data':'Test'},root)


if __name__=='__main__':
    unittest.main()
