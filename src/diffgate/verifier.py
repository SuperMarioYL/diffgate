"""Core ``EditClaim → Verdict`` verifier.

This is the only module DiffGate's CLI, MCP server, and bench mode all import.
Keep it dependency-light (just :mod:`parsers`) so it can be reused as a
library in other agent-loop harnesses.

The verifier is deliberately *deterministic*: given the same blobs and the
same claim, it always returns the same verdict. No LLMs, no probabilistic
matching — the whole pitch is "structural backpressure beats a smarter
judge."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import parsers
from .parsers import Symbol

VALID_KINDS = frozenset({"rename", "add", "delete", "move", "signature_change"})


@dataclass
class ClaimedAction:
    """A single thing the agent says it did to the file.

    ``symbol`` is the source name for renames/moves and the affected name for
    add/delete/signature_change. For renames, ``new_symbol`` carries the
    target; callers may also use the shorthand ``"old->new"`` (or
    ``"old→new"``) in ``symbol`` and we'll split it.
    """

    kind: str
    symbol: str
    scope: str = ""
    new_symbol: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaimedAction:
        kind = data["kind"]
        if kind not in VALID_KINDS:
            raise ValueError(
                f"claimed action kind {kind!r} not in {sorted(VALID_KINDS)}"
            )
        symbol = data["symbol"]
        scope = data.get("scope", "") or ""
        new_symbol = data.get("new_symbol", "") or ""
        if not new_symbol and kind in {"rename", "move"}:
            for sep in ("->", "→", "=>"):
                if sep in symbol:
                    left, _, right = symbol.partition(sep)
                    symbol, new_symbol = left.strip(), right.strip()
                    break
        return cls(kind=kind, symbol=symbol, scope=scope, new_symbol=new_symbol)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "scope": self.scope,
            "new_symbol": self.new_symbol,
        }


@dataclass
class EditClaim:
    """Everything the verifier needs to judge one edit."""

    before_blob: str
    after_blob: str
    language: str
    claimed_actions: list[ClaimedAction] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EditClaim:
        return cls(
            before_blob=data["before_blob"],
            after_blob=data["after_blob"],
            language=data.get("language", "python"),
            claimed_actions=[
                ClaimedAction.from_dict(a) for a in data.get("claimed_actions", [])
            ],
        )


@dataclass
class Mismatch:
    """One claimed action that didn't survive the structural check."""

    action: ClaimedAction
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.action.kind,
            "symbol": self.action.symbol,
            "new_symbol": self.action.new_symbol,
            "scope": self.action.scope,
            "reason": self.reason,
        }


@dataclass
class StructuralDiff:
    """What the AST diff actually shows, independent of any claim."""

    added: list[Symbol] = field(default_factory=list)
    deleted: list[Symbol] = field(default_factory=list)
    signature_changed: list[tuple[Symbol, Symbol]] = field(default_factory=list)
    body_changed: list[tuple[Symbol, Symbol]] = field(default_factory=list)
    unchanged: list[Symbol] = field(default_factory=list)

    def is_noop(self) -> bool:
        return not (
            self.added
            or self.deleted
            or self.signature_changed
            or self.body_changed
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": [_sym_dict(s) for s in self.added],
            "deleted": [_sym_dict(s) for s in self.deleted],
            "signature_changed": [
                {"before": _sym_dict(b), "after": _sym_dict(a)}
                for b, a in self.signature_changed
            ],
            "body_changed": [
                {"before": _sym_dict(b), "after": _sym_dict(a)}
                for b, a in self.body_changed
            ],
            "unchanged_count": len(self.unchanged),
        }


@dataclass
class Verdict:
    """The verifier's final answer."""

    passed: bool
    mismatches: list[Mismatch] = field(default_factory=list)
    structural_diff: StructuralDiff = field(default_factory=StructuralDiff)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "mismatches": [m.to_dict() for m in self.mismatches],
            "structural_diff": self.structural_diff.to_dict(),
        }


