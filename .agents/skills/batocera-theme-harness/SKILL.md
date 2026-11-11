---
name: batocera-theme-harness
description: Validate, render, screenshot and visually diff the themes/n64-classic-mini Batocera EmulationStation skin. Use when developing, previewing, debugging, validating, capturing screenshots of, or running visual comparisons on the theme.
---

# Batocera theme harness (`batocera-theme-harness`)

All tooling is the typed Python package in `frontend/` (rules: `frontend/AGENTS.md`).
Run every command from `frontend/`. Paths default to the repo layout, so no flags are
needed for the common case.

## 0. Gate: always run before saying a theme change is done

```bash
./run check    # lint, types, tests (ratcheted coverage), theme validation, secrets
```

## 1. Validate the theme XML (instant, the most important check)

```bash
uv run n64mini-validate-theme ../themes/n64-classic-mini --warnings-as-errors
```

Checks the theme against the grammar extracted from Batocera's own `ThemeData.cpp`
(see `frontend/README.md` for the rule codes): unknown elements and properties,
bad values, missing files and includes, the `#RRGGBB` alpha bug, and so on.

## 2. Regenerate the procedural art

```bash
uv run n64mini-generate-assets
```

Writes `art/n64_classic_bg.png` plus placeholder tiles and box art. **Theme XML is
hand-written source.** The generator never writes it. The tile and box-art sprites are
still crops of the golden reference, so carousel-zone scores near 100% are expected
and mean nothing until real art replaces them.

## 3. Tier 1: headless render and visual diff (~1 s)

```bash
uv run n64mini-compare-theme --theme-dir ../themes/n64-classic-mini [--min-similarity 95]
```

Output goes to `test_harness/screenshots/`: `xml_render_clean.png`,
`xml_render_wireframe.png` (cyan = image, magenta = grid/tiles, yellow = text),
`visual_diff_report.png` and `metrics.json`. The scores cover the whole screen plus 4 zones
(header, title banner, carousel, bottom bar).

The renderer is an **approximation**. It reads element properties from the XML, but the
carousel tile positions are fixed slots taken from the reference. It doesn't run
Batocera's grid layout. Treat Tier 2 as the truth.

Golden reference: `frontend/assets/golden_reference.png` (tracked copy of the locked design mock-up).
Render regressions are pinned in `frontend/tests/golden/render_hashes.json` (see `frontend/AGENTS.md`).

## 4. Tier 2: live EmulationStation (WSL2 + Windows host)

**Fastest check, and the one to use:** `./run engine [seconds]`. It runs the real engine on the
theme, stops it cleanly, and prints only the theme findings from its log, each mapped to the
validator code that should have reported it (`tools/es_log.py`). Never read `es_log.txt`
directly: it's thousands of lines of the engine's own font noise.

The manual steps below are for screenshots.

```bash
bash ../.agents/skills/batocera-theme-harness/scripts/setup_live_harness.sh   # one-time
uv run n64mini-capture-live --reload --navigate a,right --out ../test_harness/screenshots/live.png
uv run n64mini-compare-theme --actual ../test_harness/screenshots/live.png
```

The setup script downloads the standalone Windows build of `batocera-emulationstation`
(RetroBat), copies the theme, and writes `es_systems.cfg`, `es_settings.cfg`, dummy ROMs and
a `gamelist.xml` into `test_harness/live/`.

ES HTTP API (`127.0.0.1:1234`): `GET /reloadgames` (same as F5), `POST /input` with body
`a|b|x|y|up|down|left|right|start|select`, and `GET /quit`. Capture uses PowerShell
`GetClientRect` + `CopyFromScreen` on the ES window.

In-window debug keys: `F5` reload, `Ctrl+G` grid, `Ctrl+I` image boxes, `Ctrl+T` text
boxes, `Ctrl+M` mouse coordinates.
