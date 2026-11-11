# Native N64 ports: what each needs on stock Batocera

> **Quick-start for builders:** For step-by-step instructions on placing your ROMs onto the USB stick, letting `n64mini-setup` install all 7 ports automatically on your console, flipping between `[PORT]` and `[N64]` in the carousel (`F2`), and adding new ports to [`bundle/n64mini.json`](../bundle/n64mini.json), see the [**Setting Up Native PC Ports (`AC-S04`)**](../README.md#setting-up-native-pc-ports-ac-s04) section in [`README.md`](../README.md).

Research for AC-S04 (setting ports up on the console), checked 2026-11-06 to 2026-11-08 against each project's
GitHub releases API, README, the readme inside its release zips, and the ROM-handling source
(N64ModernRuntime `librecomp/src/recomp.cpp`, Ship of Harkinian `Extractor/Extract.cpp`). Release
contents were listed over HTTP range requests. Releases change often; re-check before
pinning one in the manifest.

Batocera is a Buildroot distribution with no package manager (system.md), so a port must be a
self-contained Linux x86_64 build unpacked into `/userdata`.

## Two families

- **HM**: HarbourMasters / libultraship ports (Starship, SpaghettiKart, Ship of Harkinian,
  2 Ship 2 Harkinian, Ghostship). Linux builds are **AppImages** (bundled libraries; need FUSE,
  or `--appimage-extract` once and run `squashfs-root/AppRun`). On first launch they convert
  the ROM into a `.o2r` archive **through GUI dialogs**. Running Torch from the command line
  instead works for SpaghettiKart (proven below); the others are unchecked.
  The Ghostship and SpaghettiKart READMEs describe making the `.o2r` on a PC and copying it
  over (their Switch instructions).
- **NR**: N64Recomp / N64ModernRuntime ports (Zelda64Recomp, Starfox64Recomp, Banjo, DK64,
  Bomberman 64, Snowboard Kids 2). Linux builds are a plain executable in a tar.gz, **not**
  self-contained: linked against system SDL2 and FreeType, probably GTK3 for the file dialog.
  The ROM step is a copy, not a conversion: the runtime byte-swaps to `.z64`, checks the hash and
  stores `<config>/<game_id>.z64`, then only re-checks that file. So placing a correct US `.z64`
  under that name skips the picker. A `portable.txt` beside the executable keeps config and saves
  next to it. Runtimes from 2026-08-30 have `--game <id>`, which skips the launcher menu; only
  DK64 v1.0.3 and Snowboard Kids 2 v2.0.2 ship it so far.

## The ports

| Port | Release (date) | Linux asset | ROM step | Licence |
|---|---|---|---|---|
| Starship (SF64) | HarbourMasters/Starship v2.0.0 (2025-05-25) | zip → `starship.appimage` | `.o2r` from US 1.0/1.1, dialog | CC0-1.0 |
| SpaghettiKart (MK64) | HarbourMasters/SpaghettiKart 1.0.0 (2026-02-25) | zip → `spaghetti.appimage` | `.o2r` from US, dialog; can be made on a PC | none in repo |
| Ship of Harkinian (OoT) | HarbourMasters/Shipwright 9.3.1 (2026-10-18) | bare `.appimage` | `oot.o2r`, yes/no dialogs; can be placed in `SHIP_HOME` | none in repo |
| 2 Ship 2 Harkinian (MM) | 2ship2harkinian 5.0.1 (2026-08-25) | zip → `2ship.appimage` | ROM beside the AppImage; dialog not ruled out | CC0-1.0 |
| Ghostship (SM64) | HarbourMasters/Ghostship 3.0.0 (2026-09-07) | zip → `ghostship.appimage` | `sm64.o2r` from US/JP, dialog; can be made on a PC | MIT |
| Zelda64Recomp (MM) | Zelda64Recomp v1.2.2 (2025-08-27) | zip → tar.gz | stored US ROM; launcher menu (no `--game`) | GPL-3.0 |
| Starfox64Recomp | sonicdcer/Starfox64Recomp 1.0.3 (2025-12-30) | zip → tar.gz | stored US 1.1 ROM; launcher menu | GPL-3.0 |
| Banjo: Recompiled | BanjoRecomp v1.0.2 (2026-07-31) | tar.gz | stored US 1.0 ROM; launcher menu | GPL-3.0 |
| DK64 Rekongpiled | Rainchus/Donkey-Kong-64-Recompiled v1.0.3 (2026-09-30) | zip → tar.gz | stored US ROM; `--game`. **README: Intel GPUs on Linux may not work** (the X4 is Intel) | GPL-3.0 |
| Dr. Mario 64 Recompiled | no upstream releases; fork has Windows only | **no Linux build** | n/a | GPL-3.0 |
| Bomberman 64: Recompiled | RevoSucks/BM64Recomp v1.0.0 (2026-03-22) | zip → tar.gz; also an AppImage (bundled libraries) | stored US ROM; launcher menu | GPL-3.0 |
| Snowboard Kids 2: Recompiled | cdlewis/snowboardkids2-recomp v2.0.2 (2026-10-17) | bare tar.gz | stored US ROM; `--game` | GPL-3.0 |

## Recommended first port: Snowboard Kids 2: Recompiled v2.0.2

**Checked in the Batocera 43.1 VM (2026-11-06, `./run vm`):** the Linux X64 tar.gz (SHA-256
`be89a751…3690e`, matching GitHub's digest) unpacks to `SnowboardKids2Recompiled`, `assets/` and
`recompcontrollerdb.txt`. `ldd` resolves every library from the stock image, GTK3 included, with
none missing. Run with `portable.txt`, it loads and gets as far as opening the audio device (the
headless VM has none). So the NR family's system libraries are on stock Batocera.

How it has to be launched (from its source at v2.0.2 and RecompFrontend `fa4f0ca`): on Linux it
loads `assets/` from the **current directory**, and looks for `portable.txt` there too; otherwise
its app folder is `APP_FOLDER_PATH` (or `~/.config/<id>`). The app folder holds the stored ROM
(`snowboardkids2.n64.us.z64`, checked against `rom_hash` 0x81f434ff431162eb, an XXH3-64) and the
saves. `--game snowboardkids2` (the `mod_game_id`) starts the game directly if that ROM is valid.
So the recipe runs it from its install folder with `APP_FOLDER_PATH` set to a saves folder that
survives updates. The owner's `Snowboard Kids 2 (U) [!].z64` has that XXH3 (checked with the
`xxhash` package on this PC); its SHA-1, `5ce896fd…`, is what the console checks. With a sound
card added to the VM it starts and stops at `Unable to find devices that support Vulkan`: its
renderer needs Vulkan, which the X4's Intel GPU has and the VM doesn't.

Nothing on its path needs a person: unpack the tar.gz, add `portable.txt`, copy the owner's US ROM
(z64 byte order) to `snowboardkids2.n64.us.z64`, launch `SnowboardKids2Recompiled --game
snowboardkids2`. Every other NR port uses the same runtime, so the recipe carries over (the older
ones add one controller press at the launcher). If a later NR build needs a library Batocera
lacks, Bomberman 64's AppImage (bundled libraries) is the fallback pattern.

## Banjo-Kazooie and Bomberman 64 (2026-11-06)

Same recipe as Snowboard Kids 2, from each release's source at its tag: the ROM is stored as
`<game_id>.z64` in the app folder (`bk.n64.us.1.0.z64`, `bm64_us.z64`), and both honour
`APP_FOLDER_PATH` (Banjo through RecompFrontend `d0d90ba`, Bomberman through its own
`zelda64::get_app_folder_path`). The owner's `Banjo-Kazooie (U) (!).v64` and
`Bomberman 64 (U) [!].z64` match each port's `rom_hash` (XXH3-64 0x1B67585D56E07F8C,
0xc4c0b74bbb696426); their SHA-1s pin them in the manifest. Bomberman's Linux zip holds a
tar.gz, which the recipe unpacks with Batocera's own `tar`.