def verify(claim: EditClaim) -> Verdict:
    """Run the structural gate on a single :class:`EditClaim`."""
    before_symbols = parsers.parse_symbols(claim.before_blob, claim.language)
    after_symbols = parsers.parse_symbols(claim.after_blob, claim.language)
    diff = _compute_diff(before_symbols, after_symbols)

    mismatches: list[Mismatch] = []
    for action in claim.claimed_actions:
        m = _check_action(action, diff, before_symbols, after_symbols)
        if m is not None:
            mismatches.append(m)

    # If the agent claimed *anything* but the AST shows zero changes, treat
    # that as a silent-success lie even if a per-action check happened to pass.
    if claim.claimed_actions and diff.is_noop() and not mismatches:
        mismatches.append(
            Mismatch(
                action=claim.claimed_actions[0],
                reason="no structural change detected — claimed edit appears to be a no-op",
            )
        )

    return Verdict(
        passed=not mismatches,
        mismatches=mismatches,
        structural_diff=diff,
    )


def compute_diff(before_blob: str, after_blob: str, language: str) -> StructuralDiff:
    """Return the structural AST diff between two source blobs.

    This is the diff half of the §2 "Core data primitive" — ``verify`` checks a
    claim against this diff, but an agent (or a CI audit step) can also inspect the
    diff directly to self-correct a failed claim or to generate a truthful
    ``claimed_actions`` payload before staking one. Thin public wrapper over the
    private :func:`_compute_diff` so the diff stays computed by exactly one code
    path; no new verification logic.
    """
    before_symbols = parsers.parse_symbols(before_blob, language)
    after_symbols = parsers.parse_symbols(after_blob, language)
    return _compute_diff(before_symbols, after_symbols)


@dataclass
class FileClaim:
    """One file's worth of an edit that spans multiple files.

    ``path`` is the human-facing label (used to tag mismatches so a failing
    Verdict names the offending file); ``claim`` is the per-file
    :class:`EditClaim` the core verifier runs.
    """

    path: str
    claim: EditClaim


def verify_multi(file_claims: list[FileClaim]) -> Verdict:
    """Gate a multi-file edit in one call.

    Each file is verified independently with :func:`verify`; the per-file
    mismatches are merged into a single :class:`Verdict` whose reasons are
    prefixed with the originating file path, and the structural diffs are
    aggregated so the caller sees the combined delta. The Verdict passes only
    if every file passes.
    """
    mismatches: list[Mismatch] = []
    combined = StructuralDiff()
    for fc in file_claims:
        verdict = verify(fc.claim)
        for m in verdict.mismatches:
            mismatches.append(
                Mismatch(action=m.action, reason=f"[{fc.path}] {m.reason}")
            )
        d = verdict.structural_diff
        combined.added.extend(d.added)
        combined.deleted.extend(d.deleted)
        combined.signature_changed.extend(d.signature_changed)
        combined.body_changed.extend(d.body_changed)
        combined.unchanged.extend(d.unchanged)
    return Verdict(passed=not mismatches, mismatches=mismatches, structural_diff=combined)


