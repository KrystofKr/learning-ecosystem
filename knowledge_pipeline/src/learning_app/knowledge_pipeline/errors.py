"""Errors shared by the knowledge updater and its editor adapter."""

class SynthesisError(RuntimeError):
    pass

class SynthesisBusy(SynthesisError):
    pass
