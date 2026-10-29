"""Configuration owned by the knowledge pipeline."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULTS = {
    "data_dir": "data",
    "codex_binary": "codex",
    "model": "gpt-6.1-sol",
    "reasoning_effort": "xhigh",
    "timeout_seconds": 600,
}


@dataclass(frozen=True)
class Settings:
    root: Path
    config_file: Path
    values: dict

    @property
    def data(self):
        path = Path(self.values["data_dir"]).expanduser()
        return (self.root / path).absolute() if not path.is_absolute() else path.absolute()

    @property
    def channel(self):
        return self.data / "justin_sung"

    @property
    def incoming(self):
        return self.channel / "incoming"

    @property
    def archive(self):
        return self.channel / "archive"

    @property
    def synthesis(self):
        return self.channel / "synthesis/main.txt"

    @property
    def baseline(self):
        return self.root / "config/baseline.json"

    def editor_config(self):
        fields = ("codex_binary", "model", "reasoning_effort", "timeout_seconds")
        return {key: self.values[key] for key in fields}


def load_settings(root=None, config_file=None):
    source_root = Path(__file__).resolve().parents[3]
    default_root = source_root if (source_root / "pyproject.toml").is_file() else Path.cwd()
    root = Path(root or os.environ.get("LEARNING_KNOWLEDGE_ROOT") or default_root).resolve()
    nested = root / "knowledge_pipeline"
    if (nested / "pyproject.toml").is_file() and (nested / "src/learning_app").is_dir():
        root = nested
    selected = (config_file or os.environ.get("LEARNING_KNOWLEDGE_CONFIG")
                or root / "config/knowledge_pipeline.local.json")
    config_file = Path(selected).absolute()
    values = dict(DEFAULTS)
    if config_file.is_file():
        supplied = json.loads(config_file.read_text(encoding="utf-8"))
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
            raise ValueError("Unknown knowledge pipeline configuration fields.")
        values.update(supplied)
    string_fields = (key for key in DEFAULTS if key != "timeout_seconds")
    if not all(isinstance(values[key], str) and values[key] for key in string_fields):
        raise ValueError("Configuration string fields must be non-empty.")
    if type(values["timeout_seconds"]) is not int or values["timeout_seconds"] <= 0:
        raise ValueError("timeout_seconds must be a positive integer.")
    return Settings(root, config_file, values)
