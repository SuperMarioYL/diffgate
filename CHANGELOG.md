# Changelog

All notable changes to DiffGate are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0]

Two correctness fixes that close the last known silent-lie / over-flag holes in
the multi-language verifier core: overloaded same-name methods no longer
fabricate a diff on a pure reorder (a silent-lie false-negative), and Go methods
now carry their receiver type as scope so truthful scoped claims aren't
over-flagged.

### Fixed
- **Overloaded methods no longer fabricate a signature/body change on reorder.**
  Symbols were keyed by `(scope, name, kind)` only — signature was not in the key —
  so overloaded same-name methods (C++ `int f(int)` + `double f(double)`, Java
  `int f(int)` + `String f(String)`) folded into one list and were paired
  *positionally*. Reordering the overloads (or deleting one) re-aligned the lists and
  mis-paired `f(int)` against `f(double)`, fabricating `signature_changed` /
  `body_changed` entries — which defeated the no-op safety net and let a **lying**
  `signature_change f` claim pass (`passed=True`) in both C++ and Java. Overloads are
  now paired by **content** (signature, then body hash) before any positional
  fallback, so a pure reorder is a true no-op, a real signature edit still surfaces,
  and the single-overload / non-overloaded paths are unchanged.
- **Go methods carry their receiver type as scope.** `func (s *Server) Handle() {}`
  parsed to a method with `scope=''` because `_walk` never read the receiver type, so
  a truthful scoped `add Handle scope=Server` / `signature_change Handle scope=Server`
  returned `passed=False` (the MCP docstring tells agents `scope` is the containing
  type, so agents emit such claims). The receiver type is now read from the method's
  first parameter list (unwrapping pointer and generic receivers), completing the same
  over-flag fix already shipped for TS arrows, C++ out-of-line methods, and Java
  records — and a free `func Handle` and a method `Handle` no longer collide at the
  module scope.

## [0.4.0]

Four correctness fixes to the v0.3.0 language coverage (truthful edits were being
false-positived in C++ and Java), the docs brought in sync with the shipped
9-language set, and the catch-rate badge wired from a committed bench trace.

### Fixed
- **C++ out-of-line methods no longer false-positive truthful edits.** Out-of-line
  definitions like `void Foo::bar(int x) {}` were parsed with the qualified name
  `Foo::bar`, so a truthful `rename bar→baz` / `delete bar` never matched and the
  verdict failed — the dominant C++ header/impl layout. `_cpp_declarator_name` now
  returns the unqualified final segment (`bar`) and carries `Foo` as scope.
- **Java records are now visible.** `LANGUAGE_RULES["java"]` omitted
  `record_declaration`, so `public record Point(...) {}` (Java 16+, ubiquitous in
  Java 17 LTS / Spring Boot 3) yielded zero symbols and a truthful `add Point` /
  `rename Point→Pt` returned `passed=False`. Records are now mapped like classes.
- **`.h` / `.hxx` headers auto-detect as C++.** `EXT_TO_LANG` mapped
  `.cpp/.cc/.cxx/.hpp/.hh` but not the most common header extension `.h`, so the
  default `--lang auto` errored out on `widget.h` and exited before the verifier ran.
  Both `.h` and `.hxx` now resolve to `cpp`.
- **Docs match the shipped languages.** The `verify_edit` MCP tool docstring and the
  README architecture / feature / languages sections still advertised only
  Python / TypeScript / Go / Rust; they now list the full
  `python | typescript | tsx | javascript | go | rust | java | cpp | ruby` set, so
  an agent reading the tool contract isn't told java/cpp/ruby are unsupported when
  they work at runtime.

### Added
- **Catch-rate badge wired from a committed trace.** The README badge (en + zh-CN)
  now reads from `bench/catch_rate.json`, regenerated from
  `diffgate bench bench/silent_lie_trace.jsonl`. CI verifies the badge matches a
  fresh bench run so it can't silently drift.

## [0.3.0]

CLI/MCP parity, multi-file edits, three more languages, and three silent-lie
fixes that tightened the exact catch-rate the README badge quotes.

### Added
- **Structured `--claim-file` mode.** `diffgate verify --claim-file claims.json`
  (or `--claim-file -` for stdin) feeds structured `claimed_actions` straight
  into the verifier, giving the CLI the same payload shape the MCP
  `verify_edit` tool already accepted — full CLI/MCP parity for agents that
  emit JSON claims. See `examples/claim_file_schema.json` for the payload shape.
- **Multi-file verify.** A claim file may carry its own per-action before/after
  paths, so a single `diffgate verify --claim-file` run can gate an edit that
  spans several files and aggregate every mismatch into one verdict.
- **Three new languages.** `LANGUAGE_RULES` now covers **Java, C++, and Ruby**
  in addition to Python, TS/TSX/JS, Go, and Rust — all via grammars already
  shipped in `tree-sitter-language-pack`, no new dependency.

### Fixed
- **Bench no longer drops error rows from the denominator.** `run_bench` used to
  `continue` past any row that raised before bumping `result.total`, so a
  `was_lie=true` row that crashed silently vanished from the headline catch-rate.
  Error rows are now counted and attributed to their label instead of dropped.
- **`move` of a duplicate-named symbol is no longer a false pass.** Deleting one
  of two same-named symbols (module-level `foo` vs `A.foo`) made the scope-sets
  differ, which previously read as a passing "move". A move now requires the
  symbol to genuinely leave one scope **and** land in a new one.
- **TS/JS assigned functions are visible.** `export const handler = (req) => {…}`
  and `const foo = function(){}` are now emitted as symbols (arrow / function
  expression assignments), so truthful edits to the dominant TS/JS idiom are no
  longer over-flagged.

## [0.2.0]

First real feature iteration: claims can now pin a containing **scope**, and
the CLI surface that the README has always advertised is finally wired up.

### Added
- **Scope-aware claim matching.** Every claim kind (`add`, `delete`, `rename`,
  `signature_change`, `move`) now honours the `scope` field. Claiming
  `add MyClass.helper` only passes when the method really lands inside
  `MyClass` — adding a free-standing `helper()` no longer satisfies it. An
  empty `scope` stays a wildcard, so existing name-only claims behave exactly
  as before. New mismatch reasons call out scope mismatches explicitly
  (e.g. `'helper' was added, but not in scope 'MyClass'`).
- `diffgate mcp-server --stdio` and `diffgate bench <traces.jsonl>` are now
  real CLI subcommands. They were documented in the README and the `mcp.json`
  snippet but were previously only reachable via `python -m diffgate.…`;
  copying the documented config now works as written.
- Eight new fixtures plus focused unit tests covering scope lies, scope
  truths, and the unscoped wildcard path.

### Fixed
- **Silent-lie false negatives from scope confusion.** Before this release the
  verifier matched claimed symbols by *name only*, so an agent that claimed it
  edited a method on a class but actually touched a same-named module-level
  function (or vice versa) slipped through as a pass. The structural gate now
  distinguishes the two.

## [0.1.0]

First public release covering the m1 milestone:

- `diffgate verify --before X --after Y --claim ...` CLI that exits 0 on a
  structural match and 1 on mismatch, with a `rich`-rendered diff table.
- Tree-sitter parsers for Python and TypeScript.
- Claim kinds: `rename`, `add`, `delete`.
- ≥20 hand-crafted silent-lie fixtures in `tests/fixtures/silent_lie_cases.json`.

[Unreleased]: https://github.com/supermario-leo/diffgate/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/supermario-leo/diffgate/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/supermario-leo/diffgate/releases/tag/v0.1.0
