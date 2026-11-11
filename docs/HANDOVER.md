# Handover: where the project stands (2026-11-07)

Up to 2026-11-07 the work was done by Claude Code. From then on Gemini takes over.
This page says where things stand and how to pick them up. The rules are not repeated here;
they are in [`AGENTS.md`](../AGENTS.md) and [`frontend/AGENTS.md`](../frontend/AGENTS.md).
Read both first: they are binding, and the pre-commit hook enforces most of them.

## Read in this order

1. [`AGENTS.md`](../AGENTS.md): the gate, MFIC, safety rules, conduct, and the lessons table.
   Each row of the lessons table is a mistake that has already cost time.
2. [`frontend/AGENTS.md`](../frontend/AGENTS.md): Python standards, ratchets, golden images.
3. [`docs/requirements/theme.md`](requirements/theme.md): the theme's requirements (`AC-T…`),
   the **engine facts** (numbered, each grounded in vendored engine source), the owner's
   decisions (`DEC-…`), and the open questions (`OPEN-…`).
4. [`docs/requirements/system.md`](requirements/system.md): the console setup (`AC-S…`).
5. [`docs/requirements/validator.md`](requirements/validator.md) and [`docs/MFIC.md`](MFIC.md),
   when you touch the validator.

## What the project is

A DIY "N64 Classic Mini": a Radxa X4 (x86) running **Batocera Linux 43.1**. Batocera's stock
EmulationStation is reskinned with our theme, not replaced. Each game can run in the emulator
or, where one is installed, as a native PC port. A "Showcase" of 30 games can be swapped with
the full library.

| Path | What |
|---|---|
| `themes/n64-classic-mini/` | The theme. `base.xml` is written by hand; the art under `art/` is generated (`uv run n64mini-generate-assets`). |
| `music/` | Private (owner's console only, `Ryandev/n64-wavetable-sync`): the synth with a faithful recreation of a copyrighted menu theme. Never published; its render lives in git-ignored `local/theme.wav`. |
| `menu-music/` | Public (`Ryandev/n64-classic-mini-music`): the same synth with the theme's original menu loop; `./run sync-theme` writes `themes/n64-classic-mini/music/theme.wav`. |
| `cad/` | Standalone parametric CadQuery 50%-scale N64 Classic Mini enclosure, 13-part print plate, diode-laser jig SVG, BOM generator, and geometry gate (`./cad/run all`). |
| `frontend/src/n64mini/theme_schema/` | The theme validator, checked against a grammar taken from the vendored engine source. |
| `frontend/src/n64mini/theme_assets/` | The art and music generator (Pillow). |
| `frontend/src/n64mini/harness/` | The dev-machine harness: a Tier-1 preview renderer that ports the engine's carousel and item template (`render.py`, `carousel.py`, `template.py`, `bindings.py`), the live engine sandbox and screenshot comparison. |
| `frontend/src/n64mini/console/` | The code that **runs on the console**. It may use only the standard library, plus Pillow in `strip.py`, `launch.py`, and `suspend.py` (import-linter enforces this). It covers setup, the Showcase/Full switch (`showcase.py`, `library.py`), the flip (`flip.py`), the launch wrapper (`launch.py`), the Reset button (`reset.py`), the 4-slot `NUS-006` Suspend Point List (`suspend.py`), and the strip (`strip.py`). |
| `firmware/rp2040_reset/` | CircuitPython USB HID firmware (`code.py`) for the Radxa X4's onboard RP2040 microcontroller (`GP3` Reset switch -> `KEY_F13` -> `n64mini-reset`, AC-T18). |
| `bundle/` | What goes onto the USB stick: the manifest `n64mini.json`, `install.sh`/`update.sh`, the boot service, the engine config and hotkeys. |
| `frontend/vendor/` | Vendored Batocera/EmulationStation source. Upstream facts are taken from here, never typed by hand (pinned in `SOURCE.txt`). |
| `frontend/run` | Every task: `check`, `vm …`, `bundle`, `stick`, `engine`, `shot`. Run `frontend/run help`. |

## How to work on it

- **The gate:** `frontend/run check > /tmp/check.log 2>&1; echo "exit=$?"`. Never pipe it into
  `tail` or `grep`: the pipe hides the exit code. Work is done only when the gate passes on the
  final state.
- **The real engine is the referee.** The preview renderer and the tests are not enough. Check a
  visible change on the Batocera 43.1 VM:
  - `frontend/run vm start`, with `VM_DISPLAY=window` to get a desktop window under WSLg.
  - `frontend/run vm ssh …` and `frontend/run vm shot out.png`.
  - Install as the console would: build a bundle with `frontend/run bundle DIR`, copy it in with
    `vm copy`, then run `update.sh`.
  - Several engine behaviours were found only this way; the lessons table lists them.
- **Golden images:** after a deliberate visual change, look at the PNGs, then run
  `UPDATE_GOLDEN=1 uv run pytest tests/test_render_golden.py`. Do the same with
  `n64mini-generate-assets` for the art.
- **Ratchets** (`frontend/.quality_baseline.json`):
  - Console and validator coverage, and the mutation score, may only hold or rise.
  - The suppression count (`noqa` and the like) may only fall. It stands at 7.
  - Don't add a `noqa` to get past a lint; restructure the code instead.
- **Review:** `/review` is a Claude Code command. To run it in another tool, follow
  [`.claude/commands/review.md`](../.claude/commands/review.md) by hand, with the agent briefs in
  `.claude/agents/`; the blind approver brief is the important one.
- **Commits** use `type(scope): description`, with a body that says why. Commit only when the
  owner asks.

## Owner rules that are easy to miss

- **Never ask for or accept the owner's password.** The owner runs `sudo` themselves (suggest
  the command).
