# Python standards: `frontend/`

These rules apply to every `.py` file under `frontend/`. They cover both the
dev-machine tooling and any code that ships to the Batocera device. Humans and
coding agents (Claude Code, Antigravity) follow the same rules.

## The gate (must pass before work is "done")

```bash
./run check         # what the pre-commit hook and CI run
./run help          # all targets
```

| Stage | Tools |
|---|---|
| lint | `ruff` (`select = ["ALL"]`, every ignore justified in `pyproject.toml`), suppression ratchet, `ruff format`, `import-linter` layer contracts, `deptry`, `vulture`, and `typos` + `shellcheck` over git-derived repo-wide file sets |
| types | `mypy --strict`, `pyright` strict |
| test | `pytest` + `hypothesis`, random order (`pytest-randomly`), 60 s timeout, branch coverage |
| validate | theme XML against the extracted Batocera grammar, warnings as errors |
| secrets | `detect-secrets` over everything a commit could include |
| slow, CI weekly | `./run mutate` (mutmut), `./run audit` (pip-audit) |

No exceptions. If a check is wrong, fix the config in `pyproject.toml` *with a
comment saying why*. Don't sprinkle ignores.

### Ratchets (`.quality_baseline.json`)

Coverage of the console code and the validator (`ratchet.RATCHETED`: `n64mini.console`,
`n64mini.theme_schema`) and the mutation score may **hold or improve, never drop**. Everything
else only has to clear the 80% floor in `pyproject.toml` (owner, 2026-11-06: a 90%+ bar
everywhere costs more than it catches; don't write tests just to move the number). The suppression count
(`# noqa`, `# type: ignore`, `# pyright: ignore`, `# fmt: skip`, `# pragma: no cover`) may
**only fall**, and every `noqa` must give a reason: `# noqa: S311 - visual noise, not crypto`.
Mutation is scored like Stryker (timeouts count as caught) with a 0.1-point tolerance for
re-run noise. Coverage and counts have no tolerance. `./run test` and
`./run mutate` tighten the baseline automatically when a run scores higher, and the
pre-commit hook stages the change. To lower one on purpose (deleting well-tested
code, for example), edit the JSON in the same commit and say why in the message.
CI runs with `--check-only` and never rewrites it.

### Mutation testing

`./run mutate` (~10 min locally) mutates `src/` and fails if the kill rate drops.
A **surviving mutant is a missing assertion**: inspect it with
`uv run mutmut show <name>` and add a test that fails on it. Pixel-drawing modules
are excluded (see `[tool.mutmut]`). The golden tests cover them byte for byte.

### Golden images

`tests/golden/render_hashes.json` pins the Tier-1 render and the diff report.
After an intentional visual change, look at the new PNGs, then run
`UPDATE_GOLDEN=1 uv run pytest tests/test_render_golden.py`.
The generated theme art is pinned the same way by `test_generator_reproduces_committed_art`.
Regenerate it with `uv run n64mini-generate-assets`.

## Typing

- **Python 3.12 only.** Batocera's on-device interpreter is 3.12: releases 42 and 43.1 ship
  3.12.8 (`package/python3/python3.mk` in their buildroot, checked 2026-11-06; the development
  branch is already on 3.14). So don't use
  3.13+ features (no `warnings.deprecated`, no PEP 696 defaults on `TypeVar`).
  PEP 695 `type X = ...` and `class C[T]` are fine.
- Both `mypy --strict` and `pyright` strict must be clean. They catch different
  things, so we run both.
- Every function has full annotations, tests included (`-> None`).
- **No `Any`** in our own signatures. Model external data at the boundary:
  - JSON / config shapes → `TypedDict`
  - domain values → `@dataclass(frozen=True, slots=True)`
  - closed sets of strings → `StrEnum` or `Literal[...]`
  - IDs / units that mustn't mix → `NewType`
- `cast()` is allowed only to work around a **third-party stub bug**, with a
  comment naming the bug (see `validate.py`: lxml-stubs `sourceline`).
- `# type: ignore` must carry an error code (`# type: ignore[attr-defined]`),
  and mypy enforces that.
- Collections in signatures: accept abstract (`Mapping`, `Sequence`,
  `Iterable`), return concrete (`dict`, `list`, `tuple`).
- `from __future__ import annotations` at the top of every module.
- Public functions and classes get a Google-style docstring (ruff `D`, tests exempt).
- Functions stay under McCabe complexity 12. Split dispatch into helpers.

## Style & structure

- `ruff format` owns formatting (100 cols). Don't hand-format against it.
- `pathlib.Path` everywhere, never `os.path`.
- Pure functions plus small frozen dataclasses. Don't keep module-level mutable state.
- CLIs: `def main(argv: list[str] | None = None) -> int`, wired in
  `[project.scripts]`, and `raise SystemExit(main())`. Exit codes: 0 ok,
  1 check failed, 2 bad usage or input.
- Errors: raise specific exception subclasses. Never use bare `except:`, and
  never swallow `Exception`.
- Upstream facts (Batocera grammar, paths, versions) are **extracted from
  vendored source** (`vendor/`), never hand-copied. Pin the commit in
  `vendor/*/SOURCE.txt`.

## Tests

- `pytest`, files in `tests/`, named `test_<behaviour>()`.
- Ground assertions as high as possible on the ladder in the root [`AGENTS.md`](../AGENTS.md):
  real system, then invariant (`tests/test_properties.py` has metamorphic tests), then
  snapshot, and only last a hand-picked value.
- A new gate or file set gets a red-confirmation test in `tests/test_gates.py`: it must fail
  on a planted defect *and* pass a clean file.
- Tests that verify a validator rule cite it: list them on the rule's `Tests:` line in
  `docs/requirements/validator.md` (gated by `tests/test_requirements.py`).
- `tests/blind/` belongs to the blind approver. Don't edit those tests, and don't read them while
  writing validator code. A strict `xfail` there is an open disagreement for the human to settle.
- The Tier-1 renderer raises `RenderError` rather than guessing (missing file, garbage value).
  Only an absent property falls back, to the engine's own default.
- Every validator rule or bug fix gets a test that fails without it. Assert the
  exact code **and** message. Asserting "some issue exists" lets mutants survive.
- Parsers and validators get Hypothesis property tests (`tests/test_properties.py`).
- **Every branch also gets a fixed-input test.** A branch only a property test reaches is
  covered on some runs and not others, so the no-tolerance coverage ratchet fails at random.
  Hypothesis adds breadth on top; it isn't what covers a branch.
- Use `tmp_path` fixtures for files. Tests never write into the repo.
- Gate tests (`tests/test_n64_theme.py`) assert the real theme is valid. When
  you accept a known warning, add it to `ACCEPTED_WARNINGS` with a TODO.
- Code that runs on the device may only import what the Batocera image ships (not yet
  verified for `PIL`/`lxml`, so assume stdlib only). When that package exists, add an
  import-linter `forbidden` contract for it.

## Theme XML

- The generated theme under `../themes/` must pass
  `uv run n64mini-validate-theme ../themes/n64-classic-mini`.
- Colours: write `RRGGBB` or `RRGGBBAA`, **never `#RRGGBB`**. Batocera's
  `HtmlColor::parse` drops the alpha channel for that form (rule W001).
- Bundle fonts and images inside the theme. Absolute device paths (W002) aren't
  portable.

## Reference material (`docs/reference/`)

- `typing-best-practices.rst`: python/typing official best practices
- `mypy-existing-code.rst`: how to bring untyped scripts under `--strict`
- `batocera-write-themes.txt`: Batocera wiki theming page (raw export)
- `../vendor/batocera-emulationstation/ThemeData.cpp`: the real grammar
