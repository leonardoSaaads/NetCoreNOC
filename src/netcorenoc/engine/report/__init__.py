"""The three deterministic CLI reports, compute half and render half alike.

Bias, champion agreement, and shadow mode. Each is a **gate** rather than a dashboard —
the suite compares its output byte for byte against a frozen expectation — and each is a
CLI deliverable by design, because a route would add HTTP surface to a scope bypass.

**Nothing imports this subpackage except `__main__.py`**, which is what makes it a leaf
and what put it first in v0.15.1's move order.
"""
