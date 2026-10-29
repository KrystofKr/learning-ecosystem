"""Fresh Codex subscription checks without generating a model turn.

Only documented app-server account methods are used. Credentials, account
identifiers and raw RPC responses are never returned or included in errors.
"""

import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import SynthesisError

PAID_PLANS = frozenset({
    "go", "plus", "pro", "prolite", "promax", "team", "business",
    "self_serve_business_prolite", "ent26", "enterprise",
    "edu", "edu_plus", "edu_pro",
})
CHECK_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1024 * 1024

MESSAGES = {
    "subscription_required": (
        "Automatizace je zastavena: Codex nepotvrdil aktivní placené předplatné. "
        "Obnov předplatné a potom spusť learning-knowledge access."
    ),
    "authentication_required": (
        "Automatizace je zastavena: Codex nemá přihlášený účet ChatGPT. "
        "Přihlas se pomocí codex login a spusť learning-knowledge access."
    ),
    "unsupported_auth": (
        "Automatizace je zastavena: přístup k Codexu není ověřený přes účet ChatGPT "
        "s placeným plánem. Přihlášení přes API klíč se pro tuto automatizaci nepoužije."
    ),
    "access_unverified": (
        "Automatizace je pozastavena: předplatné Codex se nepodařilo spolehlivě ověřit. "
        "Může jít o síť, přihlášení nebo nepodporované rozhraní CLI. "
        "Zpracování se nespustí; zkus learning-knowledge access."
    ),
    "quota_exhausted": (
        "Automatizace je pozastavena: Codex hlásí vyčerpaný limit nebo omezení čerpání. "
        "Po obnovení dostupného limitu spusť learning-knowledge access. "
        "Na placené API se nepřechází."
    ),
    "codex_unavailable": (
        "Automatizace je zastavena: nakonfigurovaný Codex CLI není dostupný. "
        "Ověř jeho instalaci a potom spusť learning-knowledge access."
    ),
}


class CodexAccessBlocked(SynthesisError):
    def __init__(self, reason):
        self.reason = reason
        self.reported = False
        self.exit_code = 20
        super().__init__(MESSAGES[reason])


@dataclass(frozen=True)
class CodexAccess:
    plan_type: str
    checked_at_utc: str

    def as_dict(self):
        return {"status": "allowed", "plan_type": self.plan_type,
                "checked_at_utc": self.checked_at_utc}


def evaluate_access(account_result, limits_result):
    """Trust a live quota plan, never a cached login or token claim alone."""
    if not isinstance(account_result, dict):
        raise CodexAccessBlocked("access_unverified")
    account = account_result.get("account")
    if account is None:
        raise CodexAccessBlocked("authentication_required")
    if not isinstance(account, dict):
        raise CodexAccessBlocked("access_unverified")
    if account.get("type") != "chatgpt":
        raise CodexAccessBlocked("unsupported_auth")
    if not isinstance(limits_result, dict):
        raise CodexAccessBlocked("access_unverified")
    buckets = limits_result.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else None
    if bucket is None:
        candidate = limits_result.get("rateLimits")
        if isinstance(candidate, dict) and candidate.get("limitId") in (None, "codex"):
            bucket = candidate
    if not isinstance(bucket, dict):
        raise CodexAccessBlocked("access_unverified")
    plan = bucket.get("planType")
    if not isinstance(plan, str):
        raise CodexAccessBlocked("access_unverified")
    if plan == "free":
        raise CodexAccessBlocked("subscription_required")
    if plan not in PAID_PLANS:
        raise CodexAccessBlocked("access_unverified")
    if bucket.get("spendControlReached") is True or bucket.get("rateLimitReachedType"):
        raise CodexAccessBlocked("quota_exhausted")
    for name in ("primary", "secondary"):
        window = bucket.get(name)
        if window is None:
            continue
        if not isinstance(window, dict):
            raise CodexAccessBlocked("access_unverified")
        used = window.get("usedPercent")
        if type(used) is not int or used < 0:
            raise CodexAccessBlocked("access_unverified")
        if used >= 100:
            raise CodexAccessBlocked("quota_exhausted")
    return CodexAccess(plan, datetime.now(timezone.utc).isoformat())


def subscription_environment():
    environment = dict(os.environ)
    # Subscription access must not silently select a billable API credential.
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
        environment.pop(key, None)
    return environment


class _AccountRpc:
    def __init__(self, command, cwd, timeout):
        self.deadline = time.monotonic() + timeout
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=cwd, env=subscription_environment(),
            start_new_session=True,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""
        self.received = 0
        self.next_id = 0

    def send(self, value):
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        self.process.stdin.flush()

    def request(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        request = {"id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        self.send(request)
        while True:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                response = json.loads(line)
                if not isinstance(response, dict):
                    raise ValueError("Malformed account response")
                if response.get("id") == request_id:
                    if "error" in response or not isinstance(response.get("result"), dict):
                        raise ValueError("Account request failed")
                    return response["result"]
                # Account notifications can contain identity data; discard them.
            remaining = self.deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise TimeoutError("Codex account check timed out")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise EOFError("Codex account server stopped")
            self.received += len(chunk)
            if self.received > MAX_RESPONSE_BYTES:
                raise ValueError("Codex account response exceeded its bound")
            self.buffer += chunk

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


def require_codex_subscription(config=None, *, timeout=CHECK_TIMEOUT_SECONDS):
    if config is None:
        from .settings import load_settings
        config = load_settings().editor_config()
    binary = Path(shutil.which(config["codex_binary"]) or config["codex_binary"])
    if not binary.is_file():
        raise CodexAccessBlocked("codex_unavailable")
    try:
        with tempfile.TemporaryDirectory(prefix="learning-codex-account-") as temporary:
            command = [
                str(binary), "--no-daemon",
                "-c", "sqlite_home=" + json.dumps(temporary),
                "-c", "log_dir=" + json.dumps(str(Path(temporary) / "logs")),
                "-c", "features.apps=false", "-c", "features.multi_agent=false",
                "-c", "features.shell_tool=false", "-c", 'web_search="disabled"',
                "-c", 'model_provider="openai"',
                "app-server", "--listen", "stdio://",
            ]
            rpc = _AccountRpc(command, temporary, timeout)
            try:
                rpc.request("initialize", {"clientInfo": {
                    "name": "learning_app_subscription_check",
                    "title": "Learning App subscription check", "version": "0.1.0",
                }})
                rpc.send({"method": "initialized", "params": {}})
                account = rpc.request("account/read", {"refreshToken": True})
                # Reject non-ChatGPT auth before making any quota request.
                identity = account.get("account")
                if identity is None:
                    raise CodexAccessBlocked("authentication_required")
                if not isinstance(identity, dict):
                    raise CodexAccessBlocked("access_unverified")
                if identity.get("type") != "chatgpt":
                    raise CodexAccessBlocked("unsupported_auth")
                limits = rpc.request("account/rateLimits/read")
                return evaluate_access(account, limits)
            finally:
                rpc.close()
    except CodexAccessBlocked:
        raise
    except (OSError, ValueError, TypeError, EOFError, TimeoutError,
            subprocess.SubprocessError):
        raise CodexAccessBlocked("access_unverified") from None
