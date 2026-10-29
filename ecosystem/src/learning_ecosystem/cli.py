"""Commands used by the coaching skills and by the transcript automation."""
import argparse
import json
from pathlib import Path
import sys

from .workspace import Workspace, EcosystemError, load_json


def main(argv=None, *, root=None):
    parser = argparse.ArgumentParser(prog='learning-ecosystem')
    parser.add_argument('--root', type=Path, default=root or Path(__file__).resolve().parents[3])
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor')
    commands.add_parser('subjects')
    commands.add_parser('install')
    sync = commands.add_parser('sync')
    sync.add_argument('--synthesis', type=Path)
    importer = commands.add_parser('import-library')
    importer.add_argument('--source', type=Path, required=True)
    methods = commands.add_parser('methods')
    methods.add_argument('--technique')
    render=commands.add_parser('render')
    render.add_argument('--subject',required=True)
    render.add_argument('--source',required=True)
    render.add_argument('--page',type=int,required=True)
    render.add_argument('--scale',type=float,default=2.0)
    for name in ('status', 'next', 'lesson', 'record'):
        command = commands.add_parser(name)
        command.add_argument('--subject', required=True)
        if name in ('next', 'lesson'):
            command.add_argument('--technique')
        if name == 'lesson':
            command.add_argument('--context', required=True)
            command.add_argument('--domain', required=True)
        if name == 'record':
            command.add_argument('--input', type=Path, required=True)
    args = parser.parse_args(argv)
    workspace = Workspace(args.root)
    try:
        if args.command == 'sync':
            from .sync import synchronize
            result = synchronize(workspace.root, synthesis=args.synthesis)
        elif args.command == 'doctor':
            from .setup import doctor
            result = doctor(workspace.root)
        elif args.command == 'install':
            from .setup import install_runtime
            result = install_runtime(workspace.root)
        elif args.command == 'import-library':
            from .setup import import_library
            result = import_library(workspace.root, args.source)
        elif args.command == 'subjects':
            result = workspace.store().subjects()
        elif args.command == 'render':
            from .render import render_page
            result=render_page(workspace.root,args.subject,args.source,args.page,args.scale)
        elif args.command == 'methods':
            catalog = workspace.current()
            if args.technique:
                technique = next((item for item in catalog['techniques'] if item['id'] == args.technique), None)
                if technique is None:
                    raise EcosystemError('Unknown technique')
                result = dict(version=catalog['version'], technique=technique,
                    passages=[catalog['sections'][key] for key in technique['sections'] if key in catalog['sections']],
                    source_changes=catalog['changes'])
            else:
                result = dict(version=catalog['version'], changes=catalog['changes'],
                    techniques=[{key: item[key] for key in ('id', 'title', 'purpose', 'prerequisites', 'available', 'missing_sections')}
                                for item in catalog['techniques']])
        else:
            from .training import Trainer
            trainer = Trainer(workspace.root)
            if args.command == 'status':
                result = trainer.status(args.subject)
            elif args.command == 'next':
                result = trainer.next_step(args.subject, args.technique)
            elif args.command == 'lesson':
                result = trainer.lesson(args.subject, context=args.context, domain=args.domain,
                                        technique=args.technique)
            else:
                result = trainer.record(args.subject, load_json(args.input))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Ecosystem operation failed: {error}', file=sys.stderr)
        return 1
