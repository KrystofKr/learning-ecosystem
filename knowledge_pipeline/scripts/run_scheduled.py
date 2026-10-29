"""Run the scheduled command from this component's source tree."""

import sys
from pathlib import Path


def main():
    component = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(component / "src"))
    from learning_app.knowledge_pipeline.cli import main as run
    return run(["--root", str(component), "scheduled"])


if __name__ == "__main__":
    raise SystemExit(main())
