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
        # `class Handler { handle = (req) => {} }` — the class-property arrow-function
        # form (the canonical bound-callback pattern in Angular/NestJS controllers,
        # Three.js scene classes, React class components). Without it the property is
        # invisible and a truthful scoped `add handle scope=Handler` false-fails.
        "public_field_definition": ("function", None),
    },
    "tsx": {
        "function_declaration": ("function", None),
        "class_declaration": ("class", None),
        "method_definition": ("method", None),
        "interface_declaration": ("class", None),
        "variable_declarator": ("function", None),
        # Same class-property arrow-function form as typescript.
        "public_field_definition": ("function", None),
    },
    "javascript": {
        "function_declaration": ("function", None),
        "class_declaration": ("class", None),
        "method_definition": ("method", None),
        "variable_declarator": ("function", None),
        # JS calls the class-property node `field_definition` (no `public_` prefix);
        # same arrow/function-expression value guard as ts/tsx above.
        "field_definition": ("function", None),
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
        # `public record Point(int x, int y) {}` (Java 16+, ubiquitous in 17
        # LTS / Spring Boot 3). The record's name identifier, component list,
        # and body are all exposed via the standard fields the existing
        # _walk/_find_name path already reads, so mapping it as a class is the
        # whole fix — without it a record yields zero symbols and truthful
        # add/rename claims false-positive.
        "record_declaration": ("class", None),
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
        # TS/JS `variable_declarator` (and its class-property forms
        # `public_field_definition` / `field_definition`) is only a symbol when its
        # value is an arrow function or function expression — `const x = 5` /
        # `count = 0` are data, not callables. Resolve the function-bearing node so
        # name/signature/body extraction targets the right place.
        value_node = None
        if node.type in ("variable_declarator", "public_field_definition", "field_definition"):
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
            # C++ out-of-line method definitions (``void Foo::bar() {}``) sit at
            # module/namespace level so the walk-scope is empty, yet the
            # qualified declarator names their enclosing class. Propagate the
            # qualifier as the symbol's scope and promote to method so the
            # symbol keys/scores identically to the same method defined inline
            # inside ``struct Foo`` — otherwise an out-of-line def and its inline
            # twin would collide on (scope, name) but differ on kind, surfacing
            # a spurious delete+add when an edit merely moves a method in/out.
            sym_scope = scope
            if language == "cpp" and node.type == "function_definition":
                qual = _cpp_qualifier_scope(node, source)
                if qual:
                    sym_scope = qual
                    if effective_kind == "function":
                        effective_kind = "method"
            # Go methods (``func (s *Server) Handle() {}``) sit at file level so
            # the walk-scope is empty, yet the receiver type (``Server``) is the
            # method's real owning scope. The MCP docstring tells agents ``scope``
            # is the "containing class/module name", so an agent emits scoped
            # claims for Go methods — and without the receiver as scope every
            # scoped claim false-fails a truthful edit. Read it from the receiver
            # parameter_list and key the method identically to a C++ out-of-line
            # method, so a free ``func Handle`` and a method ``Handle`` no longer
            # collide at scope=''.
            elif language == "go" and node.type == "method_declaration":
                recv = _go_receiver_scope(node, source)
                if recv:
                    sym_scope = recv
            sig = _signature_text(value_node or node, source)
            body_h = _body_hash(value_node or node, source)
            out.append(
                Symbol(
                    name=name,
                    kind=effective_kind,
                    signature=sig,
                    scope=sym_scope,
                    body_hash=body_h,
                    line=node.start_point[0] + 1,
                )
            )
            if effective_kind in SCOPE_NODE_KINDS:
                next_scope = f"{scope}.{name}" if scope else name
    elif language == "rust" and node.type == "impl_item":
        # Rust `impl` blocks (`impl Foo { fn bar() {} }`, `impl Trait for Baz {}`)
        # own their methods but aren't Symbols themselves (impl_item isn't in
        # LANGUAGE_RULES), so without this branch the walk-scope passed to child
        # `function_item`s stays '' and a method `fn bar` is emitted with
        # kind=function scope=''. The MCP docstring tells agents `scope` is the
        # containing type, so an agent emits `add bar scope=Foo` — and that truthful
        # scoped claim false-fails. Read the implementing type from the impl_item
        # `type` field and propagate it as next_scope so child fn's key by scope=Foo
        # and are promoted to method by the existing effective_kind rule, the same
        # way a C++ out-of-line method or Go receiver method does.
        impl_type = _rust_impl_type_name(node, source)
        if impl_type:
            next_scope = f"{scope}.{impl_type}" if scope else impl_type

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
    """Walk the C++ declarator chain to the leaf name node.

    For an out-of-line method definition (``void Foo::bar(int x) {}``) the leaf
    declarator is a ``qualified_identifier`` whose text is ``Foo::bar``. The
    verifier matches claims by the bare name (``s.name == action.symbol``), so
    returning the whole qualified text makes a truthful ``rename bar→baz`` /
    ``delete bar`` never match and over-flags the dominant C++ header/impl
    layout. Return only the final segment after the last ``::``; the qualifier
    is recovered separately by :func:`_cpp_qualifier_scope` so scoped claims
    can match out-of-line definitions the same way they match inline ones.
    """
    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        if declarator.type in {"identifier", "field_identifier"}:
            return _node_text(declarator, source)
        if declarator.type == "qualified_identifier":
            # Foo::bar → bar ; A::B::bar → bar ; ::bar → bar
            return _node_text(declarator, source).rsplit("::", 1)[-1].strip()
        if declarator.type == "destructor_name":
            return _node_text(declarator, source)
        declarator = declarator.child_by_field_name("declarator")
    return None


def _cpp_qualifier_scope(node, source: bytes) -> str | None:
    """Return the qualifier (``Foo``) of an out-of-line C++ method, or ``None``.

    Walks the same declarator chain as :func:`_cpp_declarator_name`; if the leaf
    is a ``qualified_identifier`` the part before the last ``::`` is the
    enclosing class/namespace. ``None`` for a plain (unqualified) free function
    so the caller leaves the walk-scope untouched.
    """
    declarator = node.child_by_field_name("declarator")
    while declarator is not None:
        if declarator.type == "qualified_identifier":
            text = _node_text(declarator, source)
            if "::" not in text:
                return None
            qual = text.rsplit("::", 1)[0].strip()
            # ``::bar`` (global) has an empty qualifier — no meaningful scope.
            return qual or None
        if declarator.type in {"identifier", "field_identifier", "destructor_name"}:
            return None
        declarator = declarator.child_by_field_name("declarator")
    return None


def _go_receiver_scope(node, source: bytes) -> str | None:
    """Return the receiver type name of a Go ``method_declaration``, or ``None``.

    For ``func (s *Server) Handle(a int) {}`` the receiver lives in the FIRST
    ``parameter_list`` child (before the method name): a single
    ``parameter_declaration`` whose ``type`` field is a ``type_identifier``
    (value receiver ``s Server``) or a ``pointer_type`` wrapping one (pointer
    receiver ``s *Server``). Generic receivers like ``(s *Stack[T])`` use a
    ``generic_type`` whose own ``type`` field is the base ``type_identifier``.
    Returns the bare type name (``Server`` / ``Stack``) so the method keys by
    scope the same way a C++ out-of-line method does. ``None`` if no receiver is
    found (defensive — a ``method_declaration`` always has one in valid Go).
    """
    # The receiver parameter_list is the first parameter_list child; the second
    # (if any) is the method's own parameters.
    recv_list = None
    for child in node.children:
        if child.type == "parameter_list":
            recv_list = child
            break
    if recv_list is None:
        return None
    for decl in recv_list.children:
        if decl.type != "parameter_declaration":
            continue
        type_node = decl.child_by_field_name("type")
        if type_node is None:
            continue
        name = _go_unwrap_type_name(type_node, source)
        if name:
            return name
    return None


def _go_unwrap_type_name(type_node, source: bytes) -> str | None:
    """Peel ``*T`` / ``T[U]`` wrappers down to the base ``type_identifier`` text."""
    node = type_node
    # Unwrap pointer_type (``*Server``) and generic_type (``Stack[T]``) to the
    # underlying named type. Guard the loop so a malformed tree can't spin.
    for _ in range(8):
        if node is None:
            return None
        if node.type == "type_identifier":
            return _node_text(node, source)
        if node.type == "pointer_type":
            inner = node.child_by_field_name("type")
            node = inner if inner is not None else _first_type_child(node)
            continue
        if node.type == "generic_type":
            inner = node.child_by_field_name("type")
            node = inner if inner is not None else _first_type_child(node)
            continue
        # Fallback: a directly-nested type_identifier child.
        node = _first_type_child(node)
    return None


def _first_type_child(node):
    for child in node.children:
        if child.type in {"type_identifier", "pointer_type", "generic_type"}:
            return child
    return None


def _rust_impl_type_name(node, source: bytes) -> str | None:
    """Return the implementing type name of a Rust ``impl_item``, or ``None``.

    For ``impl Foo { fn bar() {} }`` the ``type`` field is a ``type_identifier``
    (``Foo``). For ``impl Trait for Baz { fn qux() {} }`` the ``type`` field is the
    implementing type ``Baz`` (the trait is on the separate ``trait`` field). For
    ``impl Foo<T> { fn bar() {} }`` the ``type`` field is a ``generic_type`` whose own
    ``type`` child is the base ``type_identifier`` — unwrap it so the scope is the bare
    type name, mirroring :func:`_go_unwrap_type_name`. ``None`` if the type can't be
    resolved (defensive — a malformed tree).
    """
    type_node = node.child_by_field_name("type")
    if type_node is None:
        return None
    if type_node.type == "generic_type":
        base = type_node.child_by_field_name("type")
        if base is not None:
            type_node = base
    if type_node.type == "type_identifier":
        return _node_text(type_node, source)
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
