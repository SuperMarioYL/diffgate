"""Language-aware symbol extraction via tree-sitter.

A single ``parse_symbols(blob, language)`` entry point returns a normalized
list of :class:`Symbol` records (functions / classes / methods) for the four
languages DiffGate v0.1 supports: Python, TypeScript, Go, Rust.

The verifier diffs two such lists to decide whether an agent's claimed edit
actually happened. We deliberately keep the parser small — no semantic
resolution, no cross-file imports — because the gate only needs *structural*
backpressure, not a type checker.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from tree_sitter_language_pack import get_parser

SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "python",
    "typescript",
    "tsx",
    "javascript",
    "go",
    "rust",
)

# node_type -> (kind, _placeholder). The placeholder slot is reserved for
# future per-node specialization (e.g. distinguishing struct vs interface in
# Go); v0.1 only needs the kind.
LANGUAGE_RULES: dict[str, dict[str, tuple[str, None]]] = {
    "python": {
        "function_definition": ("function", None),
        "class_definition": ("class", None),
    },
    "typescript": {
        "function_declaration": ("function", None),
        "class_declaration": ("class", None),
        "method_definition": ("method", None),
        "method_signature": ("method", None),
        "interface_declaration": ("class", None),
    },
    "tsx": {
        "function_declaration": ("function", None),
        "class_declaration": ("class", None),
        "method_definition": ("method", None),
        "interface_declaration": ("class", None),
    },
    "javascript": {
        "function_declaration": ("function", None),
        "class_declaration": ("class", None),
        "method_definition": ("method", None),
    },
    "go": {
        "function_declaration": ("function", None),
        "method_declaration": ("method", None),
        "type_spec": ("class", None),
    },
    "rust": {
        "function_item": ("function", None),
        "struct_item": ("class", None),
        "enum_item": ("class", None),
        "trait_item": ("class", None),
    },
}

# Node types whose name should be propagated as a scope prefix to children.
SCOPE_NODE_KINDS = {"class", "method"}


class UnsupportedLanguageError(ValueError):
    """Raised when ``parse_symbols`` is called with an unknown language id."""


@dataclass(frozen=True)
class Symbol:
    """A single parsed declaration the verifier can reason about."""

    name: str
    kind: str  # "function" | "method" | "class"
    signature: str  # parameter-list text, empty for non-callables
    scope: str  # dotted parent path (e.g. "MyClass" for methods)
    body_hash: str  # 12-hex-char sha256 prefix of the body bytes
    line: int  # 1-based start line

    @property
    def fqn(self) -> str:
        """Fully-qualified name: ``Scope.name`` or just ``name`` at module level."""
        return f"{self.scope}.{self.name}" if self.scope else self.name


def parse_symbols(blob: str, language: str) -> list[Symbol]:
    """Extract declarations from ``blob`` written in ``language``.

    Raises :class:`UnsupportedLanguageError` if the language id isn't in
    :data:`SUPPORTED_LANGUAGES`.
    """
    lang = language.lower().strip()
    if lang not in LANGUAGE_RULES:
        raise UnsupportedLanguageError(
            f"language {lang!r} not supported in v0.1 "
            f"(supported: {', '.join(sorted(LANGUAGE_RULES))})"
        )

    parser = get_parser(lang)
    source = blob.encode("utf-8")
    tree = parser.parse(source)

    out: list[Symbol] = []
    _walk(tree.root_node, lang, source, scope="", out=out)
    return out


def _walk(node, language: str, source: bytes, scope: str, out: list[Symbol]) -> None:
    rules = LANGUAGE_RULES[language]
    rule = rules.get(node.type)

    next_scope = scope
    if rule is not None:
        kind, _ = rule
        name = _find_name(node, source)
        if name:
            # A bare function_definition / function_declaration nested inside a
            # class is really a method; promote so the diff doesn't conflate
            # module-level and class-level functions with the same name.
            effective_kind = "method" if (kind == "function" and scope) else kind
            sig = _signature_text(node, source)
            body_h = _body_hash(node, source)
            out.append(
                Symbol(
                    name=name,
                    kind=effective_kind,
                    signature=sig,
                    scope=scope,
                    body_hash=body_h,
                    line=node.start_point[0] + 1,
                )
            )
            if effective_kind in SCOPE_NODE_KINDS:
                next_scope = f"{scope}.{name}" if scope else name

    for child in node.children:
        _walk(child, language, source, next_scope, out)


def _find_name(node, source: bytes) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return _node_text(name_node, source)
    # Fallback: first identifier-ish child. Covers Go's type_spec where the
    # name lives one level deeper without a "name" field on every grammar
    # version.
    for child in node.children:
        if child.type in {"identifier", "type_identifier", "property_identifier"}:
            return _node_text(child, source)
    return None


def _signature_text(node, source: bytes) -> str:
    params = node.child_by_field_name("parameters")
    if params is not None:
        return _node_text(params, source)
    # Rust functions sometimes only expose the signature on a child node.
    sig_node = node.child_by_field_name("signature")
    if sig_node is not None:
        return _node_text(sig_node, source)
    return ""


def _body_hash(node, source: bytes) -> str:
    body = node.child_by_field_name("body")
    if body is None:
        return ""
    return hashlib.sha256(_node_text(body, source).encode("utf-8")).hexdigest()[:12]


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
