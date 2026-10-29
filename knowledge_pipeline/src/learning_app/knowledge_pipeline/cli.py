"""Standalone command line for the knowledge pipeline."""

import argparse
import json
import sys
from pathlib import Path

from .service import KnowledgePipeline
from .settings import load_settings
from .subscription import CodexAccessBlocked
from .access_reporting import report_access_block


def main(argv=None, *, prog="learning-knowledge"):
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument("--root", type=Path, help="Project root containing local config and data.")
    parser.add_argument("--config", type=Path, help="Path to the knowledge pipeline JSON configuration.")
    parser.add_argument("command", choices=("init", "baseline", "check", "access", "update",
                                           "run", "scheduled", "validate"))
    parser.add_argument("--panels", action="store_true",
                        help="Also compare every archived source panel.")
    args = parser.parse_args(argv)
    try:
        pipeline = KnowledgePipeline(load_settings(root=args.root, config_file=args.config))
        if args.command == "run":
            return pipeline.run()
        if args.command == "scheduled":
            return pipeline.scheduled()
        if args.command == "init":
            result = pipeline.initialize()
        elif args.command == "baseline":
            result = pipeline.capture_baseline()
        elif args.command == "check":
            result = pipeline.check()
        elif args.command == "access":
            result = pipeline.access()
        elif args.command == "update":
            result = pipeline.update()
        else:
            result = pipeline.validate(panels=args.panels)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except CodexAccessBlocked as error:
        return report_access_block(pipeline.settings.incoming, error, operation=args.command)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
