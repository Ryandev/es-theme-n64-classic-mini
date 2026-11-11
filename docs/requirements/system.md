# Requirements: the console's software setup

How the N64 Classic Mini's software gets onto the console and stays up to date. The front end
itself is in [`theme.md`](theme.md). Same conventions: numbered `AC-S…` requirements, each with a
**Status** (`done` or `planned`) and how it's **Verified** (`gate`, `harness` = on this machine,
`console` = on the real hardware).

## The owner's design (2026-11-06)

- **Stock Batocera.** No custom image or fork. Batocera's version is pinned and its automatic
  updates are off; a new release is adopted only after it's been checked here (the engine
  source re-pinned, the gate and the engine checks re-run).
- **Setup scripts that run on boot do the work on the console,** including all the port setup.
  Nothing is prepared on a PC beforehand except getting the scripts there once.
- **Nothing changes on the fly** other than what the owner does at the console (flipping a card,
  Showcase ⇄ Full).

## Facts this rests on

1. **Batocera keeps everything of ours in `/userdata`:** a separate partition that its system
   updates replace nothing in. Theme, settings, the N64 launch override, hotkeys, event scripts,
   ports, ROMs and saves all live there.
2. **Boot-time scripts are Batocera services:** a script in `/userdata/system/services/`, enabled
   in System settings → Services or with `batocera-services`
   ([wiki](https://www.wiki.batocera.org/launch_a_script)). `custom.sh` is deprecated since
   Batocera 43.
3. **The console's Python is 3.12.8** in Batocera 42 and 43.1 (their buildroot's
   `package/python3/python3.mk`), so setup is standard-library Python like the other console
   code.
4. **The engine can show messages** over its HTTP API (`POST /notify`, `/messagebox`), so setup
   can report progress and failures on screen.
5. **Batocera 43.1 ships Pillow 11.0.0, with JPEG support** (checked in the 43.1 VM, 2026-11-06:
   `PIL.__version__` and `PIL.features.check("jpg")`; Batocera's own `configgen/utils/bezels.py`
   imports it). Only the strip of box art under the cards (theme AC-T20) needs an image library,
   so only `n64mini.console.strip` may import it; every other console module stays standard
   library (the import-linter contract in `frontend/pyproject.toml`).

---

### AC-S01: One-time install

Getting the scripts onto a fresh stock Batocera is the only manual step: copy them over its
network share or from a USB stick, and enable the service. After that, everything goes through
the boot setup.

- **Status:** done (harness: a clean Batocera 43.1 VM, 2026-11-06); needs the console
- **Verify:** harness, console

`bundle/install.sh`, run from the stick (`bash /media/<label>/n64mini-bundle/install.sh`),
installs and enables the service (`batocera-services enable n64mini`) and sets the console up from
the stick. In the VM it installed everything in one run.

### AC-S02: Boot setup is idempotent and versioned

On every boot, `n64mini-setup` brings each component (settings, theme, launcher and
`es_systems_n64.cfg`, hotkeys and the `game-selected` hook, each port) to the version in its
manifest. A component already at that version is skipped, so a normal boot does nothing. What it
installed, and at which version, is recorded on the console.

- **Status:** done (gate; harness: with the stick in at boot, the service ran setup and skipped everything)
- **Verify:** harness, console

The service is `/userdata/system/services/n64mini`. `S99userservices` starts user services after
the engine's own start script (`S31emulationstation`), in the background, so setup waits up to
120 s for the engine's HTTP server before showing progress, and carries on without it if it never
answers. Batocera's storage manager mounts a stick at `/media/<label>`, but only a partitioned one
(a bare filesystem on the whole device is skipped), and on FAT every file shows as executable.
So each `files`/`file` component declares which of its files are executable, and setup gives the
installed copy exactly those modes, whatever the stick says.

### AC-S03: A failure never leaves a half-setup

A component is set up beside the working one and swapped in only once complete. If it fails, the
previous version stays in place, the failure is shown on screen and logged, and the next boot
tries again. One failing port doesn't stop the others or the front end.

- **Status:** done (gate)
- **Verify:** harness, console

### AC-S04: Ports are set up on the console from a pinned manifest

The repo's port manifest pins each port: version, download URL, SHA-256 and its setup recipe. On
the console, setup downloads the release, rejects it unless the checksum matches, unpacks it into
a private folder (never `roms/ports`), runs the port's preparation step against the owner's ROM
where it needs one, and only then lists it in `ports.json`. So a game becomes flippable only once
its port is ready (AC-T15).

- **Status:** done for Snowboard Kids 2, SpaghettiKart, Banjo-Kazooie, Bomberman 64, Majora's
  Mask, Donkey Kong 64 and Star Fox 64 (`bundle/n64mini.json`); each further port gets its own
  recipe and check
- **Verify:** gate (`test_recipe.py`, `test_setup.py`), harness (the Batocera VM), console (the
  game itself running: the VM has no GPU)

How a recipe works (`console/recipe.py`):

- **The owner's ROM** is found by checksum, not by name: files matching the catalogue's pattern
  for the game, of which one must have a SHA-1 (in z64 byte order) the port accepts. Duplicate
  or wrong-version dumps are passed over. With no usable dump the port **waits**: it's retried
  on the next boot, with no failure on screen, and the CLI lists why.
- **Preparation commands** run in the build folder (Torch, for the HarbourMasters ports).
- **Saves and config live outside the port's folder**, in `/userdata/saves/n64mini/<port>`, so an
  update (which replaces the whole folder) keeps them. Files made from the ROM are built inside
  the port's folder and linked from there.
- Only once all that has worked is the port listed in `ports.json`, and the badges refreshed.

Checked in the Batocera 43.1 VM (2026-11-06): a full run with real downloads took 18 s and
installed both ports; a second run skipped everything; the wrong-version Mario Kart dump was
passed over; both games got their flip badge. Each port's `ports.json` command starts it,
with config written to its saves folder and the ROM-derived files found, up to its renderer:
Snowboard Kids 2 needs Vulkan and SpaghettiKart OpenGL, which the VM lacks.

### AC-S05: Without a network, setup carries on

A download that fails (no network, a server down) leaves that port out for now: its game simply
isn't flippable, and setup retries on the next boot. Everything that doesn't need the network
still completes.

- **Status:** done (gate: `test_without_a_network_the_port_waits_and_the_rest_completes`)
- **Verify:** harness

### AC-S06: Setup shows progress

A long first boot (ports downloading and preparing) puts progress on screen through the engine,
rather than a silent wait.

- **Status:** done (gate: `--engine` shows each step and "N64 Mini is up to date", then reloads the game lists, or says a restart follows); the messages themselves not yet looked at on screen
- **Verify:** console

### AC-S07: Updates are new versions of the scripts and manifest

Updating ports, the theme or the scripts means a new version reaching the console, on a USB
stick (DEC-S1). The next boot redoes only what changed. A generator that builds the stick comes
later.

- **Status:** done for changes on the stick (harness: one changed file on the stick redid exactly its component). `frontend/run stick DIR` writes them to a mounted stick (under WSL a Windows drive, `/mnt/e`) as `n64mini-bundle/`, swapping the old copy out only once the new one is complete; `--image FILE` writes a partitioned FAT image for the VM (needs `dosfstools` and `mtools`)
- **Verify:** harness, console

Files the bundle carries take `"version": "content"`: a hash of their names and bytes (and the
manifest's executable list), so a change on the stick is picked up without bumping a number.

### AC-S08: The setup is testable without a full run

The owner's concern (2026-11-06): if testing any change needs a full run on the console, the
test cycle is too long. So testing is layered, and the setup is built for it:

1. **Logic, in the gate (seconds).** Setup is standard-library Python run against a fake
   `/userdata` folder, with downloads served from a local test server and a fake engine API.
   Versioning and skipping, safe swaps, checksum rejection, no network, and each port recipe's
   steps are tested on every change.
2. **One component, on a real Batocera (about a minute).** `n64mini-setup --only <component>
   [--force]` runs a single component, and a mirror option lets downloads come from this PC's
   cache instead of the internet. A Batocera VM here (QEMU with KVM, which WSL provides) is
   restored from a snapshot taken after the one-time install, the changed script is copied over
   SSH, and just that component runs. Setup needs no graphics, so the VM can run headless.
3. **The full first-boot run (occasionally):** a fresh VM through the one-time install and a full
   setup. Before a release and after a Batocera update, not on every change.

The console itself is needed only for what a VM can't show: the reset button and its firmware,
the controller's buttons, performance and the real display.

**The VM runs the engine through virgl:** Batocera has no software OpenGL of its own
(`swrast_dri.so`), so the VM's display is `virtio-vga-gl` on WSL's render node
(`/dev/dri/renderD128`; the owner must be in the `kvm` and `render` groups). Without it the engine
crash-looped, unnoticed, through the first VM tests; `run vm start` now fails unless the engine is
running and answering. No Vulkan (Batocera has no virtio Vulkan driver), so Snowboard Kids 2
still stops at its renderer. Screenshots come from Batocera's own `batocera-screenshot`.

Layer 2's VM exists: `frontend/run vm` (create, start, stop, ssh, copy, snapshot, restore, list,
shot), with a `clean` snapshot of stock Batocera 43.1 plus our SSH key. Restoring it and booting
to SSH takes under a minute; the first boot of a fresh image takes about 15. Still to come: the
one-time install snapshot and the mirror served from this PC.

- **Status:** done (gate; the VM: `frontend/run vm`, including `usb` and `VM_STICK=` for a stick)
- **Verify:** gate, harness

### AC-S09: The engine is set up as the console (DEC-6)

Setup merges the engine's settings into `es_settings.cfg`, keeping any others the owner has set:
our theme (`ThemeSet`), straight into the N64 game list (`StartupSystem` `n64`,
`StartupOnGameList`), no built-in collections (`CollectionSystemsAuto` empty), an immediate hand-off
into the launch card-flip/expand/cross-fade animation and `gamesplash.xml` (`GameTransitionStyle`
`instant`), and every other
system hidden (`HiddenSystems`). Setup also sets `n64.bezel=none` in `/userdata/system/batocera.conf`
so games run on a plain black 16:9 screen without Batocera's default console bezel
(`configgen-defaults.yml`), and replaces every Batocera boot reference with the N64 startup video
([`docs/CREDITS.md`](../CREDITS.md), *Nintendo 64 Startup Remake* by Meurotte,
<https://www.youtube.com/watch?v=D9aFp7SzSVc>):
- the `splash` component installs `console/splash.mp4` (`local/splash.mp4` swapped in by
  `frontend/run bundle`) to `/userdata/splash/splash.mp4`, which `/etc/init.d/S28splash` plays via
  `mpv` instead of `/usr/share/batocera/splash/splash.mp4` and `/etc/init.d/S30splashscreencontrol`
  holds to completion (`splash_autoplay`);
- `batocera.conf` sets `splash.screen.enabled=1`, `splash.screen.subtitle=0` (`--no-sub-auto` in
  `S28splash`, hiding Batocera's version subtitle), and `es.customsargs=--no-splash`
  (`emulationstation-standalone` → `main.cpp`, skipping EmulationStation's startup splash);
- [`themes/n64-classic-mini/splash.xml`](../../themes/n64-classic-mini/splash.xml) overrides
  `:/splash.xml` (`Splash.cpp`) with a black screen and hidden progress bar/label; and
- [`bundle/console/boot-custom.sh`](../../bundle/console/boot-custom.sh) (installed to
  `/boot/boot-custom.sh` and run at `S00bootcustom` before `S03system-splash`) removes
  `/usr/share/batocera/splash/boot-logo*.png` and `splash.srt` and strips `Booting Batocera.linux`
  from `/boot/EFI/BOOT/grub.cfg` and `syslinux.cfg`.
Stock Batocera ships demo games for 14 other systems, so
without that they'd show. The hidden list is every system the console's own `es_systems*.cfg`
files name, apart from `n64`, so it follows the Batocera version installed, and its version is a
hash of the settings, so a Batocera update that adds systems gets them hidden on the next boot with
the stick. The engine reads its settings, theme and systems only at start-up, **and writes its whole
in-memory settings back as it quits** (`main.cpp` → `ViewController::saveState` →
`Settings::saveFile`), so a settings file changed under a running engine is lost the moment it
next quits. `bundle/update.sh` therefore runs setup in two phases: everything else first, with the
engine running and progress on screen, then, only if any start-up component (settings, theme, N64
override) changed, Batocera's own `S31emulationstation suspend` (the engine quits and its launch
loop waits), those components, and `resume`. With no engine running it sets everything up at
once. (`GET /restart` would reboot the console.)

- **Status:** done (gate; harness: on a clean Batocera 43.1 VM with the engine running, the
  install from a FAT stick ended with the engine back up in the N64 carousel, our theme, no other
  system and no collections, 2026-11-06). The first attempt wrote the settings and then quit the
  engine, which wrote its old settings back over them; the VM caught it
- **Verify:** gate, harness, console
- **Tests:** `test_es_settings.py`, `test_setup.py::test_settings_merge_into_the_engines_file_and_hide_other_systems`, `test_bundle.py::test_every_setting_is_one_the_engine_has_with_the_type_it_stores`

**Not set:** kiosk mode (`UIMode`), by the owner's ruling (DEC-S4).

**The owner's ScreenScraper login** (`ScreenScraperUser`, `ScreenScraperPass`; `ScreenScraper.cpp`
reads them from the engine's settings) comes from the git-ignored `.env.agent`
(`SCREENSCRAPER_USERNAME`, `SCREENSCRAPER_PASSWORD`): `frontend/run bundle` adds a
`scraper-login` es-settings component to the bundle's copy of the manifest only, so it's on the
owner's stick and console, never in the repo. On the VM (2026-11-06) the engine's own scraper
logged in with it and scraped the N64 list.

## Decisions

### DEC-S1 (was OPEN-S1): New versions arrive on a USB stick

Ruling (owner, 2026-11-06): for now, a USB stick the boot setup looks for. A generator that
builds the stick's contents comes later.

### DEC-S2 (was OPEN-S3): QEMU in WSL for the Batocera VM

Ruling (owner, 2026-11-06): yes. Dev-machine only, never shipped.

### DEC-S3 (was OPEN-S4): HarbourMasters ports: command-line conversion if possible, else a PC

Ruling (owner, 2026-11-06): the HarbourMasters ports (Ship of Harkinian, Starship,
SpaghettiKart, Ghostship, 2 Ship 2 Harkinian) build a `.o2r` from the ROM through GUI dialogs on
first launch. First choice: find (or contribute) a command-line way to build it, so setup stays on
the console. Fallback: build the `.o2r` once on a PC and carry it on the USB stick; it comes from
the owner's ROM, so it's never committed or published.

Outcome (2026-11-06): the first choice works for SpaghettiKart. Torch, built at the port's own
pin, runs on stock Batocera and makes an `.o2r` the port loads without a dialog (details in
[`../ports.md`](../ports.md)). The other HarbourMasters ports get the same check, one by one.

### DEC-S4: No kiosk mode; Batocera's full menus stay

Ruling (owner, 2026-11-06): the engine keeps its full menus behind Start. Kiosk mode (`UIMode`
`Kiosk`, `Settings.cpp`) would hide them behind a button code, so setup doesn't set it.

## Open questions

### OPEN-S2: Which port first?

Research in [`../ports.md`](../ports.md). Recommendation: **Snowboard Kids 2: Recompiled**. Its
whole setup runs on the console without a person (unpack, place the ROM under its expected name,
launch with `--game`), and the same runtime is behind the other N64Recomp ports. Its libraries
(SDL2, FreeType, GTK3) are all on stock Batocera 43.1: checked in the VM on 2026-11-06
(`ports.md`).

