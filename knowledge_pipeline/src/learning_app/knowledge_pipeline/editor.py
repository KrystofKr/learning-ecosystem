"""Codex adapter isolated from downloads, archives and scheduling."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .errors import SynthesisError
from .schemas import PLAN_SCHEMA, REVIEW_SCHEMA
from .settings import load_settings
from .storage import atomic, write_json
from . import subscription

PROMPT = Path(__file__).with_name("prompts") / "synthesis.txt"


class CodexEditor:
    def __init__(self, config=None, *, access_checker=None):
        if config is None:
            self.config = load_settings().editor_config()
        elif isinstance(config, dict):
            self.config = dict(config)
        else:
            self.config = json.loads(Path(config).read_text(encoding="utf-8"))
        self.require_access = (access_checker if access_checker is not None else
                               lambda: subscription.require_codex_subscription(self.config))

    def __call__(self, task, payload, run_dir):
        if task not in {"PLAN", "REVIEW"}:
            raise SynthesisError("Unknown editor task; expected PLAN or REVIEW.")
        self.require_access()
        run_dir = Path(run_dir)
        config=self.config
        binary=Path(shutil.which(config['codex_binary']) or config['codex_binary'])
        if not binary.is_file():
            raise SynthesisError('Configured Codex executable is unavailable.')
        schema=run_dir/(task.lower()+'_schema.json')
        answer=run_dir/(task.lower()+'_answer.json')
        write_json(schema, PLAN_SCHEMA if task=='PLAN' else REVIEW_SCHEMA)
        request=PROMPT.read_text()+'\nÚkol: '+task+'\nVSTUPNÍ DATA:\n'+json.dumps(payload,ensure_ascii=False)
        atomic(run_dir/(task.lower()+'_input.txt'),request.encode())
        command=[str(binary),'--no-daemon','--ask-for-approval','never','exec',
            '--ignore-user-config','--ignore-rules','--ephemeral','--skip-git-repo-check',
            '--sandbox','read-only','--color','never','--json',
            '-c','features.shell_tool=false','-c','features.shell_snapshot=false',
            '-c','features.apps=false','-c','features.multi_agent=false',
            '-c','web_search="disabled"','-c','project_doc_max_bytes=0',
            '-c','forced_login_method="chatgpt"',
            '--model',config['model'],'-c','model_reasoning_effort='+json.dumps(config['reasoning_effort']),
            '--output-schema',str(schema),'-o',str(answer),'-']
        # Use an empty cwd outside the project, without project config/AGENTS/hooks.
        with tempfile.TemporaryDirectory(prefix='justinsung-editor-') as cwd:
            command=command[:-1]+['-c','sqlite_home='+json.dumps(cwd),
                '-c','log_dir='+json.dumps(str(Path(cwd)/'logs')),'-']
            with (run_dir/(task.lower()+'_events.jsonl')).open('wb') as out, (run_dir/(task.lower()+'_stderr.txt')).open('wb') as err:
                try:
                    result=subprocess.run(command,input=request.encode(),stdout=out,stderr=err,cwd=cwd,
                                          timeout=config['timeout_seconds'],check=False,
                                          env=subscription.subscription_environment())
                except subprocess.TimeoutExpired as exc:
                    self.require_access()
                    raise SynthesisError(f'Codex {task} timed out; the published synthesis was not changed.') from exc
        if result.returncode or not answer.is_file():
            self.require_access()
            raise SynthesisError(f'Codex {task} failed (exit {result.returncode}); details: {run_dir.name}/{task.lower()}_stderr.txt')
        try:
            return json.loads(answer.read_text())
        except (ValueError,UnicodeError) as exc:
            raise SynthesisError('Codex did not return valid structured output.') from exc
