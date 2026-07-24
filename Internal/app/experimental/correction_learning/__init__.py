"""Clean-room automatic correction learning for Mumble.

Importing this experimental package has no side effects and loads no optional
Windows automation dependency until monitoring is explicitly started.
"""

from .engine import analyze_correction
from .manager import CorrectionLearningManager
from .monitor import InsertedSpanTracker, UIAEditMonitor

__all__ = [
    "analyze_correction",
    "CorrectionLearningManager",
    "InsertedSpanTracker",
    "UIAEditMonitor",
]