- **ROMs (`N64/`) must never be committed.** A test enforces this.
- **`.env.agent`** holds the owner's ScreenScraper login. It is git-ignored: never print, commit
  or publish its values.
- **No Nintendo marks in the repo** (DEC-5): the repo will become public. The owner's own marks
  and boot media go in the git-ignored `local/` folder (`local/brand.png`, `local/console.png`,
  `local/launch_splash.png`, `local/splash.mp4`), and `frontend/run bundle` swaps them in (credits
  and sources in [`docs/CREDITS.md`](CREDITS.md)).
- **The owner's design is the plan.** Restate it, build it, and keep your own ideas to a
  separate "also suggest" list.
- **Never delete files, tests or significant code without asking.** Never weaken a check
  without asking.
- **Blind tests** (`frontend/tests/blind/`) belong to the blind approver. The owner rules on
  disagreements (BLIND-n).

## The current UI (built 2026-11-06 to 2026-11-08, AC-T19 to AC-T21)

The owner's design (`highlight_1.png`), modelled on the NES/SNES Classic menu:

- **The cards** sit in a row of 5 with rounded corners (`CORNER = 24`, `radius = 16`), a unified dark
  interior, and a narrow `6 px` border (grey on unselected cards, off-white with a bright top-edge
  LED highlight on the selected centre card). Each holds only its box art, with four suspend-point
  circles at the bottom left (a circle fills while save state slot *n* exists), the `PORT` pill in
  the middle for ported games, and `1P`/`2P`/`4P` at the bottom right. The `'Full library'`
  (`Showcase or Full.n64mini`) card has been dropped from the carousel (`bundle/n64mini.json`).
- **The carousel rotates around the centre card** (`centerSelection="true"`,
  `scrollLoop="true"`, `animateSelection="true"`): the middle card is selected with the steady
  off-white LED-highlighted frame, and moving left or right slides the row continuously.
- **The title banner** (`y = 103..160`) is a dark console pill with an off-white `5 px` border and
  top-edge LED highlight, displaying the current game's title in white/off-white (`F2F5FAFF`).
