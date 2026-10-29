"""Persist and deliver a subscription-block notice without processing sources."""

import sys
from datetime import datetime, timezone
from pathlib import Path

from .storage import atomic, safe, write_json
from .errors import SynthesisError
from .subscription import CodexAccess, CodexAccessBlocked

REPORTED_BLOCK = 10
UNREPORTED_BLOCK = 20


def record_access_allowed(output, access, *, operation="automation"):
    if not isinstance(access, CodexAccess):
        raise CodexAccessBlocked("access_unverified")
    output = Path(output)
    safe(output)
    if output.is_dir():
        write_json(output / "access_state.json", {**access.as_dict(), "operation": operation})


def report_access_block(output, error, notifier=None, *, operation="automation"):
    if error.reported:
        return error.exit_code
    output = Path(output)
    message = str(error)
    status = {
        "status": "blocked",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "block_reason": error.reason,
        "operation": operation,
        "error": message,
        "notification_delivered": False,
    }
    print(message, file=sys.stderr, flush=True)
    output_ok = False
    try:
        safe(output)
        output.mkdir(parents=True, exist_ok=True)
        safe(output)
        atomic(output / "ALERT.txt", (
            "AUTOMATIZACE POZASTAVENA — CODEX\n"
            + status["checked_at_utc"] + "\n\n" + message + "\n"
        ).encode("utf-8"))
        output_ok = True
    except (OSError, SynthesisError):
        pass
    try:
        if notifier is None:
            from .notifications import LocalNotifier
            notifier = LocalNotifier(output)
        if hasattr(notifier, "show"):
            delivery = notifier.show("Automatizace pozastavena — Codex", message,
                                     "JSCodexAccess")
        else:
            delivery = notifier(message)
        status["notification_delivered"] = True
        if isinstance(delivery, dict):
            status["notification_delivery"] = delivery
    except Exception:
        pass
    if output_ok:
        try:
            write_json(output / "access_state.json", status)
            write_json(output / "status.json", status)
        except (OSError, SynthesisError):
            pass
    error.reported = True
    error.exit_code = REPORTED_BLOCK if status["notification_delivered"] else UNREPORTED_BLOCK
    return error.exit_code
