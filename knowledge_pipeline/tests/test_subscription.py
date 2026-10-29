import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from learning_app.knowledge_pipeline import KnowledgePipeline, subscription
from learning_app.knowledge_pipeline import cli, downloader, scheduler, synthesis
from learning_app.knowledge_pipeline.access_reporting import report_access_block
from learning_app.knowledge_pipeline.settings import load_settings
from learning_app.knowledge_pipeline import editor as editor_module


def allowed():
    return subscription.CodexAccess("pro", "2026-10-26T08:00:00+00:00")


def denied(*_):
    raise subscription.CodexAccessBlocked("subscription_required")


def account(kind="chatgpt", plan="pro"):
    return {"account": {"type": kind, "planType": plan, "email": "PRIVATE_IDENTITY"}}


def limits(plan="pro", used=10):
    return {"rateLimits": {"limitId": "codex", "planType": plan,
                           "primary": {"usedPercent": used}, "secondary": None}}


class SubscriptionPolicyTests(unittest.TestCase):
    def test_known_paid_plans_are_accepted_without_returning_identity(self):
        for plan in ("plus", "pro", "business", "enterprise", "edu", "go"):
            with self.subTest(plan=plan):
                result = subscription.evaluate_access(account(), limits(plan)).as_dict()
                self.assertEqual(result["status"], "allowed")
                self.assertEqual(result["plan_type"], plan)
                self.assertNotIn("PRIVATE_IDENTITY", json.dumps(result))

    def test_cached_paid_account_does_not_override_a_live_free_plan(self):
        with self.assertRaises(subscription.CodexAccessBlocked) as caught:
            subscription.evaluate_access(account(plan="pro"), limits("free"))
        self.assertEqual(caught.exception.reason, "subscription_required")

    def test_live_paid_plan_can_override_a_stale_free_account_claim(self):
        self.assertEqual(subscription.evaluate_access(account(plan="free"), limits()).plan_type,
                         "pro")

    def test_api_key_is_rejected_even_with_a_paid_looking_quota(self):
        with self.assertRaises(subscription.CodexAccessBlocked) as caught:
            subscription.evaluate_access(account(kind="apiKey"), limits())
        self.assertEqual(caught.exception.reason, "unsupported_auth")

    def test_signed_out_is_reported_as_authentication_required(self):
        with self.assertRaises(subscription.CodexAccessBlocked) as caught:
            subscription.evaluate_access({"account": None}, limits())
        self.assertEqual(caught.exception.reason, "authentication_required")

    def test_unknown_or_missing_live_plan_is_not_inferred_from_login(self):
        for plan in (None, "unknown", "future-plan", {"unexpected": "object"}):
            with self.subTest(plan=plan), self.assertRaises(subscription.CodexAccessBlocked) as caught:
                subscription.evaluate_access(account(), limits(plan))
            self.assertEqual(caught.exception.reason, "access_unverified")

    def test_missing_or_unrelated_quota_is_not_an_active_subscription(self):
        for value in ({}, {"rateLimits": None},
                      {"rateLimits": {"limitId": "unrelated", "planType": "pro"}}):
            with self.subTest(value=value), self.assertRaises(subscription.CodexAccessBlocked):
                subscription.evaluate_access(account(), value)

    def test_current_codex_bucket_wins_over_the_legacy_quota_view(self):
        value = limits("pro")
        value["rateLimitsByLimitId"] = {"codex": limits("free")["rateLimits"]}
        with self.assertRaises(subscription.CodexAccessBlocked) as caught:
            subscription.evaluate_access(account(), value)
        self.assertEqual(caught.exception.reason, "subscription_required")

    def test_exhausted_primary_or_secondary_window_stops_the_run(self):
        for name in ("primary", "secondary"):
            value = limits()
            value["rateLimits"][name] = {"usedPercent": 100}
            with self.subTest(name=name), self.assertRaises(subscription.CodexAccessBlocked) as caught:
                subscription.evaluate_access(account(), value)
            self.assertEqual(caught.exception.reason, "quota_exhausted")

    def test_spend_control_or_server_limit_prevents_additional_usage(self):
        for field, value in (("spendControlReached", True),
                             ("rateLimitReachedType", "usage_limit")):
            data = limits()
            data["rateLimits"][field] = value
            with self.subTest(field=field), self.assertRaises(subscription.CodexAccessBlocked):
                subscription.evaluate_access(account(), data)

    def test_invalid_usage_is_not_treated_as_zero(self):
        for used in (True, -1, "0"):
            with self.subTest(used=used), self.assertRaises(subscription.CodexAccessBlocked) as caught:
                subscription.evaluate_access(account(), limits(used=used))
            self.assertEqual(caught.exception.reason, "access_unverified")

    def test_api_credentials_are_removed_from_child_environment_only(self):
        values = {"OPENAI_API_KEY": "private-value", "CODEX_API_KEY": "private-value",
                  "CODEX_ACCESS_TOKEN": "private-value"}
        with patch.dict(os.environ, values):
            environment = subscription.subscription_environment()
            for key in values:
                self.assertNotIn(key, environment)
                self.assertEqual(os.environ[key], "private-value")


