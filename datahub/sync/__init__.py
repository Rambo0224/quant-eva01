"""Independent acquisition, raw storage and canonical publication module."""


def run_pipeline(end, datasets=None, symbols=None):
    """Public API; see workflow.run_pipeline for result schema."""
    from .workflow import run_pipeline as run
    return run(end, datasets, symbols)
