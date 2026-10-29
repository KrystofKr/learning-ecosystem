"""Replace the external Codex account service in unrelated local tests."""

from unittest.mock import patch

from learning_app.knowledge_pipeline.subscription import CodexAccess


def permit_codex_access(test_case):
    check = patch(
        "learning_app.knowledge_pipeline.subscription.require_codex_subscription",
        return_value=CodexAccess("pro", "2026-10-26T08:00:00+00:00"),
    )
    check.start()
    test_case.addCleanup(check.stop)
