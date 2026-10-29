#!/usr/bin/env python3
"""Return coursework to osu_repository and finish the independent ecosystem layout."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'ecosystem/src'))
from learning_ecosystem.layout import restore_coursework


if __name__ == '__main__':
    try:
        print(json.dumps(restore_coursework(ROOT), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as error:
        print('Layout repair stopped: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
