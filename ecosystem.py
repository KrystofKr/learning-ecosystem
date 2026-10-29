#!/usr/bin/env python3
"""Relocatable entry point for the local learning ecosystem."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'ecosystem/src'), str(ROOT / 'knowledge_pipeline/src')]
from learning_ecosystem.cli import main

if __name__ == '__main__':
    raise SystemExit(main(root=ROOT))
