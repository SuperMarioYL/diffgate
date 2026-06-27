"""DiffGate — structural verification gate for coding agents.

DiffGate sits between a coding agent's tool call and the next loop iteration.
It parses the post-edit file, compares it to the agent's claimed actions, and
fails the step on mismatch — turning silent "I renamed foo to bar" lies into
loud, retry-triggering errors.

Public surface:
    from diffgate import verify, EditClaim, Verdict
"""

from __future__ import annotations

__version__ = "0.4.0"

__all__ = [
    "__version__",
    "EditClaim",
    "Verdict",
    "verify",
]


def __getattr__(name: str):
    # Lazy re-exports so importing the package doesn't pull tree-sitter at
    # import time (keeps `diffgate --help` snappy and avoids hard import
    # failures before optional native deps are wired up in later stages).
    if name in {"EditClaim", "Verdict", "verify"}:
        from . import verifier  # noqa: PLC0415

        return getattr(verifier, name)
    raise AttributeError(f"module 'diffgate' has no attribute {name!r}")
