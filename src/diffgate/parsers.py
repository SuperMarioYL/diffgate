"""Language-aware symbol extraction via tree-sitter.

A single ``parse_symbols(blob, language)`` entry point returns a normalized
list of :class:`Symbol` records (functions / classes / methods) for the
languages DiffGate supports: Python, TypeScript / TSX / JavaScript, Go, Rust,
and — as of v0.3.0 — Java, C++, and Ruby.

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
    "java",
    "cpp",
    "ruby",
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
        # `export const handler = (req) => {}` / `const foo = function(){}`
        "variable_declarator": ("function", None),
    },
    "tsx": {
        "function_declaration": ("function", None),
        "class_declaration": ("class", None),
        "method_definition": ("method", None),
        "interface_declaration": ("class", None),
        "variable_declarator": ("function", None),
    },
    "javascript": {
        "function_declaration": ("function", None),
        "class_declaration": ("class", None),
        "method_definition": ("method", None),
        "variable_declarator": ("function", None),
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
    "java": {
        "method_declaration": ("function", None),
        "constructor_declaration": ("function", None),
        "class_declaration": ("class", None),
        "interface_declaration": ("class", None),
        "enum_declaration": ("class", None),
    },
    "cpp": {
        "function_definition": ("function", None),
        "class_specifier": ("class", None),
        "struct_specifier": ("class", None),
    },
    "ruby": {
        "method": ("function", None),
        "singleton_method": ("function", None),
        "class": ("class", None),
        "module": ("class", None),
    },
}

# Node types that are only emitted as a Symbol when they actually wrap a
# function value (arrow / function expression). Used to keep `const x = 5`
# from being mistaken for a callable in TS/JS.
_FUNCTION_VALUE_NODE_TYPES = {"arrow_function", "function_expression"}

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
            f"language {lang!r} not supported "
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
        # TS/JS `variable_declarator` is only a symbol when its value is an
        # arrow function or function expression — `const x = 5` is data, not
        # a callable. Resolve the function-bearing node so name/signature/body
        # extraction targets the right place.
        value_node = None
        if node.type == "variable_declarator":
            value_node = node.child_by_field_name("value")
            if value_node is None or value_node.type not in _FUNCTION_VALUE_NODE_TYPES:
                for child in node.children:
                    _walk(child, language, source, next_scope, out)
                return

        kind, _ = rule
        name = _find_name(node, source)
        if name:
            # A bare function_definition / function_declaration nested inside a
            # class is really a method; promote so the diff doesn't conflate
            # module-level and class-level functions with the same name.
            effective_kind = "method" if (kind == "function" and scope) else kind
            sig = _signature_text(value_node or node, source)
            body_h = _body_hash(value_node or node, source)
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
    # C++ function definitions bury the name inside the declarator chain
    # (function_definition → function_declarator → identifier|field_identifier).
    if node.type == "function_definition":
        name = _cpp_declarator_name(node, source)
        if name is not None:
            return name
    # Fallback: first identifier-ish child. Covers Go's type_spec where the
    # name lives one level deeper without a "name" field on every grammar
    # version, and Ruby's class/module whose name is a `constant`.
    for child in node.children:
        if child.type in {
            "identifier",
            "type_identifier",
            "property_identifier",
            "field_identifier",
            "constant",
        }:
            return _node_text(child, source)
    return None


def _cpp_declarator_name(node, source: bytes) -> str | None:
    """Walk the C++ declarator chain to the leaf name node."""
    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        if declarator.type in {"identifier", "field_identifier"}:
            return _node_text(declarator, source)
        if declarator.type in {"qualified_identifier", "destructor_name"}:
            return _node_text(declarator, source)
        declarator = declarator.child_by_field_name("declarator")
    return None


def _signature_text(node, source: bytes) -> str:
    params = node.child_by_field_name("parameters")
    if params is not None:
        return _node_text(params, source)
    # C++ exposes parameters on the inner function_declarator, not on the
    # function_definition itself.
    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        cpp_params = declarator.child_by_field_name("parameters")
        if cpp_params is not None:
            return _node_text(cpp_params, source)
        declarator = declarator.child_by_field_name("declarator")
    # Rust functions sometimes only expose the signature on a child node.
    sig_node = node.child_by_field_name("signature")
    if sig_node is not None:
        return _node_text(sig_node, source)
    return ""


def _body_hash(node, source: bytes) -> str:
    body = node.child_by_field_name("body")
    if body is None:
        # Arrow functions with an expression body (`x => x + 1`) have no
        # `body` field — hash the whole node so a changed expression still
        # registers as a body change.
        if node.type in _FUNCTION_VALUE_NODE_TYPES:
            return hashlib.sha256(_node_text(node, source).encode("utf-8")).hexdigest()[:12]
        return ""
    return hashlib.sha256(_node_text(body, source).encode("utf-8")).hexdigest()[:12]


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