Neither runtime (N64ModernRuntime `ca568b6`, `0bb76b0`) has `--game`, so each opens its own
launcher first and the player presses Start there. In the VM both installed, resolved every
library, and stopped at the renderer (Vulkan), as Snowboard Kids 2 did.

## Majora's Mask, Donkey Kong 64, and Star Fox 64 (2026-11-08)

All three follow the same N64Recomp zip-inside-tar.gz recipe (`port:zelda64` v1.2.2,
`port:dk64` v1.0.3, `port:starfox64` 1.0.3):
- `Zelda64Recomp` v1.2.2 stores the US 1.0 ROM as `mm.n64.us.1.0.z64` (SHA-1 `d6133ace…`,
  XXH3-64 `0xEF18B4A9E2386169`) in `APP_FOLDER_PATH` (`zelda64::get_app_folder_path`).
- `DK64 Rekongpiled` v1.0.3 stores the US ROM as `DK64.z64` (SHA-1 `cf806ff2…`,
  XXH3-64 `0x4d876060f09b3fc5`) in `APP_FOLDER_PATH` (`recompui::file::get_app_folder_path`) and
  launches straight into the game with `--game dk64`.
- `Starfox64Recomp` 1.0.3 stores the US 1.1 ROM as `sf64.n64.us.1.1.z64` (SHA-1 `d8b10885…`,
  XXH3-64 `0x163fd3fc3813f54e`) in `APP_FOLDER_PATH` (`zelda64::get_app_folder_path`).