- **Game launch and gameplay backdrop:** launching a game flips the selected centre card over and
  expands it to full screen with cubic ease-out and cross-fade into `gamesplash.xml` (showing the
  N64 boot logo frame on a black screen instead of the game's screenshot), and `n64.bezel=none` in
  `batocera.conf` drops Batocera's 16:9 console bezel for a plain black backdrop.
- **The strip:** a row of every game's box art under the cards, with a ▼ over the selected
  game.
  - The console draws one PNG per game (`console/strip.py`) into
    `/userdata/system/n64mini/strip/<rom>.png`, and the theme shows the selected game's PNG
    through a bound path. Moving the selection therefore reloads nothing.
  - The PNGs are redrawn after setup, after a Showcase/Full switch and at every boot (the boot
    service).
  - Shown in the **30-Game Showcase** mode; hidden in **Full Library** mode (where `strip.py`
    writes a transparent 1152x68 placeholder so no mini cards or indicator appear).
- **The top and bottom console bars** (`TOP_H = 92`, `BOTTOM_Y = 630`, matching the Classic Mini
  reference proportions) use the same molded N64 charcoal vertical gradient (`(74, 78, 86)` to
  `(46, 49, 55)`).
  - The **top bar** (`art/brand.png` + native `<image name="topbar" extra="true">` bound to
    `{game:multidisk}`) features a recessed centre pocket holding the three flat
    LED-highlighted option icons (Settings cog, Showcase/Full library toggle swapping between the
    Gold Star `art/icon_showcase.png` and Cyan-Blue Fanned 3-Card Hand `art/icon_full.png`, and BG
    music toggle with a diagonal slash when muted) and places the 3D `N` cube logo on the right
    (`frontend/assets/svg/n_logo.svg`, aligned at `x = 1158` with the red Power LED below).
    Pressing `Up` from the carousel highlights the top-bar icons; toggling Showcase/Full via the
    middle button or `KEY_F3` displays a temporary drop-down callout toast (`art/toast_showcase.png`
    `"Showcase"` or `art/toast_full.png` `"Full Library"`) directly beneath the middle button while
    updating the carousel live without reloading EmulationStation. Because the pocket is rendered by
    EmulationStation itself, opening the Settings menu (`Start`) dims the top bar together with the
    rest of the background without X11 overlay flicker. Hotkeys map `KEY_F2` to `n64mini-flip` (flip
    card between `[PORT]` and `[N64]`), `KEY_F3` to `n64mini-library` (also intercepted live on the
    carousel by `n64mini-topbar.sh`), and `KEY_F13` (`KEY_F9` in the VM) to `n64mini-reset`
    (physical Reset button).
  - The **bottom bar** (`art/console.png`) sits below the help prompts and holds the crisp
    `NINTENDO 64` wordmark (white `NINTENDO` + red superscript `64`, without the black bezel,
    extracted from `frontend/assets/svg/front_bezel.svg`) on the bottom-left and the glowing
    crimson/red pill Power LED on the bottom-right (`x = 1158`).
- **The game details** (year, publisher) and the SUSPEND POINT pill have been removed.
- **The 4-slot `NUS-006` Suspend Point List drawer** (`console/suspend.py`, AC-T13 & AC-T18):
  - Pressing `D-Pad Down` on the carousel opens Browse mode, and pressing the physical `Reset`
    button (`KEY_F13` -> `n64mini-reset.sh`) inside an emulator game captures a live
    `batocera-screenshot` + `mupen64plus` save state into `<rom>.pending`, exits back to the
    carousel, and opens Pending Save mode over the dimmed live X11 backdrop.
  - A full-width `1280x348` `NUS-006` cartridge drawer (`art/suspend_drawer.png`, from
    `frontend/assets/svg/suspend_drawer.svg`) with a concentric arch-topped black pocket holds 4
    `NUS-006` cartridge bays (`art/cart_stock.png`, `art/cart_selected.png`, `art/cart_empty.png`
    with `No Data`, `art/cart_target.png`), with hanging `1–4` tabs aligned to each cartridge's
    top-left arc shoulder.
  - In Pending Save mode, the wingless cyan floating `NUS-006` cartridge hovers slowly up and down
    with a cartridge-width yellow `[C-Down] Save` bar (`C-Down` drops it into the slot and plays
    `sounds/suspend_save.wav`; `B` discards it with radial cartoon smoke puffs and plays
    `sounds/suspend_poof.wav`). Launching a slot (`A` or `Start`) passes `-state_slot 8
    -state_filename <path>` via `launch.py` so `mupen64plus` restores `--savestate <path>` without
    clobbering `.st0..3` on a later Reset.

## CAD enclosure workspace (`cad/`)

- The 50%-scale (`130.00 x 99.88 x 33.50 mm`, `SPLIT_Z = 12.94 mm`) N64 Classic Mini enclosure CAD
  workspace lives in `cad/`, structured after the reference GBA CAD project (`lib/step` ->
  `lib/part` -> `lib/variant` -> `lib/subassembly` -> `lib/assembly`, gated by
  `cad/run all && cad/run selftest`).
- **Reference STEP & Variant cleanup (`cad/lib/step/` & `cad/lib/variant/`):**
  - `variant/n64_console.py` strips the external `NUS-002` power brick/cord, AV Multi-Out/RCA cable,
    and SolidWorks construction planes from `step/n64_console_raw.py` (`DEVIATIONS`).
  - `variant/radxa_x4.py` strips all 6 tall through-hole headers (`USB3`, `USB2`, `RJ45`, `GPIO`,
    `PoE`, `Fan`) from the 29-solid `step/radxa_x4.py`, and mounts the Radxa X4 **inverted**
    (`180 deg` roll about `+X` at `Z = 8.20 mm`, flush with the `+Y` rear wall at
    `RADXA_Y_CENTRE = 18.80 mm`) so its rear Micro-HDMI, USB-C PD power, and 3.5mm audio jacks
    protrude through the rear wall and its milled finned aluminium heatsink
    (`variant/radxa_heatsink.py`, `40 x 38 x 10.4 mm`, `Z = 9.80 .. 20.20 mm`) faces **UP (`+Z`)**
    into the top dome.
- **Authentic 50%-scale N64 shells (`cad/lib/part/upper_shell.py` & `lower_tub.py`):** reconstructed
  from the measured B-Rep primitives of `step/n64_shell.py` (`0.5x`: twin protruding `R = 12.50 mm`
  front cylindrical pods with `R = 5.00 mm` convex top domes, bowed front/rear walls, `120.00 mm`
  recessed waist with `13x` side cooling louvers per side, 4 stepped bottom feet + central warranty
  serial bay, `10.275 deg` sloped wing decks, concave stadium `POWER`/`RESET` thumb depressions at
  `(+-32.32, -25.81)`, `33.50 mm` convex front barrel dome with 3D-engraved `MEMORY EXPANSION` cover
  seam and lettering, 25 parabolic saddle louver pockets, and concave rear cartridge cradle with
  3D-engraved `Nintendo` script oval plaque), gated by ray-casting against `step/n64_shell.py`
  (`SH_OUTSIDE` in `console_check.py`).
- **Separately-printed Light Cool Grey Cartridge Slot Flap Insert (`part/cartridge_flaps.py`,
  `J-09`), Momentary Power (`J-03`) & Reset (`J-04`) Buttons (`Omron SS-5GL`):**
  `cartridge_flaps.py` (`57.50 x 10.00 x 1.35 mm`) reproduces `Shell[1]` + `Shell[2]` of
  `Console_N64.step` (`0.5x`) in Light Cool Grey and seats into the recessed cartridge throat pocket
  of the Charcoal Grey `upper_shell`; `power_slider` and `reset_button` (`8.50 x 14.50 mm` stadium
  plungers in `8.94 x 14.94 mm` bores, `0.22 mm/side` clearance) share the exact same smooth
  `R = 19.391 mm` cylindrical-crowned stadium button profile (transverse slider nub removed from
  `POWER`) and actuate `2x Omron SS-5GL` subminiature hinge-lever microswitches (`component/omron_ss5gl.py`,
  `OP = 8.8 +/- 0.8 mm`, `r = 0.72` contact ratio, pre-loaded rest `h_rest = +10.40 mm`, `M3 x 6
  ISO 4026` flat-point set-screw stops in `Ruthex RX-M3x5.7` inserts, `OMRON_ACT`).
- **Twin-port magnetic front controller covers, `Ø10.88 mm` N64 port inserts & Clear PETG LED light
  pipe + Dual-Colour Red/Green LED (`front_port_cover.py`, `n64_port_insert.py`,
  `led_light_pipe.py`, `component/led_l819.py`):** two `28.0 x 11.0 x 1.8 mm`
  bottom-hinged covers (`Left P1+P2` at `X = -25.0 mm` and `Right P3+P4` at `X = +25.0 mm`,
  held closed by `8x Ø3.0 x 1.0 mm` N52 magnets with top fingernail slits) preserve the untouched
  `21.60 mm` central front apron, carry `4x` separately-printed Light Cool Grey N64 controller port
  replicas (`Ø10.88 mm` OD with `R = 0.50 mm` front rim fillet and D-shaped 3-pin cavities, `J-08`)
  concealing `4x TE Connectivity 292303-7` USB-A receptacles, and flank the central Clear PETG
  lenticular Power LED light pipe (`6.36 x 1.76 mm` front lens + 3-lead `Kingbright L-819EGW`
  Red/Green bi-colour LED in `lower_tub` driven by `firmware/rp2040_reset/code.py`: `Red = Power /
  Standby`, `Green = Console ON`, `J-07`).
- **Clear Acrylic front jewel, Diode-Laser Plywood Jig, 13-part print plate & BOM (`front_bezel.py`,
  `bezel_laser_jig.py`, `plate.py`, `bom.py`):** `front_bezel` (`24.14 x 15.50 x 2.00 mm` Clear Cast
  Acrylic backed by a Cricut vinyl decal) seats flush in the central front nose pocket (`J-06`), cut
  and aligned using the `3.0 mm` birch plywood jig (`export/bezel_laser_jig/bezel_laser_jig_0.svg`);
  `cad/run export` writes individual part STEPs/SVGs, the 13-part build plate
  (`cad/export/plate/print_plate.step`), and the full assembly
  (`cad/export/assembly/n64_classic_mini_assembly.step`), and `cad/run bom` generates
  [`notes/BOM_COMPLETE.md` in the CAD repo](https://github.com/Ryandev/n64-classic-mini-cad/blob/main/notes/BOM_COMPLETE.md).

## Open items

**Needs the real hardware (Radxa X4):**

- Time the black screen and red clock shown when a card flips. The engine rebuilds the grid on
  every metadata change. The owner accepted it pending this timing (AC-T15).
- The C-down mapping for the flip, which depends on how the actual controller reports its
  C-buttons (AC-T15).
- The reset button that makes a suspend point (AC-T18).
- The Vulkan ports and audio on the device.

**Dev machine:**

- `dosfstools` and `mtools` are needed for `frontend/run stick --image`. The owner installs them
  with `sudo apt install dosfstools mtools`.

**Known limits, accepted:**

- Unscraped games are blank in the cards and the strip (related to OPEN-6).
- New box art reaches the strip at the next boot.
- The circles show save state slots 0 to 3 only.
- The Windows-side engine harness (`frontend/run engine`/`shot`) has no `/userdata`, so it shows
  no strip. Use the VM for that.