def _compute_diff(before: list[Symbol], after: list[Symbol]) -> StructuralDiff:
    # Key by (scope, name, kind) so a class and a function with the same name
    # don't accidentally collide. Multiple symbols at the same key (e.g.
    # overloaded methods) are folded into a list.
    before_by_key: dict[tuple[str, str, str], list[Symbol]] = {}
    after_by_key: dict[tuple[str, str, str], list[Symbol]] = {}
    for s in before:
        before_by_key.setdefault((s.scope, s.name, s.kind), []).append(s)
    for s in after:
        after_by_key.setdefault((s.scope, s.name, s.kind), []).append(s)

    added: list[Symbol] = []
    deleted: list[Symbol] = []
    signature_changed: list[tuple[Symbol, Symbol]] = []
    body_changed: list[tuple[Symbol, Symbol]] = []
    unchanged: list[Symbol] = []

    for key, after_syms in after_by_key.items():
        if key not in before_by_key:
            added.extend(after_syms)
    for key, before_syms in before_by_key.items():
        if key not in after_by_key:
            deleted.extend(before_syms)

    for key in set(before_by_key) & set(after_by_key):
        before_syms = before_by_key[key]
        after_syms = after_by_key[key]
        _diff_symbol_group(
            before_syms,
            after_syms,
            added=added,
            deleted=deleted,
            signature_changed=signature_changed,
            body_changed=body_changed,
            unchanged=unchanged,
        )

    return StructuralDiff(
        added=added,
        deleted=deleted,
        signature_changed=signature_changed,
        body_changed=body_changed,
        unchanged=unchanged,
    )


def _diff_symbol_group(
    before_syms: list[Symbol],
    after_syms: list[Symbol],
    *,
    added: list[Symbol],
    deleted: list[Symbol],
    signature_changed: list[tuple[Symbol, Symbol]],
    body_changed: list[tuple[Symbol, Symbol]],
    unchanged: list[Symbol],
) -> None:
    """Diff the symbols sharing one ``(scope, name, kind)`` key.

    The common case is exactly one symbol on each side, but overloaded
    same-name methods (C++ ``int f(int)`` + ``double f(double)``, Java
    ``int f(int)`` + ``String f(String)``) fold multiple symbols into one
    group. Pairing those **positionally** is a silent-lie bug: tree-sitter
    emits overloads in source order, so a pure reorder (or deleting one
    overload) re-aligns the lists and mis-pairs ``f(int)`` against
    ``f(double)``, fabricating ``signature_changed`` / ``body_changed``
    entries for an edit that changed nothing — which lets a LYING
    ``signature_change`` claim pass because the AST *appears* to have moved.

    Fix: pair by **content** first. An after-symbol whose ``signature``
    matches a not-yet-consumed before-symbol is the same overload regardless
    of order → unchanged (or a body change if the body differs). What's left
    after content matching is the genuine signature change / add / delete,
    paired positionally as a last resort so a real same-name signature edit
    still surfaces as ``signature_changed`` rather than a delete+add pair.
    """
    # Fast path: the overwhelmingly common 1:1 case keeps the original
    # positional semantics exactly.
    if len(before_syms) <= 1 and len(after_syms) <= 1:
        _pair_positionally(
            before_syms,
            after_syms,
            added=added,
            deleted=deleted,
            signature_changed=signature_changed,
            body_changed=body_changed,
            unchanged=unchanged,
        )
        return

    remaining_before = list(before_syms)
    remaining_after: list[Symbol] = []

    # Pass 1: exact (signature, body_hash) match — a truly unchanged overload,
    # paired with its twin no matter where it sits in source order.
    for a in after_syms:
        match = _take_match(
            remaining_before,
            lambda b, a=a: b.signature == a.signature and b.body_hash == a.body_hash,
        )
        if match is not None:
            unchanged.append(a)
        else:
            remaining_after.append(a)

    # Pass 2: same signature, different body — the same overload with an
    # edited body (a real body change, not a fabricated signature change).
    still_after: list[Symbol] = []
    for a in remaining_after:
        match = _take_match(remaining_before, lambda b, a=a: b.signature == a.signature)
        if match is not None:
            body_changed.append((match, a))
        else:
            still_after.append(a)

    # Whatever is left can't be content-matched: pair positionally so a
    # genuine signature change on a same-name symbol still reads as
    # signature_changed, and any surplus is a real add/delete.
    _pair_positionally(
        remaining_before,
        still_after,
        added=added,
        deleted=deleted,
        signature_changed=signature_changed,
        body_changed=body_changed,
        unchanged=unchanged,
    )


