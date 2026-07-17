"""Built-in decision makers for the pluggable DecisionRegistry.

Importing this module triggers the ``@DecisionRegistry.register`` decorators
on all included maker classes, making them available to the hybrid agent.
"""

from __future__ import annotations

# Import all maker modules to trigger @DecisionRegistry.register decorators
from . import api_maker  # noqa: F401
from . import bus_multi_maker  # noqa: F401
from . import multi_maker  # noqa: F401
from . import rule_maker  # noqa: F401

__all__ = [
    "api_maker",
    "multi_maker",
    "rule_maker",
]
