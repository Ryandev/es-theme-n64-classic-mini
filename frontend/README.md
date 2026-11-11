# `frontend/`: N64 Classic Mini frontend tooling (Python)

Typed Python tooling for the Batocera skin (`../themes/n64-classic-mini/`) and,
later, the on-device hooks. The CAD case is a separate project.

```bash
uv sync                          # create .venv with runtime + dev dependencies
./run check                      # the gate (see ./run help for every target)
uv run n64mini-validate-theme ../themes/n64-classic-mini [--system n64] [-W]
uv run n64mini-generate-assets   # re-render the theme's procedural PNG art
uv run n64mini-compare-theme --theme-dir ../themes/n64-classic-mini   # Tier-1 render + diff
uv run n64mini-capture-live --reload --navigate a                     # Tier-2 live ES capture
```

| Package | What it does |
|---|---|
| `n64mini.theme_schema` | Batocera grammar extraction and the XML validator |
| `n64mini.theme_assets` | N64-style procedural art (3-point filter, RGBA5551 dither, low-poly bevels) |
| `n64mini.harness` | headless render, pixel/zone metrics, diff report, live ES capture |

Theme XML in `../themes/n64-classic-mini/` is hand-written source, gated by the validator.
Fonts are bundled in its `fonts/` folder (DejaVu, Bitstream Vera licence), and the art
generator and the renderer use the same files.

Coding rules: [AGENTS.md](AGENTS.md).

## Theme XML validation: "does the XML have a schema?"

**No official one.** Batocera publishes no XSD/DTD/JSON-Schema for theme XML.
The grammar the engine enforces is a set of C++ tables in
`es-core/src/ThemeData.cpp` (`sElementMap`, `sSupportedViews`, ...). So:

1. `vendor/batocera-emulationstation/` holds `ThemeData.cpp` and
   `HtmlColor.cpp`, pinned to an upstream commit (`SOURCE.txt`).
2. `n64mini-extract-grammar` parses those tables into
   `src/n64mini/theme_schema/data/batocera_theme_grammar.json`. That's 38
   element types and 495 typed properties.
3. `n64mini-validate-theme` walks `theme.xml` and its `<include>` chain the same
   way the engine does (format-7 in-order parsing, `${variables}`,
   `${system.theme}`, base-class inheritance like `clock` → `text`), and checks:

| Code | Severity | Rule |
|---|---|---|
| E001 | error | XML is not well-formed |
| E002 | error | root is not `<theme>` |
| E003 | error | `<formatVersion>` missing, non-numeric, or below engine minimum |
| E004 | error | unknown top-level tag |
| E010 / E011 | error | `<include>` not found / include cycle |
| E020 | error | view/element missing `name` |
| E021 | error | view name not one the engine supports |
| E030 | error | unknown element type |
| E031 | error | property not valid for that element (engine silently drops it) |
| E040 | error | value doesn't parse as its type (pair, rect, float, bool, colour) |
| E041 | error | referenced image/font/sound file doesn't exist |
| W001 | warning | `#RRGGBB` colour: engine bug leaves alpha 0, so use `RRGGBB` |
| W002 | warning | absolute device path, not checked and not portable |
| W003 | warning | undefined `${variable}` |
| W004 | warning | unknown attribute (ignored by engine) |

Things the engine only logs as a warning and then drops (unknown elements and
properties, missing includes) are **errors** here, because a dropped node is
always a theme bug.

To bump upstream, update the vendored files and `SOURCE.txt`, then run
`uv run n64mini-extract-grammar`. `tests/test_grammar.py` fails if the
committed JSON drifts from the vendored source.