All three match the owner's US ROM dumps in `N64/ROMS` and are pinned in `bundle/n64mini.json`.

## Building `.o2r` files without dialogs (DEC-S3, option b)

Checked 2026-11-06 in each port's `.gitmodules` and Torch's README:

| Port | Asset pipeline |
|---|---|
| Starship, SpaghettiKart, Ghostship, Ship of Harkinian | [HarbourMasters/Torch](https://github.com/HarbourMasters/Torch) (MIT), a submodule pinned per port |
| 2 Ship 2 Harkinian | OTRExporter + ZAPDTR (the older pipeline; ZAPD also has a command line) |

Torch's README gives a plain command line, `./torch otr baserom.z64`. The Starship and Ghostship
release zips ship the `config.yml` and `assets/` YAML it reads. So the likely route is: build Torch
for Linux at the commit each port pins, ship that binary in the bundle, and run `torch otr` against
the owner's ROM with the port's own config, producing the `.o2r` the port then finds without a
dialog.

**Proven for SpaghettiKart 1.0.0 (2026-11-06, Batocera 43.1 VM):**

- Torch at SpaghettiKart's pinned commit (`2d474dd`) builds on this PC with plain `cmake` and
  `make` (no extra packages), and the binary runs on stock Batocera with no missing libraries.
- The command is the one SpaghettiKart's own build runs: `torch o2r baserom.us.z64
  --additional-files meta/mods.toml`, from a folder holding the port's `config.yml`, `yamls/`,
  `meta/` and `include/defines.h` at the release tag (the release zip has only the AppImage, so
  those come from the source). Note `o2r`, not the README's `otr`. It takes about 7 s and under
  1 GB of memory on the VM, and logs 433 warnings and errors (vertex notes, 17 packed display
  lists it can't process). The port's in-game extraction calls the same Torch code
  (`GameExtractor::GenerateOTR`), so it would log the same.
- With `SHIP_HOME` set to a folder holding that `mk64.o2r`, SpaghettiKart's AppImage loads it
  ("Adding Archive …/mk64.o2r", its `mk64-assets` mod and sound banks) and shows no dialog. It then
  stops at `Failed to initialize OpenGL loader!` because the VM has no OpenGL; drawing a frame
  needs the console (or a VM with GL).
- The `.o2r` made on Batocera and the one made in WSL have identical contents (all 32,447
  entries); only zip timestamps differ.
- The owner's `Mario Kart 64 (U) [!].z64` has the SHA-1 SpaghettiKart's `config.yml` accepts.

**Seen running (2026-11-06, the VM with OpenGL):** launched from its flipped card in the carousel,
SpaghettiKart drew Mario Kart 64's attract mode from the `mk64.o2r` built on the console, with
no missing assets visible.

**Still open:**
- Torch's only release is v1.0.0 (2024), so we build it, per port pin, and ship the binary in the
  bundle. Each HarbourMasters port pins its own Torch commit and needs its own check.
- **Star Fox 64:** uses `Starfox64Recomp` 1.0.3 (`port:starfox64`) with the owner's US 1.1 dump
  (`Star Fox 64 (U) [!].z64`).
