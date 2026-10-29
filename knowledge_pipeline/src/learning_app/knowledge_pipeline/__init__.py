"""Independent transcript collection and synthesis component.

Importing this package does not start downloads, model calls or notifications.
Use KnowledgePipeline for integration and the module CLI for standalone runs.
"""

from .service import KnowledgePipeline

__all__ = ["KnowledgePipeline"]