SERVER = r"""
import json
import sys
import time

mode, logfile = sys.argv[1:]
for line in sys.stdin:
    request = json.loads(line)
    with open(logfile, "a", encoding="utf-8") as output:
        output.write(json.dumps(request) + "\n")
    method = request["method"]
    if method == "initialized":
        continue
    if mode == "stall":
        time.sleep(10)
        continue
    if method == "initialize":
        result = {}
    elif method == "account/read":
        kind = "apiKey" if mode == "api" else "chatgpt"
        result = {"account": {"type": kind, "email": "PRIVATE_IDENTITY", "planType": "pro"}}
        print(json.dumps({"method": "account/updated",
                          "params": {"email": "PRIVATE_IDENTITY"}}), flush=True)
    elif method == "account/rateLimits/read":
        if mode == "error":
            print(json.dumps({"id": request["id"], "error": {
                "code": -1, "message": "PRIVATE_BACKEND_MESSAGE"}}), flush=True)
            continue
        result = {"rateLimits": {"limitId": "codex", "planType": "pro",
                                 "primary": {"usedPercent": 5}}}
    else:
        raise AssertionError("Unexpected RPC: " + method)
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
"""


class SubscriptionTransportTests(unittest.TestCase):
    def probe(self, mode, *, timeout=2):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        program = root / "account_server.py"
        program.write_text(SERVER)
        logfile = root / "requests.jsonl"
        real_popen = subprocess.Popen
        self.processes = []
        self.commands = []

        def start(command, **kwargs):
            self.commands.append(command)
            process = real_popen([sys.executable, "-u", str(program), mode, str(logfile)],
                                 **kwargs)
            self.processes.append(process)
            return process

        self.logfile = logfile
        with patch.object(subscription.subprocess, "Popen", side_effect=start):
            return subscription.require_codex_subscription(
                {"codex_binary": sys.executable}, timeout=timeout)

    def test_protocol_checks_fresh_account_and_quota_without_model_turns(self):
        result = self.probe("paid")
        self.assertEqual(result.plan_type, "pro")
        requests = [json.loads(line) for line in self.logfile.read_text().splitlines()]
        self.assertEqual([item["method"] for item in requests],
                         ["initialize", "initialized", "account/read", "account/rateLimits/read"])
        self.assertTrue(requests[2]["params"]["refreshToken"])
        self.assertIn("--no-daemon", self.commands[0])
        self.assertIn("stdio://", self.commands[0])
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_api_login_does_not_even_request_quota(self):
        with self.assertRaises(subscription.CodexAccessBlocked) as caught:
            self.probe("api")
        self.assertEqual(caught.exception.reason, "unsupported_auth")
        requests = [json.loads(line)["method"] for line in self.logfile.read_text().splitlines()]
        self.assertNotIn("account/rateLimits/read", requests)
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_backend_failure_does_not_expose_identity_or_raw_error(self):
        with self.assertRaises(subscription.CodexAccessBlocked) as caught:
            self.probe("error")
        self.assertEqual(caught.exception.reason, "access_unverified")
        self.assertNotIn("PRIVATE_", str(caught.exception))
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_hung_check_times_out_and_terminates_the_child(self):
        started = time.monotonic()
        with self.assertRaises(subscription.CodexAccessBlocked) as caught:
            self.probe("stall", timeout=0.15)
        self.assertEqual(caught.exception.reason, "access_unverified")
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(all(process.poll() is not None for process in self.processes))


class AutomationBlockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = load_settings(root=self.root)
        self.pipeline = KnowledgePipeline(self.settings, access_checker=denied)
        self.pipeline.initialize()
        self.alerts = []
        self.notices = patch(
            "learning_app.knowledge_pipeline.notifications.LocalNotifier.show",
            return_value={"os_accepted": True, "history_verified": True})
        self.show = self.notices.start()
        self.addCleanup(self.notices.stop)

    def assert_blocked(self):
        value = json.loads((self.settings.incoming / "status.json").read_text())
        self.assertEqual(value["status"], "blocked")
        self.assertEqual(value["block_reason"], "subscription_required")
        self.assertTrue((self.settings.incoming / "access_state.json").is_file())
        self.assertIn("předplatné", (self.settings.incoming / "ALERT.txt").read_text())

    def test_run_stops_before_source_requests_or_synthesis(self):
        before = self.settings.synthesis.read_bytes()
        with patch.object(downloader, "Source") as source, \
                patch.object(synthesis.Updater, "run") as update:
            self.assertEqual(self.pipeline.run(self.alerts.append), 10)
        source.assert_not_called()
        update.assert_not_called()
        self.assertEqual(self.settings.synthesis.read_bytes(), before)
        self.assertEqual(len(self.alerts), 1)
        self.assert_blocked()

    def test_revoked_access_is_identified_after_a_model_rejection(self):
        checker = Mock(side_effect=[allowed(),
                                   subscription.CodexAccessBlocked("subscription_required")])
        client = editor_module.CodexEditor({
            "codex_binary": sys.executable, "model": "test-model",
            "reasoning_effort": "high", "timeout_seconds": 10,
        }, access_checker=checker)
        with patch.object(editor_module.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaises(subscription.CodexAccessBlocked) as caught:
                client("PLAN", {}, self.root)
        self.assertEqual(caught.exception.reason, "subscription_required")
        self.assertEqual(checker.call_count, 2)

    def test_update_cannot_recover_or_publish_without_subscription(self):
        pending = self.settings.synthesis.parent / "state/pending.json"
        pending.write_text("must not be read or recovered")
        before = self.settings.synthesis.read_bytes()
        with patch.object(synthesis.Updater, "recover") as recover:
            with self.assertRaises(subscription.CodexAccessBlocked):
                self.pipeline.update()
        recover.assert_not_called()
        self.assertEqual(pending.read_text(), "must not be read or recovered")
        self.assertEqual(self.settings.synthesis.read_bytes(), before)
        self.assert_blocked()

    def test_init_does_not_recover_a_pending_transaction_without_subscription(self):
        pending = self.settings.synthesis.parent / "state/pending.json"
        pending.write_text("must not be read or recovered")
        before = self.settings.synthesis.read_bytes()
        with patch.object(synthesis.Updater, "recover") as recover:
            with self.assertRaises(subscription.CodexAccessBlocked):
                self.pipeline.initialize()
        recover.assert_not_called()
        self.assertEqual(pending.read_text(), "must not be read or recovered")
        self.assertEqual(self.settings.synthesis.read_bytes(), before)
        self.assert_blocked()

    def test_scheduled_attempt_does_not_advance_the_week_or_inspect_pending_work(self):
        schedule_state = self.settings.incoming / "schedule_state.json"
        schedule_state.write_text('{"last_success_at_utc":"2026-10-26T08:00:00Z"}')
        before = schedule_state.read_bytes()
        with patch.object(self.pipeline, "_updater") as update:
            result = self.pipeline.scheduled(
                notifier=self.alerts.append, now=datetime.fromisoformat("2026-10-27T09:00:00+01:00"))
        self.assertEqual(result, 10)
        update.assert_not_called()
        self.assertEqual(schedule_state.read_bytes(), before)
        self.assert_blocked()

    def test_baseline_does_not_contact_youtube_when_blocked(self):
        with patch.object(downloader, "Source") as source:
            with self.assertRaises(subscription.CodexAccessBlocked):
                self.pipeline.capture_baseline()
        source.assert_not_called()
        self.assertFalse(self.settings.baseline.exists())
        self.assert_blocked()

    def test_manual_cli_reports_the_same_block_once(self):
        with patch.object(subscription, "require_codex_subscription", side_effect=denied), \
                patch.object(downloader, "Source") as source, \
                contextlib.redirect_stderr(io.StringIO()) as output:
            result = cli.main(["--root", str(self.root), "run"])
        self.assertEqual(result, 10)
        self.assertIn("předplatné", output.getvalue())
        source.assert_not_called()
        self.assertEqual(self.show.call_count, 1)
        self.assert_blocked()

    def test_scheduled_block_is_retried_after_subscription_returns(self):
        output = self.settings.incoming
        (output / "schedule_state.json").write_text(
            '{"last_success_at_utc":"2026-10-26T08:00:00Z"}')
        (output / "status.json").write_text(
            '{"status":"blocked","notification_delivered":true}')
        calls = []

        def run(_):
            calls.append(1)
            downloader.write_json(output / "status.json", {
                "status": "success", "checked_at_utc": "2026-10-27T08:00:00Z"})
            return 0

        for _ in range(2):
            self.assertEqual(scheduler.main(
                output=output, runner=run, notifier=self.alerts.append,
                now=datetime.fromisoformat("2026-10-27T09:00:00+01:00"),
                pending_checker=lambda: False, access_checker=allowed), 0)
        self.assertEqual(calls, [1])
        self.assertEqual(self.alerts, [])

    def test_loss_before_publication_preserves_main_and_incoming_source(self):
        from test_synthesis import TRANSCRIPT, panel, plan, review
        folder = self.settings.incoming / "2026-10-25_newVideo001"
        folder.mkdir()
        (folder / "transcript.txt").write_text(TRANSCRIPT)
        (folder / "panel.json").write_text(json.dumps(panel("newVideo001", TRANSCRIPT)))
        (folder / "metadata.json").write_text(json.dumps({
            "id": "newVideo001", "title": "Synthetic example", "published_date": "2026-10-25",
            "text_sha256": synthesis.digest(TRANSCRIPT.encode())}))
        checker = Mock(side_effect=[allowed(), subscription.CodexAccessBlocked("subscription_required")])
        editor = lambda task, *_: plan() if task == "PLAN" else review()
        updater = synthesis.Updater(
            self.settings.incoming, self.settings.synthesis, self.settings.archive,
            editor=editor, access_checker=checker, notifier=self.alerts.append)
        before = self.settings.synthesis.read_bytes()
        with self.assertRaises(subscription.CodexAccessBlocked):
            updater.run()
        self.assertEqual(self.settings.synthesis.read_bytes(), before)
        self.assertTrue((folder / "transcript.txt").is_file())
        self.assertFalse(updater.pending_file.exists())
        self.assert_blocked()

    def test_notification_failure_still_keeps_a_durable_notice(self):
        def unavailable(_):
            raise RuntimeError("Notification unavailable")
        error = subscription.CodexAccessBlocked("subscription_required")
        result = report_access_block(self.settings.incoming, error, unavailable)
        self.assertEqual(result, 20)
        self.assert_blocked()
        value = json.loads((self.settings.incoming / "status.json").read_text())
        self.assertFalse(value["notification_delivered"])

    def test_access_loss_between_videos_stops_before_the_next_download(self):
        from test_downloader import FakeSource
        source = FakeSource([{"id": "newVideo001", "title": "First"},
                             {"id": "newVideo002", "title": "Second"}])
        checker = Mock(side_effect=[allowed(),
                                   subscription.CodexAccessBlocked("subscription_required")])
        with self.assertRaises(subscription.CodexAccessBlocked):
            downloader.download_new(source, self.settings.incoming,
                                    {"baseline_video_ids": []}, before_download=checker)
        self.assertEqual(source.calls, ["newVideo001"])
        self.assertTrue((self.settings.incoming / "2026-10-24_newVideo001/transcript.txt").is_file())
        self.assertFalse((self.settings.incoming / "2026-10-24_newVideo002").exists())

    def test_maintenance_commands_do_not_require_model_access(self):
        self.assertFalse(self.pipeline.check()["pending"])
        self.assertEqual(self.pipeline.validate(panels=True)["sources"], 0)


if __name__ == "__main__":
    unittest.main()
