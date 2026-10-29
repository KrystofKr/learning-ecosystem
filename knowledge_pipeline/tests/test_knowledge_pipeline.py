from access_fixtures import permit_codex_access
import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from learning_app.knowledge_pipeline import KnowledgePipeline
from learning_app.knowledge_pipeline import downloader, editor, synthesis
from learning_app.knowledge_pipeline.settings import load_settings


ROOT = Path(__file__).resolve().parents[1]
CONFIG_ENV = ("LEARNING_KNOWLEDGE_ROOT", "LEARNING_KNOWLEDGE_CONFIG")


def clean_environment():
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    for name in CONFIG_ENV:
        environment.pop(name, None)
    return environment


class ComponentSettingsTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, clean_environment(), clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_default_configuration_has_one_canonical_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config").mkdir()
            scoped = root / "config/knowledge_pipeline.local.json"
            self.assertEqual(load_settings(root=root).config_file, scoped)
            scoped.write_text(json.dumps({"model": "knowledge-model"}))
            settings = load_settings(root=root)
            self.assertEqual(settings.config_file, scoped)
            self.assertEqual(settings.values["model"], "knowledge-model")

    def test_explicit_configuration_overrides_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.json"
            second = root / "second.json"
            first.write_text(json.dumps({"model": "first-model"}))
            second.write_text(json.dumps({"model": "second-model"}))
            with patch.dict(os.environ, {"LEARNING_KNOWLEDGE_CONFIG": str(first)}):
                self.assertEqual(load_settings(root=root).values["model"], "first-model")
                self.assertEqual(load_settings(root=root, config_file=second).values["model"],
                                 "second-model")

    def test_parent_project_root_resolves_to_the_self_contained_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            component = parent / "knowledge_pipeline"
            (component / "src/learning_app").mkdir(parents=True)
            (component / "pyproject.toml").write_text('[project]\nname="learning-app"\n')
            settings = load_settings(root=parent)
            self.assertEqual(settings.root, component)
            self.assertEqual(settings.incoming, component / "data/justin_sung/incoming")

    def test_explicit_root_overrides_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            with patch.dict(os.environ, {"LEARNING_KNOWLEDGE_ROOT": str(parent / "other")}):
                self.assertEqual(load_settings().root, parent / "other")
                self.assertEqual(load_settings(root=parent).root, parent)


class ComponentIsolationTests(unittest.TestCase):
    def setUp(self):
        permit_codex_access(self)

    def test_unknown_editor_task_is_rejected_before_starting_a_process(self):
        client = editor.CodexEditor({"codex_binary": sys.executable,
                                     "model": "test-model",
                                     "reasoning_effort": "high", "timeout_seconds": 10})
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(editor.subprocess, "run") as process:
            with self.assertRaisesRegex(synthesis.SynthesisError, "Unknown editor task"):
                client("UNSUPPORTED", {}, Path(temporary))
            process.assert_not_called()

    def test_editor_import_and_configuration_need_no_youtube_dependencies(self):
        program = """
import importlib.abc
import sys

class RejectNetworkDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'requests', 'yt_dlp'}:
            raise AssertionError('Unexpected YouTube dependency: ' + fullname)

sys.meta_path.insert(0, RejectNetworkDependencies())
from learning_app.knowledge_pipeline.editor import CodexEditor
client = CodexEditor({'codex_binary': sys.executable, 'model': 'isolated-model',
                      'reasoning_effort': 'high', 'timeout_seconds': 10})
assert client.config['model'] == 'isolated-model'
assert 'learning_app.knowledge_pipeline.synthesis' not in sys.modules
assert 'learning_app.knowledge_pipeline.downloader' not in sys.modules
"""
        result = subprocess.run([sys.executable, "-B", "-c", program], env=clean_environment(),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_standalone_cli_initializes_and_validates_one_storage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for command in ("init", "check", "validate"):
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "learning_app.knowledge_pipeline",
                     "--root", str(root), command],
                    cwd=root, env=clean_environment(), capture_output=True,
                    text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                value = json.loads(result.stdout)
                if command == "validate":
                    self.assertEqual(value["sources"], 0)
                    self.assertFalse(value["pending"])
            self.assertTrue((root / "config/knowledge_pipeline.local.json").is_file())
            self.assertFalse((root / "config/local.json").exists())

    def test_scheduled_entry_uses_component_root_through_a_compatibility_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = ROOT / "scripts/run_scheduled.py"
            link = Path(temporary) / "run_scheduled.py"
            link.symlink_to(script)
            for entry in (script, link):
                with self.subTest(entry=entry), \
                        patch("learning_app.knowledge_pipeline.cli.main", return_value=10) as run, \
                        patch.object(sys, "path", sys.path.copy()):
                    namespace = runpy.run_path(str(entry))
                    self.assertEqual(namespace["main"](), 10)
                    run.assert_called_once_with(["--root", str(ROOT), "scheduled"])

    def test_two_instances_keep_data_and_model_selection_separate(self):
        from test_synthesis import TRANSCRIPT, panel, plan, review

        class Source:
            def list_videos(self):
                return [{"id": "newVideo001", "title": "Synthetic study method"}]

            def get_panel(self, video):
                rows = [{"timestamp": timestamp, "text": text}
                        for timestamp, text in synthesis.transcript_rows(TRANSCRIPT)]
                metadata = {"id": video["id"], "title": video["title"],
                            "published_date": "2026-10-25", "duration_seconds": 20}
                return panel(video["id"], TRANSCRIPT), rows, metadata

        models = []

        def model_response(command, **kwargs):
            models.append(command[command.index("--model") + 1])
            answer = Path(command[command.index("-o") + 1])
            result = plan() if "Úkol: PLAN\n" in kwargs["input"].decode() else review()
            answer.write_text(json.dumps(result))
            return subprocess.CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temporary:
            pipelines = []
            for name in ("first", "second"):
                root = Path(temporary) / name
                root.mkdir()
                configuration = root / "editor.json"
                configuration.write_text(json.dumps({"model": name + "-model",
                                                      "codex_binary": sys.executable}))
                settings = load_settings(root=root, config_file=configuration)
                pipeline = KnowledgePipeline(settings)
                pipeline.initialize()
                settings.baseline.parent.mkdir(parents=True, exist_ok=True)
                settings.baseline.write_text(json.dumps({"channel_id": downloader.CHANNEL_ID,
                                                          "baseline_video_ids": []}))
                pipelines.append(pipeline)
            alerts = []
            with patch.object(downloader, "Source", return_value=Source()), \
                    patch.object(editor.subprocess, "run", side_effect=model_response):
                self.assertEqual(pipelines[0].run(alerts.append), 0)
                self.assertEqual(pipelines[1].validate(panels=True)["sources"], 0)
                self.assertEqual(pipelines[1].run(alerts.append), 0)
                self.assertEqual(pipelines[0].run(alerts.append), 0)
            self.assertEqual(models, ["first-model", "first-model", "second-model", "second-model"])
            self.assertEqual(alerts, [])
            for pipeline in pipelines:
                self.assertEqual(pipeline.validate(panels=True)["sources"], 1)
                state = json.loads((pipeline.settings.synthesis.parent / "state/state.json").read_text())
                record = state["sources"]["newVideo001"]
                self.assertTrue(Path(record["path"]).is_relative_to(pipeline.settings.archive))


if __name__ == "__main__":
    unittest.main()