def _take_match(pool: list[Symbol], pred) -> Symbol | None:
    """Pop and return the first symbol in ``pool`` satisfying ``pred``, or None."""
    for i, sym in enumerate(pool):
        if pred(sym):
            return pool.pop(i)
    return None


def _pair_positionally(
    before_syms: list[Symbol],
    after_syms: list[Symbol],
    *,
    added: list[Symbol],
    deleted: list[Symbol],
    signature_changed: list[tuple[Symbol, Symbol]],
    body_changed: list[tuple[Symbol, Symbol]],
    unchanged: list[Symbol],
) -> None:
    """Pair two symbol lists by index; surplus on either side is add/delete."""
    pair_count = min(len(before_syms), len(after_syms))
    for b, a in zip(before_syms[:pair_count], after_syms[:pair_count], strict=False):
        if b.signature != a.signature:
            signature_changed.append((b, a))
        elif b.body_hash != a.body_hash:
            body_changed.append((b, a))
        else:
            unchanged.append(a)
    added.extend(after_syms[pair_count:])
    deleted.extend(before_syms[pair_count:])


def _scope_matches(claimed_scope: str, symbol: Symbol) -> bool:
    """Whether ``symbol`` lives in the scope the claim pinned.

    An empty ``claimed_scope`` is a wildcard: the claim doesn't care where the
    symbol lives, so any scope matches (this preserves the v0.1 name-only
    behaviour for unscoped claims). When the claim *does* name a scope —
    e.g. ``"MyClass"`` for a method, or ``""`` is the module level — the
    symbol's own ``scope`` must match exactly. This is what stops an agent
    from claiming ``add MyClass.helper`` and getting a pass when it actually
    dropped a module-level ``helper`` instead.
    """
    if not claimed_scope:
        return True
    return symbol.scope == claimed_scope


def _scope_label(scope: str) -> str:
    """Human-readable scope name for mismatch reasons."""
    return scope or "<module>"


def _check_action(
    action: ClaimedAction,
    diff: StructuralDiff,
    before: list[Symbol],
    after: list[Symbol],
) -> Mismatch | None:
    if action.kind == "rename":
        return _check_rename(action, diff)
    if action.kind == "add":
        return _check_add(action, diff)
    if action.kind == "delete":
        return _check_delete(action, diff, after)
    if action.kind == "signature_change":
        return _check_signature_change(action, diff)
    if action.kind == "move":
        return _check_move(action, before, after)
    return Mismatch(action, f"unknown action kind {action.kind!r}")


def _check_rename(action: ClaimedAction, diff: StructuralDiff) -> Mismatch | None:
    if not action.new_symbol:
        return Mismatch(action, "rename claim missing target name (use 'old→new')")
    old_deleted = any(
        s.name == action.symbol and _scope_matches(action.scope, s)
        for s in diff.deleted
    )
    new_added = any(
        s.name == action.new_symbol and _scope_matches(action.scope, s)
        for s in diff.added
    )
    if not old_deleted and not new_added:
        return Mismatch(
            action,
            f"claimed rename {action.symbol}→{action.new_symbol} but neither "
            f"name appears in the structural diff (no-op edit)",
        )
    if not old_deleted:
        return Mismatch(
            action,
            f"claimed rename: original '{action.symbol}' is still present in "
            f"the after-blob",
        )
    if not new_added:
        return Mismatch(
            action,
            f"claimed rename: target '{action.new_symbol}' not found among "
            f"newly added symbols",
        )
    return None


def _check_add(action: ClaimedAction, diff: StructuralDiff) -> Mismatch | None:
    if any(
        s.name == action.symbol and _scope_matches(action.scope, s)
        for s in diff.added
    ):
        return None
    # A same-named symbol added in a *different* scope is a common silent lie:
    # the agent claims it added a method on a class but actually added a free
    # function (or vice versa). Call that out specifically.
    if action.scope and any(s.name == action.symbol for s in diff.added):
        return Mismatch(
            action,
            f"claimed add: '{action.symbol}' was added, but not in scope "
            f"'{_scope_label(action.scope)}'",
        )
    return Mismatch(
        action,
        f"claimed add: '{action.symbol}' not found among newly added symbols",
    )


