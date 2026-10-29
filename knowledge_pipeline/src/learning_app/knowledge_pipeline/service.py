"""Public integration boundary for the independent knowledge pipeline."""

from .settings import Settings, load_settings
from . import subscription
from .access_reporting import record_access_allowed, report_access_block


class KnowledgePipeline:
    """Coordinate one configured library without sharing per-run mutable state."""

    def __init__(self, settings: Settings | None = None, *, editor=None, access_checker=None):
        self.settings = settings if settings is not None else load_settings()
        self.editor = editor
        self.require_access = (access_checker if access_checker is not None else
                               lambda: subscription.require_codex_subscription(
                                   self.settings.editor_config()))

    def _updater(self, notifier=None):
        from .editor import CodexEditor
        from .synthesis import Updater

        settings = self.settings
        editor = (self.editor if self.editor is not None else
                  CodexEditor(settings.editor_config(), access_checker=self.require_access))
        return Updater(settings.incoming, settings.synthesis, settings.archive, editor=editor,
                       access_checker=self.require_access, notifier=notifier)

    def initialize(self):
        from .bootstrap import initialize_storage
        return initialize_storage(self.settings, access_checker=self.require_access)

    def capture_baseline(self):
        from .bootstrap import capture_baseline
        try:
            return capture_baseline(self.settings, access_checker=self.require_access)
        except subscription.CodexAccessBlocked as error:
            report_access_block(self.settings.incoming, error, operation="baseline")
            raise

    def access(self):
        access = self.require_access()
        record_access_allowed(self.settings.incoming, access, operation="access")
        return access.as_dict()

    def check(self):
        from .ecosystem import pending
        return {"pending": self._updater().has_pending() or pending(self.settings),
                "synthesis": str(self.settings.synthesis)}

    def _update_and_sync(self, notifier=None, *, download_lock_held=False):
        from . import ecosystem
        from .errors import SynthesisBusy

        try:
            processed = self._updater(notifier).run(download_lock_held=download_lock_held)
        except (subscription.CodexAccessBlocked, SynthesisBusy):
            raise
        except Exception:
            # Earlier transcripts in the same batch may already be committed.
            # Publish those if access and the source transaction still permit it.
            if ecosystem.project(self.settings) is not None:
                try:
                    self.require_access()
                    ecosystem.synchronize(self.settings)
                except Exception:
                    pass
            raise
        if ecosystem.project(self.settings) is not None:
            self.require_access()
            ecosystem.synchronize(self.settings)
        return processed

    def update(self):
        from .errors import SynthesisBusy
        try:
            return {"processed": self._update_and_sync()}
        except SynthesisBusy:
            return {"status": "busy", "processed": []}

    def validate(self, *, panels=False):
        from .archive import validate_archive

        index, manifest, _ = validate_archive(self.settings.archive, verify_panels=panels)
        return {"sources": len(index), "panel_rows": manifest["panel_row_count"],
                "pending": self._updater().has_pending()}

    def run(self, notifier=None):
        from .downloader import main
        from .notifications import LocalNotifier

        if notifier is None:
            notifier = LocalNotifier(self.settings.incoming)
        return main(notifier, settings=self.settings, access_checker=self.require_access,
                    synthesis_updater=lambda: self._update_and_sync(notifier, download_lock_held=True))

    def scheduled(self, *, notifier=None, now=None):
        from .scheduler import main
        from .ecosystem import pending

        return main(output=self.settings.incoming, runner=self.run, notifier=notifier,
                    now=now, pending_checker=lambda: self._updater().has_pending() or pending(self.settings),
                    access_checker=self.require_access)
