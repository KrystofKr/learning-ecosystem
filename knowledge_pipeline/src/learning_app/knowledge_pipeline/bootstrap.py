"""Create empty local storage; the public repository contains no study data."""

import csv
import io

CHAPTERS = [
    'Co je skutečným výsledkem učení',
    'Diagnostika učebního systému',
    'Mentální úsilí a kognitivní zátěž',
    'Priming a příprava struktury',
    'Vrstvení a pořadí učení',
    'Hluboké zpracování a vztahy',
    'Poznámky a myšlenkové mapy',
    'Čtení a druhy informací',
    'Aktivní vybavování a zpětná vazba',
    'Memorování a kartičky',
    'Osvojování dovedností a návyků',
    'Metakognice a reflexe',
    'Myšlení a rozhodování',
    'Učení v práci a odbornost',
    'Cíle a hodnoty',
    'Produktivita a systémy',
    'Priority a plánování',
    'Prokrastinace a motivace',
    'Pozornost a soustředění',
    'Energie a odpočinek',
    'AI a nástroje při učení',
    'Praktický rozcestník',
    'Rejstřík a rozlišení rámců',
    'Katalog všech 0 zdrojových videí',
]
INDEX_FIELDS = ['upload_date','title','url','id','duration_seconds','panel_row_count',
                'word_count','first_timestamp','last_timestamp','text_file','raw_file']


def empty_synthesis():
    text='JUSTIN SUNG — KONSOLIDOVANÁ SYNTÉZA 0 PŘEPISŮ\n'+'='*72+'\n\n'
    text+='Prázdná osnova. Obsah doplní zpracování vlastních přepisů.\n\nOBSAH\n-----\n'
    text+='\n'.join(f'{i:02}. {title}' for i,title in enumerate(CHAPTERS,1))+'\n'
    for i,title in enumerate(CHAPTERS,1):
        text+='\n'+'='*72+f'\n{i}. {title.upper()}\n'+'='*72+'\n\n'
        if i<24:
            text+=f'{i}.1 {title}\n'+'-'*72+'\n\n'
    return text+'OBSAHOVÁ VALIDACE\n-----------------\nNové doplnění vyžaduje zdrojovou oporu a obsahovou kontrolu.\n'


def initialize_storage(settings, *, access_checker=None):
    from .downloader import CHANNEL_ID, CHANNEL_URL
    from .archive import atomic,json_bytes
    from .synthesis import Updater,document_bytes
    from . import subscription
    from .access_reporting import record_access_allowed, report_access_block
    checker = (access_checker if access_checker is not None else
               lambda: subscription.require_codex_subscription(settings.editor_config()))
    if (settings.synthesis.parent / 'state/pending.json').exists():
        try:
            record_access_allowed(settings.incoming, checker(), operation="init_recovery")
        except subscription.CodexAccessBlocked as error:
            report_access_block(settings.incoming, error, operation="init_recovery")
            raise
    settings.incoming.mkdir(parents=True,exist_ok=True)
    settings.synthesis.parent.mkdir(parents=True,exist_ok=True)
    settings.config_file.parent.mkdir(parents=True,exist_ok=True)
    if not settings.config_file.exists():
        atomic(settings.config_file,json_bytes(settings.values))
    if not settings.archive.exists():
        settings.archive.mkdir(parents=True)
        for directory in ['transcripts','raw','segments','metadata']:
            (settings.archive/directory).mkdir()
        buffer=io.StringIO(newline='')
        csv.DictWriter(buffer,fieldnames=INDEX_FIELDS).writeheader()
        atomic(settings.archive/'index.csv',buffer.getvalue().encode('utf-8-sig'))
        atomic(settings.archive/'manifest.json',json_bytes({
            'channel':'Justin Sung','channel_id':CHANNEL_ID,'channel_url':CHANNEL_URL,
            'videos':[],'expected_video_count':0,'downloaded_transcript_count':0,
            'missing_transcript_count':0,'panel_row_count':0,'word_count':0,
        }))
        atomic(settings.archive/'all_transcripts.txt',b'\n')
    if not settings.synthesis.exists():
        atomic(settings.synthesis,document_bytes(empty_synthesis()))
    Updater(settings.incoming,settings.synthesis,settings.archive,
            access_checker=checker).initialize()
    return {'synthesis':str(settings.synthesis),'baseline_required':not settings.baseline.exists()}


def capture_baseline(settings, *, access_checker=None):
    from .downloader import Source,CHANNEL_ID
    from .archive import atomic,json_bytes
    from . import subscription
    from .access_reporting import record_access_allowed, report_access_block
    checker = (access_checker if access_checker is not None else
               lambda: subscription.require_codex_subscription(settings.editor_config()))
    try:
        record_access_allowed(settings.incoming, checker(), operation="baseline")
    except subscription.CodexAccessBlocked as error:
        report_access_block(settings.incoming, error, operation="baseline")
        raise
    if settings.baseline.exists():
        raise FileExistsError('Baseline already exists; it was not overwritten.')
    videos=Source().list_videos()
    settings.baseline.parent.mkdir(parents=True,exist_ok=True)
    atomic(settings.baseline,json_bytes({'channel_id':CHANNEL_ID,'baseline_video_ids':[v['id'] for v in videos]}))
    return {'skipped_existing_videos':len(videos)}