def _check_delete(
    action: ClaimedAction, diff: StructuralDiff, after: list[Symbol]
) -> Mismatch | None:
    if any(
        s.name == action.symbol and _scope_matches(action.scope, s)
        for s in diff.deleted
    ):
        return None
    if any(
        s.name == action.symbol and _scope_matches(action.scope, s) for s in after
    ):
        scope_note = (
            f" in scope '{_scope_label(action.scope)}'" if action.scope else ""
        )
        return Mismatch(
            action,
            f"claimed delete: '{action.symbol}'{scope_note} is still present in "
            f"the after-blob",
        )
    return Mismatch(
        action,
        f"claimed delete: '{action.symbol}' was not present in either blob "
        f"(no-op)",
    )


def _check_signature_change(
    action: ClaimedAction, diff: StructuralDiff
) -> Mismatch | None:
    if any(
        a.name == action.symbol and _scope_matches(action.scope, a)
        for _, a in diff.signature_changed
    ):
        return None
    # The signature of a same-named symbol changed, but in another scope.
    if action.scope and any(
        a.name == action.symbol for _, a in diff.signature_changed
    ):
        return Mismatch(
            action,
            f"claimed signature_change: signature of '{action.symbol}' in scope "
            f"'{_scope_label(action.scope)}' is unchanged "
            f"(a same-named symbol changed elsewhere)",
        )
    return Mismatch(
        action,
        f"claimed signature_change: signature of '{action.symbol}' is "
        f"unchanged between the two blobs",
    )


def _check_move(
    action: ClaimedAction,
    before: list[Symbol],
    after: list[Symbol],
) -> Mismatch | None:
    before_hits = [s for s in before if s.name == action.symbol]
    after_hits = [s for s in after if s.name == action.symbol]
    if not before_hits:
        return Mismatch(
            action,
            f"claimed move: '{action.symbol}' not present in before-blob",
        )
    if not after_hits:
        return Mismatch(
            action,
            f"claimed move: '{action.symbol}' missing from after-blob "
            f"(looks like a delete, not a move)",
        )
    before_scopes = {s.scope for s in before_hits}
    after_scopes = {s.scope for s in after_hits}
    left = before_scopes - after_scopes
    landed = after_scopes - before_scopes
    # A genuine move must BOTH vacate at least one scope AND land in a new one.
    # If the symbol only left scopes (a duplicate was deleted) or only gained
    # scopes (a copy was added) — or the scope sets are identical — nothing
    # actually moved. A pure set-inequality from a dropped duplicate, e.g.
    # module-level `foo` removed while `A.foo` stays, is NOT a move.
    if not (left and landed):
        return Mismatch(
            action,
            f"claimed move: scope of '{action.symbol}' did not change as a move "
            f"(before {sorted(before_scopes) or ['<module>']}, "
            f"after {sorted(after_scopes) or ['<module>']}); a same-named copy "
            f"was added or removed, not relocated",
        )
    # For ``move``, ``scope`` names the *destination* the agent claims to have
    # moved the symbol into. If it's set, the symbol must actually land there —
    # otherwise the agent moved it somewhere it didn't claim.
    if action.scope and action.scope not in after_scopes:
        return Mismatch(
            action,
            f"claimed move: '{action.symbol}' did not land in scope "
            f"'{_scope_label(action.scope)}' "
            f"(now in {sorted(after_scopes) or ['<module>']})",
        )
    return None


def _sym_dict(s: Symbol) -> dict[str, Any]:
    return {
        "name": s.name,
        "kind": s.kind,
        "scope": s.scope,
        "signature": s.signature,
        "line": s.line,
    }
