"""Input layer for evidence-based career profiling.

Two guarantees, in order of importance:

1. Nothing leaves this machine un-redacted. ``career_evidence.redact`` fails closed.
2. Nothing enters a profile without a checkable quote. ``career_evidence.cards``
   verifies every claim against the exact text the model was shown.
"""

__version__ = "0.1.0"
