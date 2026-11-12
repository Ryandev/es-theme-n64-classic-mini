# Requirements: the N64 Classic Mini theme (`themes/n64-classic-mini/`)

What the product must look like and do on the console. The theme is Batocera's own
EmulationStation, reskinned, so every requirement here must be something that engine can
actually do. Facts about the engine are cited from `frontend/vendor/batocera-emulationstation/`.

Same conventions as [`validator.md`](validator.md): numbered `AC-T…` requirements that tests
cite, and a blind approver can test against them. Each requirement has:

- **Status:** `done` (implemented and verified) or `planned`. A `done` requirement must cite
  tests that exist. `frontend/tests/test_requirements.py` checks this.
- **Verify:** how it's checked. `gate` means `frontend/run check`, `render` the Tier-1 preview,
  `engine` means `frontend/run engine` (the real EmulationStation), and `look` a human comparing a
  screenshot against the reference.

**Current phase (owner, 2026-11-06): working first, design later.** The owner isn't happy with
the UI yet. The goal for now is a complete, working front end on the real engine, with simple
styling. The visual design is then iterated around the functionality the console actually
needs. So a requirement here is "done" when it works and is verified, not when it looks final.

**Recorded as we go.** Every behaviour checked on the engine goes on its requirement's status
line (what was seen, and when). Every engine behaviour the theme relies on is an engine fact
below, citing the source. Anything seen that raises a choice becomes an `OPEN-n`. Nothing
learned from a screenshot lives only in a chat or a commit message.

**Open questions** (`OPEN-n`, at the end) are design decisions the human hasn't made yet.
Nothing that depends on one is built until it's answered.

## Sources

- **Design reference:** the locked mock-up `frontend/assets/golden_reference.png` (tracked),
  and the owner's research notes on the two toggles (local only: `docs/references/research/04-…`).
- **SNES Classic behaviour:** the screenshots in `docs/references/` (local only, git-ignored):
  `04_snes_classic_suspend_point_saving.jpg` (carousel), `03_…drawer_empty.jpg` and
  `06_…multiple_slots.jpg` (suspend points).
- **Lineup:** `frontend/src/n64mini/console/data/games.json`, taken from the 30-game table in the
  owner's research notes (local only: `docs/references/research/02-…`).

## Engine facts that shape the design

These are verified in the vendored source, not assumed:

1. **Per-game data a theme can show** comes from `DetailedContainer.cpp`: `md_name`,
   `md_players`, `md_releasedate`, `md_publisher`, `md_developer`, `md_genre`, `md_image` /
   `md_thumbnail` / `md_boxart`, `md_description`, and **`md_savestate` / `md_nosavestate`**,
   which show only when the game has (or hasn't) suspend points.
2. **The suspend-point screen is built in** (`GuiSaveState.cpp`). Its layout is hard-coded C++:
   a horizontal grid with 6 visible cells, "Start new game", "Start new auto save", then
   numbered slots with screenshots, and no slot limit. A theme can change only its colours,
   fonts and frame, through the menu theme.
3. **One image grid per gamelist view.** The `grid` view has a single `imagegrid`, so a second
   live "mini-thumbnail ribbon" can't be another grid.
4. **The theme is loaded for every system and collection** (`CollectionSystemManager.cpp`), so it
   must work for all of them (validator AC-V22).
5. **Carousel tiles follow the grid's auto layout** (`ImageGridComponent.h`). With `autoLayout`,
   each tile is the grid's size, minus the grid's padding and margins, divided by the column
   count. The gridtile `size` values are ignored, and the selected tile is that size times
   `autoLayoutSelectedZoom`. The grid clips its tiles, so a zoomed tile needs grid padding
   around it.
6. **A tile's frame is a nine-patch drawn behind its image** (`GridTileComponent.cpp`,
   `NinePatchComponent.cpp`). Corners of `backgroundCornerSize` texels are copied 1:1 and the
   rest is stretched. The image is fitted inside the tile `padding`.
7. **Two engine quirks the theme must work around:**
   - A gridtile `padding` whose values are all below 1 is never scaled to the screen, because
     `Vector4f::operator*=(const Vector2f&)` in `Vector4f.cpp` is a no-op. So write it in pixels.
   - An unselected tile's frame is tinted `AAAAEE` unless `backgroundColor` is set
     (`GridTileComponent::resetProperties`).
8. **A game list opens in the theme's `defaultView`** when the view style is "automatic", which
   is the default (`ThemeData::parseTheme`, `ViewController`).
9. **A text line that doesn't fit is cut to "..."** unless `autoScroll` is `horizontal`, which
   scrolls it instead. Only lines that don't fit scroll (`TextComponent.cpp`).
10. **Menus are themed by fixed element names** in the `menu` view, and any other name is
    silently ignored (`ThemeData::ThemeMenu`).
11. **The engine lists every ROM file,** so the full library shows each dump separately (for
    example, two copies of *Yoshi's Story*). A gamelist entry with `<hidden>true</hidden>`
    removes one from the list.
12. **Card art comes from the gamelist's `<image>`,** which Batocera's scraper fills. A game that
    hasn't been scraped shows the engine's generic cartridge and its name.
13. **An emulator entry can have its own launch command** (`command` attribute on `<emulator>`,
    `SystemData.cpp:1230`; used by `getLaunchCommand`, `:1963`). The per-game choice is the
    standard per-game emulator setting (`FileData::getEmulator`).
14. **`game-selected` is an engine event** that runs scripts with the system, ROM path and name
    (`FileData.cpp:1853`; `Scripting.cpp` lists it as asynchronous).
15. **The engine's HTTP API can change a game's metadata live** (`POST
    /systems/{system}/games/{id}`), and can also notify, launch, add or remove games and reload
    (`HttpServerThread.cpp`).
16. **Theme bindings `{game:<field>}` can read any game metadata field,** plus computed ones like
    `hasSaveState` (`FileData::getProperty`).
17. **Batocera 43's hotkey daemon runs global hotkeys whose value is a shell command.** User
    files in `/userdata/system/configs/hotkeygen/` add hotkeys (`common_context.conf`) and map
    controller buttons to them (`*.mapping`). Source: `hotkeygen` `daemon.py` and `utils.py` in
    batocera.linux master, read 2026-11-06. The Control Center
    (`/userdata/system/configs/controlcenter/controlcenter.xml`) is another menu that can run
    commands. Batocera packages no N64 native ports; its packaged ports are each a separate
    system.
18. **`cartridge` is a media field that scrapers never fill** ("Non scrappable /editable
    medias", `MetaData.cpp`), and our theme doesn't otherwise use it.
19. **Save states are found through `es_savestates.cfg`:** per emulator, a `directory`, a `file`
    and `image` pattern, `autosave`, `incremental` and `firstslot` (`SaveStateConfigFile.cpp`).
    With incremental states, the savestate manager sorts them newest first.
20. **A JSON metadata update ignores path fields** (`HttpApi::ImportFromJson` skips `MD_PATH`, then
    reports "404 game not found" because nothing changed). An image field is set by uploading the
    image to `…/games/{id}/media/{field}`, which writes it at the scraper's usual path, sets the
    field and redraws the card (`HttpApi::ImportMedia`).
21. **The Windows engine's saves folder defaults to `/userdata/saves`;** a settings file
    (`emulatorLauncher.cfg` or `emulationstation.ini`) in its home folder makes it
    `<that folder>/../saves` (`Paths::loadCustomConfiguration`). Only the harness needs this; on
    Batocera, `/userdata/saves` is right.
22. **Extra system config merges tag by tag.** Every `es_systems_*.cfg` in the user ES folder
    (`/userdata/system/configs/emulationstation`) is read after `es_systems.cfg`; for a system of
    the same name, each tag it gives replaces that tag (`SystemData::loadAdditionnalConfig`). So
    a file with just `<name>n64</name>` and `<command>` changes only the launch command. Stock
    43.1's N64 entry has no emulator- or core-level commands, which would take precedence
    (`getLaunchCommand`); it's vendored in `frontend/vendor/batocera-43.1/`.
23. **Event scripts live in `<user ES folder>/scripts/<event>/`** and get the event's arguments;
    `game-selected` is run asynchronously (`Scripting.cpp`).
24. **`GET /runningGame` answers 201 when no game is running,** 200 with the game otherwise
    (`HttpServerThread.cpp`), so a hotkey command can tell whether it's inside a game. Batocera's
    hotkey daemon adds `common_context.conf` to every context and runs a command hotkey with
    `os.system` when the button is released (`/usr/bin/hotkeygen` on 43.1).
25. **Batocera 43.1's engine has no `POST /input`** (it answers 404; the vendored source and the
    Windows engine are newer). Keys reach the VM's engine through QEMU (`sendkey`) instead.

26. **A grid can draw its cards from an `<itemTemplate>`** (inside the `imagegrid`). The engine
    then draws only the template's children on each card (`GridTileComponent::applyTheme`):
    positions and sizes are fractions of the card, a missing `pos` is 0 0 and a missing `size`
    is 1 1 (`GuiComponent::loadThemedChildren`), each child is bound to its own card's game, and
    the selected card plays each child's `activate` storyboard (`deactivate` when it's left).
    `ninepatch` children work there too. Seen on the 43.1 VM, 2026-11-06.
27. **Bound paths and `visible`s are expressions** (`BindingManager.cpp`, `THEMES_BINDINGS.md`):
    string concatenation, `==`/`!=`, the `c ? a : b` ternary, `exists(path)` and `empty(value)`.
    A bound path isn't resolved against the theme, so it's written absolute. `{game:thumbnail}`
    has no fallback to the image (`FileData::getThumbnailPath(false)`), unlike the classic grid.
    After a game ends, the cards are bound again, so a suspend point made in the game shows on
    return (VM, 2026-11-06).
28. **With `centerSelection`, `scrollLoop` and `animateSelection`, the carousel rotates around the
    centre card** (`CenterSelection::FULL` in `ImageGridComponent::onCursorChanged`,
    `ImageGridComponent.h:859-939`, `:1200-1235`): the active card stays in the middle column, the
    row wraps continuously across both ends, and a 250 ms cubic ease-out camera animation slides
    the cards as the selection changes.
29. **N64 save states are `/userdata/saves/n64/<ROM file name>.st<slot>`, slots from 0.** N64's
    default emulator is `mupen64plus` (`configgen-defaults.yml`), whose entry in
    `es_savestates.cfg` is `directory="n64" file="{{romfilename}}.st{{slot0}}"
    nofileextension="false" firstslot="0" incremental="true"`; both are vendored in
    `frontend/vendor/batocera-43.1/`. Incremental: a new state takes the next free slot number.
30. **The game list's order is the gamelist's names, case-insensitive.** The list is the ROM files
    with the system's extensions, less hidden entries (`ShowHiddenFiles` is off), each named by its
    `sortname`, else its `name`, else its file name's stem; sorted by
    `StringUtil::compareIgnoreCase`, with favourites-first and leading-article stripping both off
    by default (`Settings.cpp`, `FileSorts.cpp`).
31. **Game launch shows `<theme>/gamesplash.xml` when present** (`Splash.cpp:32-56`), instead of
    the built-in `:/gamesplash.xml` (which shows the game's scraped screenshot and title). `Splash`
    is not a `GuiComponent` and has no `update(int deltaTime)` method (`Splash.cpp:255-337`), so a
    `<storyboard>` in `gamesplash.xml` freezes at `t=0`. With `GameTransitionStyle` set to
    `"instant"` (`ViewController.cpp:679-702`), the engine hands off immediately to
    `n64mini-launch.sh`, which runs the live SDL2 card-flip, cubic ease-out expansion and
    cross-fade animation while `gamesplash.xml` holds the final full-screen boot frame underneath.

---

### AC-T01: The theme passes every automated check

The theme validates with no errors or warnings for N64 and all built-in collections, and the
real engine logs no theme warnings when it loads it.

- **Status:** done
- **Verify:** gate, engine
- **Tests:** `test_n64_theme.py::test_n64_theme_has_no_errors`, `test_gates.py::test_validator_cli_fails_on_an_invalid_theme`

### AC-T02: N64-era look, generated reproducibly

Backgrounds and frames use the locked style: low-poly chiseled facets, 3-point texture
filtering, RGBA5551 ordered dither. The art is generated by `n64mini-generate-assets` and
reproduces byte for byte.

- **Status:** done
- **Verify:** gate, look
- **Tests:** `test_theme_assets.py::test_generator_reproduces_committed_art`, `test_theme_assets.py::test_surface_texture_is_deterministic_and_rgba5551_quantised`

### AC-T03: Bundled, licensed fonts only

Every font the theme uses ships inside it, with its licence. No device font paths.

- **Status:** done
- **Verify:** gate
- **Tests:** `test_n64_theme.py::test_n64_theme_has_no_errors`

### AC-T04: Horizontal row of game cards

The `grid` view shows a row of 5 cards. With `centerSelection`, `scrollLoop` and
`animateSelection` enabled (owner, 2026-11-07; engine fact 28), the selected card stays in the
middle slot and the cards rotate continuously like a carousel with a smooth slide animation. Game
lists open in it with default settings: the theme declares `defaultView="grid"`, which the engine
uses when the view style is "automatic".

- **Status:** done (on the 43.1 VM, 2026-11-07: the active card stays in the middle column and
  pressing left or right slides the row around it with continuous wrap-around)
- **Verify:** render, engine, look
- **Tests:** `test_n64_theme.py::test_theme_opens_game_lists_in_the_carousel`, `test_n64_theme.py::test_the_highlight_walks_across_the_cards`, `test_carousel.py::test_layout_matches_the_engine_formula_for_the_shipped_grid`

### AC-T05: Cards show each game's own box art, never art baked into the theme

Each card shows the game's image from its gamelist entry inside a theme-provided frame. The
theme ships **frames only**. It contains no game art, both because box art is copyrighted (the
repo is public) and because baked art makes the preview score meaningless. Box art comes from
the user's own scraper.

- **Status:** done (gate and render; on the engine on 2026-11-05, each card shows its gamelist image inside the frame)
- **Verify:** gate, render, engine
- **Tests:** `test_theme_assets.py::test_theme_art_is_only_generated_art`, `test_harness.py::test_mock_box_art_is_generated_not_read_from_the_theme`, `test_n64_theme.py::test_card_frames_are_the_generated_nine_patches`, `test_n64_theme.py::test_each_card_shows_its_player_count_or_none`

The gate checks that every file in the theme's `art/` folder is one the generator writes, so no
game art can be baked in. The preview fills the cards with generated placeholder art, standing
in for the gamelist.

### AC-T06: Selected and unselected cards look different

Unselected cards have a rounded dark frame with a narrow 6 px grey border (`tile_default.png`).
The selected card's frame (`tile_selected.png`, same 6 px border width in off-white / slight-grey
with a bright top-edge LED highlight) fades in over it via the frame's `activate` storyboard
(engine fact 26), without colour pulsing (owner, 2026-11-08, `highlight_1.png`).

- **Status:** done (on the 43.1 VM, 2026-11-08: the selected card has the steady off-white LED-highlighted border, the others grey)
- **Verify:** render, look
- **Tests:** `test_theme_assets.py::test_selected_and_unselected_frames_differ`, `test_n64_theme.py::test_card_frames_are_the_generated_nine_patches`, `test_harness.py::test_the_selected_card_plays_its_activate_storyboard`

### AC-T07: The title banner names the selected game

`md_name` is centred in the dark console pill banner (`y = 103..160`, off-white border with top-edge
LED highlight) in bold sans-serif (`DejaVuSans-Bold.ttf`, matching the Fontworks Rodin Pro DB
sans-serif typeface on the NES/SNES Classic Mini reference; owner, 2026-11-08), white/off-white
(`F2F5FAFF`), and a long title fits or scrolls within the banner without overflowing it.

- **Status:** done (on the engine on 2026-11-08: with `autoScroll` horizontal, long titles fit or scroll inside the dark pill banner)
- **Verify:** render, engine, look
- **Tests:** `test_n64_theme.py::test_banner_title_scrolls_instead_of_being_cut`

### AC-T08: No game details

The owner dropped the release year, publisher and the other details (2026-11-06): a card shows
its box art, player count and suspend points (AC-T19, AC-T09), and nothing else describes the
game. (It used to show the selected game's players, year and publisher in the top bar's plate.)

- **Status:** done (on the 43.1 VM, 2026-11-06: the top plate is empty)
- **Verify:** gate, engine
- **Tests:** `test_n64_theme.py::test_the_details_and_the_pill_are_gone`

### AC-T09: Suspend points on each card

Each card shows four circles, bottom left: circle *n* is filled while the game's save state in
slot *n* exists (engine fact 29), through a `visible` bound to `exists(...)` (fact 27). On the
`Showcase or Full.n64mini` library toggle card, the four empty circles are hidden
(`{game:rom} != "Showcase or Full.n64mini"`, owner, 2026-11-07). Nothing counts the states, so with
Batocera's incremental slots the circles show slots 0 to 3; a state in slot 4 or later shows no
circle. This replaces the SUSPEND POINT pill in the top plate.

- **Status:** done (on the 43.1 VM, 2026-11-06: with `.st0` and `.st1` for 1080 Snowboarding, its
  first two circles were filled and the other games' empty; a `.st2` made while the game ran
  filled the third when the game ended, without a restart; empty circles hidden on the library toggle card)
- **Verify:** render, engine
- **Tests:** `test_n64_theme.py::test_each_card_shows_four_suspend_points_where_batocera_saves_them`, `test_harness.py::test_exists_reads_the_sandbox_console_disk`

### AC-T10: System view

The system selection screen shows the N64 system in the same visual style. It's still reachable
(Back from the game list always goes there, `ISimpleGameListView::goBack`), even with every
other system hidden. The system carousel is transparent over the background (the engine's
default is a pale band), N64 has its own logo plate (without one the engine prints "n64"), and
the game count sits in the title banner.

- **Status:** done (on a Batocera 43.1 VM, 2026-11-06: Back showed the N64 plate and "31 GAMES" in the banner)
- **Verify:** engine, look
- **Tests:** `test_n64_theme.py::test_the_system_view_shows_n64s_plate_over_the_background`

### AC-T11: The Showcase lineup

Showcase is 30 games, kept in one canonical list:
`frontend/src/n64mini/console/data/games.json`, as the games marked `"showcase": true` (owner's brief, 2026-11-06). The same catalogue lists every other game with a playable port (AC-T15). The rule is the
console's main games plus the major games with a native PC port. Each entry records its known
ports for AC-T15, checked against public trackers on 2026-11-06. The switch between Showcase and
Full is AC-T14 (DEC-2).

Each entry names its ROM by a file-name pattern. When several dumps match, the script prefers
US, then `.z64`, then a verified (`[!]`) dump, and fails on a tie rather than guessing. A
lineup game with no ROM on the console is left out of the Showcase (DEC-7); with none at all
the switch fails, since an empty Showcase would look broken.

- **Status:** done (all 30 resolve to exactly one of the owner's 316 deduplicated ROM files, DEC-8)
- **Verify:** gate, engine
- **Tests:** `test_showcase.py::test_the_shipped_lineup_loads`, `test_showcase.py::test_resolve_prefers_us_then_z64_then_verified`, `test_showcase.py::test_resolve_fails_on_a_missing_game_or_a_tie`

The gate checks that the list is well-formed.

### AC-T12: Background music and UI sounds

The theme ships its own background music, played on the system view. The engine supports this
through `<sound name="directory">` (a folder of tracks) or `<sound name="bgsound">` in the
`system` view (`AudioManager.cpp` `changePlaylist`), when the console's `audio.thememusics`
setting is on. The repo is public, so every track must be **original or properly licensed**,
with its licence shipped next to it, exactly like the fonts.

The music is an original upbeat menu loop (32 bars, F major, 136 BPM) composed for this project
and synthesised by the separate menu-music project with N64-style wavetable and FM voices. Its
export is committed as `music/theme.wav` (with `music/LICENSE`); `menu-music/run sync-theme`
writes it, and menu-music's `THEME_PARITY` gate row checks the committed file is its export. The
loop is repeated four times inside the file: the engine plays a theme song once and restarts it
with a 1 s fade-in (`AudioManager.cpp`, `Mix_FadeInMusic(mCurrentMusic, 1, 1000)`), so repeating
it makes that dip happen every ~3.8 minutes. It's set as the system's `bgsound`, which the engine
plays when `audio.thememusics` is on (its default, `Settings.cpp`). The owner's console can carry
another track: `frontend/run bundle` puts `local/theme.wav` (git-ignored) in its place.

- **Status:** done (menu-music exports `music/theme.wav`; `theme_assets/music.py` generates the Suspend Point List SFX `sounds/suspend_save.wav` and `sounds/suspend_poof.wav`)
- **Tests:** `test_n64_theme.py::test_the_theme_plays_its_own_generated_music`, `test_theme_assets.py::test_generator_reproduces_committed_art`, `test_theme_assets.py::test_theme_music_is_the_menu_music_export_with_its_licence`, `test_theme_assets.py::test_suspend_save_and_poof_sfx_are_distinct_mono_wavs`
- **Verify:** gate, engine

### AC-T17: Menus and launch splash screen are styled N64

The main menu and every settings screen use the theme's colours through the `menu` view: a
dark panel, a gold title in the banner's sans-serif, light rows, and the selected row gold. The
engine looks these up by fixed names (`menubg`, `menutitle`, `menufooter`, `menutext`,
`menutextsmall`, `menugroup`; `ThemeData::ThemeMenu`) and silently ignores any other name.
On game launch, `gamesplash.xml` (engine fact 31) and `n64mini.console.launch` flip the selected
centre card over and expand it to full screen (`1280x720`) with cubic ease-out and a cross-fade
into the N64 boot frame on a solid black background (`art/launch_splash.png`, replaced in the
bundle by the captured emulator first frame `local/launch_splash.png`, DEC-5) instead of the
game's screenshot.

- **Status:** done (on the engine on 2026-11-07: menus styled, and launching a game flips and expands the card with ease-out and cross-fade to the N64 boot logo on black instead of the game's screenshot)
- **Verify:** gate, engine, look
- **Tests:** `test_n64_theme.py::test_menu_elements_use_names_the_engine_reads`, `test_n64_theme.py::test_gamesplash_shows_the_boot_frame_on_black_without_game_screenshot`, `test_theme_assets.py::test_launch_splash_is_an_emblem_on_a_black_screen`, `test_launch.py::test_flip_expand_frames_flip_centre_card_and_ease_out_to_full_screen`, `test_launch.py::test_compose_and_render_flip_frames_across_all_phases`, `test_launch.py::test_sdl_canvas_and_animate_cli_entry_point`

### AC-T13: The 4-slot `NUS-006` Suspend Point List drawer

Pressing `D-Pad Down` on the carousel or pressing `Reset` inside an emulator game opens the custom
4-slot `NUS-006` **Suspend Point List** overlay (`n64mini.console.suspend`, DEC-1 option b),
replacing Batocera's 6-cell `GuiSaveState` screen (engine fact 2):

- **Cartridge geometry:** a full-width `1280x348` `NUS-006` cartridge drawer (`art/suspend_drawer.png`)
  with a concentric arch-topped black pocket holds 4 `NUS-006` slot bays (`art/cart_stock.png`,
  `art/cart_selected.png`, `art/cart_empty.png`, `art/cart_target.png`), with hanging `1–4` tabs
  aligned to each cartridge's left top-arc shoulder. Empty bays show recessed dark `NUS-006`
  cartridges labelled `No Data`.
- **Pending / Floating Save mode:** a wingless glowing cyan `NUS-006` cartridge hovers slowly up and
  down (`hover_offset_y`) above the selected slot with a cartridge-width yellow `[C-Down] Save` bar.
  `C-Down` drops the cartridge into the slot (`save_drop_frames`) and plays `suspend_save.wav`;
  `B` discards the floating save with radial cartoon smoke puffs (`poof_frames`) and plays
  `suspend_poof.wav`.
- **Browse mode:** `Left`/`Right` selects Slots `1–4`, `A` launches the game from the selected
  `.st0..3` state via `n64mini.console.launch`, `C-Right` lifts a floating copy, `C-Left`
  poof-deletes the selected slot with `suspend_poof.wav`, and `B` closes the drawer. Glossy 3D
  yellow `C`-arrow icons (`btn_c_up.png`..`btn_c_right.png`) match the specular highlight of `A`/`B`.

- **Status:** done (on the 43.1 VM, 2026-11-09: Pending Save floating cartridge + Browse Slots verified via `vm shot`, `C-Down` save / `C-Left` poof-delete / `D-Pad Down` open & close verified, and `launch.py` verified against Batocera 43.1's `emulatorlauncher.py` / `mupenGenerator.py` `-state_filename` + `-state_slot 8`)
- **Verify:** gate, engine, look
- **Tests:** `test_suspend.py::test_inspect_and_load_model_across_empty_saved_and_pending_states`, `test_suspend.py::test_step_action_transitions_in_floating_and_browse_modes`, `test_suspend.py::test_compose_suspend_frame_and_keyframes_with_real_and_fallback_assets`, `test_suspend.py::test_sdl_backend_video_and_pcm_audio`, `test_suspend.py::test_interactive_loop_and_cli_main`, `test_launch.py::test_consume_resume_args_and_decide_resume_slot`, `test_n64_theme.py::test_menu_elements_use_names_the_engine_reads`

### AC-T14: Showcase ↔ Full library toggle

A script on the console switches between the 30-game Showcase and the full N64 library, then
reloads the engine (DEC-2). Requirements on the script:

- The same action run twice restores the original state exactly (a round trip, testable as an
  invariant on a copy of a gamelist).
- It changes only the `hidden` flags of N64 entries, and every other byte of the gamelist stays
  as it was.
- It fails loud and leaves the gamelist untouched when the gamelist or the Showcase list is
  malformed, when two dumps of a game tie, or when none of the Showcase's games is there. One
  that's missing is simply left out (DEC-7).
- **The console starts in the Showcase** (DEC-7): the first time setup installs the card, it
  switches to the Showcase, if there are ROMs. From then on the owner's choice stands.
- It uses only what the Batocera image ships (stdlib only, per `frontend/AGENTS.md`).

Built as `n64mini-showcase showcase|full|toggle|status [--reload]`
(`n64mini.console.showcase`). What Showcase changed is kept in `gamelist.xml.showcase.json`
beside the gamelist, so Full restores each entry's own `<hidden>` element. A game the owner hid
stays hidden. ROMs with no entry get a minimal hidden one, and Full removes it again. If the
engine has rewritten the file in between, Full works per game. An import contract keeps
`n64mini.console` on the standard library.

**Starting it on the console (researched 2026-11-06).** A theme can't bind a button to a script,
so the mock-up's "Z: SHOWCASE / FULL" isn't possible without patching the engine. Batocera's own
way to run something from the front end is a `.sh` in `/userdata/roms/ports/`, which shows up as
an entry in the Ports system ([wiki](https://www.wiki.batocera.org/systems:ports)). Event scripts
and services run on events, not on demand. Batocera 42 and 43.1 ship Python 3.12.8, enough for
the script. This can't be checked in the Windows engine harness, which doesn't run `.sh`.

**The card (DEC-6, built 2026-11-06).** `Showcase or Full.n64mini` in the N64 ROM folder is a
card, listed because the N64 override adds `.n64mini` to the stock extensions. Its gamelist entry
names where it takes you ("Full library" in the Showcase, "The Showcase" in Full), with generated
art, and sorts last (`sortname`), just left of the first game. A on it runs the toggle instead
of a game. Two engine behaviours shaped how, both found on the real engine:

- The engine runs a launch with its output piped and ends it only when the pipe closes, so the
  toggle detaches (`setsid -f`) and lets go of the pipe (`detach_output`), else each waits for
  the other.
- When a launch ends, the engine holds the launched entry as changed (play count) and writes its
  own copy of that entry back on its next save. So the toggle waits for the launch to end, writes
  the file, sets the card's new name, text and image **in the engine's memory** (the HTTP API),
  then reloads. Other entries aren't touched by that save, so their hidden flags stand.

- **Status:** done (on a clean Batocera 43.1 VM, 2026-11-06: A on the card switched to the Showcase, hiding the three games outside it, and the card turned to FULL LIBRARY; A again restored Full exactly)
- **Verify:** gate, engine
- **Tests:** `test_showcase.py::test_showcase_then_full_gives_back_the_same_bytes`, `test_showcase.py::test_showcase_hides_exactly_the_games_outside_the_lineup`, `test_showcase.py::test_showcase_changes_nothing_but_hidden_flags`, `test_showcase.py::test_full_still_works_after_the_engine_rewrites_the_file`, `test_showcase.py::test_a_bad_input_fails_loud_and_changes_nothing`, `test_showcase.py::test_the_card_is_never_hidden_and_always_offers_the_other_list`, `test_showcase.py::test_the_card_gets_its_new_face_in_the_engine_before_the_reload`, `test_showcase.py::test_detaching_lets_go_of_the_launch_pipe`, `test_launch.py::test_the_library_card_switches_the_list_instead_of_launching`, `test_bundle.py::test_the_override_adds_only_the_card_to_batoceras_extensions`

### AC-T15: Flip a card between the emulator and the native port

The owner's design (2026-11-06):

- A game that has a native PC port can be **flipped** on its card between *Emulator* (the ROM
  in Batocera's N64 emulator) and *Port* (the native program).
- The card's saves follow the flip: the saves shown are those of the selected mode.
- **A game with no port shows nothing to flip**, with no indicator and no option. Most games
  have no port.
- **Only an installed port counts** (owner's ruling, 2026-11-06, option b). A game is
  flippable only when its port is listed in `ports.json`. If the port is removed, `init` clears
  the game's badge and puts it back to Emulator. If a port goes missing anyway, the launcher
  refuses rather than quietly using the emulator.

How the engine can support it (engine facts 13-17):

- **Launching:** the mode is kept in our own `modes.json`, written by the flip. A launch wrapper
  for N64 reads it when a game starts: Port runs the port launcher, and anything else hands over
  to Batocera's normal launcher with the same arguments. The engine's own per-game emulator
  setting isn't used, because Batocera reads `batocera.conf` into memory at start-up
  (`FileData::getEmulator` → `SystemConf`), so a change there would only apply after a restart.
- **The flip input:** a global hotkey (`common_context.conf` + a `.mapping` for an unused N64
  controller button) runs our flip command. The button is **C-down** (owner left it to us,
  2026-11-06). Z, L and R are out because the carousel uses the shoulders and triggers to jump
  (`ISimpleGameListView.cpp:301-326`). If the actual controller reports C-buttons as an analog
  stick, which the hotkey daemon can't map, another button gets chosen then. The `game-selected` event tells it which card is
  selected. Inside a running game the command must do nothing.
- **Showing it only where a port exists:** a ported game's `cartridge` field (engine fact 18)
  points at an EMULATOR or PORT badge, and the theme's badge image binds to `{game:cartridge}`.
  Games without a port leave it empty, so nothing is drawn. The flip swaps the badge live
  through the engine's HTTP API, along with the per-game emulator setting.
- **Saves:** in Emulator mode, the card's suspend points are the emulator's save states
  (AC-T18). A port is an ordinary program whose state can't be captured from outside, so in Port
  mode the saves are the port's own in-game saves, and no suspend points are made.

- **Status:** planned (on the engine on 2026-11-06: in the Showcase, 1080 Snowboarding, which has no port, shows nothing; Banjo-Kazooie shows the N64 badge; uploading the PC PORT badge, as `n64mini-flip` does, changed the card live, but the engine then rebuilds the whole grid view (`GridGameListView::onFileChanged` → `reloadGameListView` on any metadata change), showing a black screen with a red clock for 1–4 s in the VM (seen 2026-11-06; still screenshots had missed it). The owner accepted the flash for now (2026-11-07): time it on the Radxa X4 and revisit only if it is long there. In the windowed VM (`VM_DISPLAY=window`), F2 runs the flip. Since AC-T19 the badge sits on every card, not under the selected one. Launch routing, also on the engine: pressing A ran `n64mini-launch` (dry-run, through WSL) with the engine's own arguments. Banjo-Kazooie, flipped to Port, chose Port and refused because no port is installed, rather than falling back; 1080 Snowboarding went to `emulatorlauncher` unchanged. The engine log showed `game-selected` firing with the game's full path. **On a real Batocera 43.1 (the VM, with OpenGL, 2026-11-06), after a clean install from the stick:** the engine came up in the N64 carousel; `game-selected` recorded each highlighted card; Mario Kart 64 showed the N64 badge; the flip command (the hotkey's) changed it to PC PORT live; A on it launched SpaghettiKart through the override, which drew the game from the `.o2r` Torch had built on the console; flipped back, A launched Batocera's `mupen64plus` with the stock arguments. Console files (2026-11-06): the bundle installs `es_systems_n64.cfg` (engine fact 22), the `game-selected` script (fact 23), the flip hotkey's command in `common_context.conf` (fact 24; the flip does nothing while a game runs) and `ports.json` (AC-S04). In the Batocera VM, with the engine's arguments filled in by hand: the event script recorded the game, an unflipped game reached `emulatorlauncher` with exactly the stock arguments, and a flipped one started the port. Still to do: the **C-down mapping**, which needs the actual controller (hotkeygen picks a mapping by the device's name, vendor and product, and some N64 pads report C-buttons as a stick); and everything on hardware. A launch that fails (a port missing) now shows its reason on screen.)
- **Verify:** engine, look
- **Tests:** `test_launch.py::test_an_unflipped_game_goes_to_emulatorlauncher_unchanged`, `test_launch.py::test_a_flipped_game_runs_its_installed_port`, `test_launch.py::test_a_flipped_game_without_a_usable_port_fails_instead_of_falling_back`, `test_launch.py::test_the_shell_wrapper_execs_exactly_the_chosen_command`, `test_flip.py::test_only_the_chosen_dump_of_a_ported_game_counts`, `test_flip.py::test_mark_badges_ported_games_only_by_their_mode`, `test_flip.py::test_flip_toggles_a_ported_game_and_ignores_others`, `test_flip.py::test_a_failed_badge_upload_leaves_the_mode_unchanged`

**Testable on this machine:** the indicator binding, the metadata update, the emulator entry's
launch routing (to a stand-in command) and the `game-selected` script, because the Windows
engine has all of these. **Only on hardware:** the hotkey daemon, which is Linux-only.

**Parked (owner, 2026-11-06):** Super Mario 64. The owner has only the Japanese *Shindou
Edition* ROM, and whether the SM64 ports accept it is unchecked.

### AC-T18: The reset button makes a suspend point

The owner's design (2026-11-06): the case's reset button, pressed during a game, captures the
game's state and returns to the carousel. The game's suspend points then show the new state with
a screenshot, ready to restore, as on the SNES Classic.

- **Wiring:** the Radxa X4's 40-pin header belongs to its RP2040 microcontroller, not to Linux
  ([Radxa docs](https://docs.radxa.com/en/x/x4/software)), so `gpioget` can't read it. Firmware
  on the RP2040 presents the button to Batocera as a USB keyboard key (for example F13), and the
  hotkey daemon maps that key (engine fact 17).
- **Capturing:** the key runs Batocera's "exit game" action, with N64 auto-save on exit and
  incremental save states on. Each exit then becomes a new suspend point with its screenshot, and
  the savestate manager (AC-T13) lists them newest first and launches into one
  (`GuiSaveState.cpp`, engine fact 19).
- **Port mode:** reset just exits; the port keeps its own in-game saves (AC-T15).

- **Status:** planned. On the Batocera 43.1 VM on 2026-11-11, pressing the Reset hotkey (F9, the same hotkeygen action as the RP2040's F13) in a running game staged `<rom>.pending` with its screenshot, quit the game and opened the `Suspend Point List` in Pending Save mode; `Start` resumed from it (`-state_slot 9 -state_filename <rom>.st9`); a second Reset staged the new state, not the resumed copy (B3); `C-Down` filed it to slot 1. The 2026-11-09 status claimed this earlier, but through hotkeygen the script deadlocked (it waited on keys hotkeygen couldn't read until the script returned); it now detaches. The physical button and RP2040 firmware `firmware/rp2040_reset/code.py` need the Radxa X4 hardware.
- **Verify:** gate, engine, look
- **Tests:** `test_reset.py::test_decide_distinguishes_emulator_and_port_when_a_game_runs`, `test_reset.py::test_stage_pending_stages_overflow_or_recent_slot_without_touching_saved_slots`, `test_reset.py::test_notify_suspend_drawer_and_cli_stage_open`, `test_reset.py::test_the_reset_command_returns_before_it_sends_keys_through_hotkeygen`, `test_reset.py::test_a_stale_resume_copy_is_not_staged_as_the_new_suspend_point`, `test_launch.py::test_an_unflipped_game_goes_to_emulatorlauncher_with_saves_pinned_to_slot_9`, `test_reset.py::test_pin_mupen_slot_updates_current_state_slot_when_present`, `test_bundle.py::test_the_reset_hotkey_maps_f13_to_the_reset_script`

**Testable on this machine:** the listing of suspend points with screenshots in the styled
screen, using an `es_savestates.cfg` and correctly named state files in the sandbox. **Only on
hardware:** the button, the firmware, and the state being written on exit.

### AC-T16: Three Classic Mini button prompts (`Up=Menu`, `Down=Save States`, `A=Start Game`); no overlays

The controls legend above the lower console bar (`y = 591`) shows only the three NES/SNES Classic
Mini button prompts (`[D-Pad Up] Menu`, `[D-Pad Down] Save States`, `[A] Start Game`; owner,
2026-11-08, `highlight_1.png`), while the engine's built-in 7-button `helpsystem` is hidden
(`textColor` and `iconColor` set to `00000000`). The engine's clock and battery/network overlays
are hidden through the `screen` view (`Window::onThemeChanged`).

- **Status:** done (on the 43.1 VM, 2026-11-08: only the three Classic Mini prompts sit above the
  bottom bar; the engine's 7-button help bar, clock and battery icon are hidden. 2026-11-12, after
  the daemon became `n64mini.console.topbar`: keyboard `Up` lit Settings, `Right` moved to
  Showcase/Full, `A` switched to Full with its toast, `Down` opened the drawer and `Esc` closed it)
- **Verify:** gate, engine, look
- **Tests:** `test_n64_theme.py::test_help_prompts_are_styled_and_overlays_hidden`, `test_n64_theme.py::test_help_prompts_sit_above_the_bottom_bar`, `test_topbar.py::test_up_opens_the_bar_left_right_move_and_down_closes`, `test_topbar.py::test_on_the_carousel_down_opens_the_drawer_and_f3_toggles`, `test_topbar.py::test_keys_the_daemon_uses_never_reach_the_engine_others_pass_through`

The preview doesn't draw the help bar, so check it with `./run shot`.

### AC-T19: Cards show the box art, the player count and the flip badge

Each card is drawn from the grid's item template (engine fact 26): the frame (AC-T06), the
game's box art (its thumbnail, else its image, as the classic grid fell back), its Emulator/Port
badge (AC-T15) over the foot of the art, its suspend points (AC-T09) bottom left, and its player
count bottom right as `1P`, `2P` or `4P`. The count is the engine's `{game:playerCount}`, the
highest number in "1-4"; a game whose gamelist has no players shows none.

- **Status:** done (on the 43.1 VM, 2026-11-06: 2P, 1P, 4P on the first cards, the N64 badge on
  the ported Banjo-Kazooie and Bomberman 64)
- **Verify:** render, engine, look
- **Tests:** `test_n64_theme.py::test_each_card_shows_its_player_count_or_none`, `test_harness.py::test_player_count_is_the_engines`, `test_harness.py::test_the_template_draws_each_cards_own_game`

### AC-T20: The strip of every game, with a cursor over the selected one

Between the cards and the help prompts, every game's box art in a row, small, with a ▼ cursor
over the selected game that moves as the selection does (owner's reference, 2026-11-06). A theme
can't draw other games' art or know the cursor's position, so the console draws one strip image
per game (`n64mini.console.strip`): the whole list in the engine's order (engine fact 30), the
cursor over that game. The theme shows the selected game's image through a bound path (fact
27). Changing the selection only changes which image is shown, so nothing reloads. A list longer
than 32 games shows the 32 around the selected one. The strips are drawn again whenever the list
or its art can change: after setup dresses the list, after the Showcase/Full switch, and at each
boot (after a scrape, new art shows from the next boot, as it does on the cards).

Drawing box art needs an image library on the console: Batocera 43.1 ships Pillow (system fact
in `system.md`), and only this module may use it.

- **Status:** done (on the 43.1 VM, 2026-11-06, installed through `update.sh`: setup drew the 31
  strips of the Showcase; the cursor started over 1080 Snowboarding and followed the selection,
  over the 16th game at Mario Tennis and over the Showcase/Full card, last, at the end, matching
  the engine's order throughout. Switching to Full from the card drew 34 strips and the strip
  showed the new list at once; unscraped games are blank in it, as their cards are.)
- **Verify:** gate, engine
- **Tests:** `test_strip.py::test_the_list_is_the_engines_list_in_its_order`, `test_strip.py::test_the_cursor_sits_over_the_selected_games_art`, `test_strip.py::test_generate_writes_each_games_strip_and_removes_stale_ones`, `test_n64_theme.py::test_the_strip_is_the_selected_games_image_from_the_console`

### AC-T21: The top and bottom bars mirror the front of the N64 console

The top bar (`TOP_H = 92`, 12.78% of 720p) and bottom bar (`BOTTOM_Y = 630`, 90 px = 12.50% of
720p, matching the NES/SNES Classic Mini bar heights) share the same molded N64 charcoal vertical
gradient (`(74, 78, 86)` to `(46, 49, 55)`). The top bar (`art/brand.png`) features a recessed
centre pocket (`470..810, 10..82`) holding the three flat LED-highlighted option icons (Settings
cog, Showcase/Full library toggle swapping between the Gold Star `art/icon_showcase.png` and
Cyan-Blue Fanned 3-Card Hand `art/icon_full.png`, and BG music toggle) and places the 3D cube
emblem on the right (`frames.build_cube_emblem`, horizontally aligned at `x = 1158` with the red
Power LED below). The emblem is original and generic (no marks, DEC-5): an isometric cube with
one colour per face and `64` on each visible face. The owner's own `n_logo.svg` replaces it when
configured (`N64MINI_SVG_N_LOGO` or `N64MINI_SVG_DIR`). Toggling Showcase/Full via the middle top-bar button or `KEY_F3` displays a
temporary drop-down callout toast (`art/toast_showcase.png` `"Showcase"` or `art/toast_full.png`
`"Full Library"`) directly beneath the middle button while updating the carousel live.
The bottom bar (`art/console.png`) places the brand wordmark (`DEFAULT_BRAND_TEXT`, `CLASSIC 64`:
white `CLASSIC` + red superscript `64`, without the black bezel, extracted from
`frontend/assets/svg/front_bezel.svg`) in
the bottom-left corner with its recessed underline groove, and the glowing crimson/red pill Power
LED in the bottom-right corner (`x = 1158`). Both remain separate layers so `frontend/run bundle`
can still swap in `local/brand.png` or `local/console.png` when present.

- **Status:** done (Tier-1 preview, 2026-11-11: original cube emblem; the 43.1 VM look of 2026-11-09 predates it and needs repeating)
- **Verify:** gate, look
- **Tests:** `test_n64_theme.py::test_the_console_is_its_own_layer_the_bundle_can_swap`, `test_theme_assets.py::test_top_and_bottom_bars_share_console_gradient_and_reference_heights`, `test_theme_assets.py::test_committed_svgs_and_console_port_grouping`, `test_theme_assets.py::test_library_icons_and_dropdown_toasts_distinguish_showcase_and_full`, `test_theme_assets.py::test_cube_emblem_has_one_colour_per_face_and_64_on_each`, `test_theme_assets.py::test_brand_uses_the_owners_svg_when_one_is_configured`, `test_strip.py::test_sync_topbar_swaps_generated_showcase_and_full_icons`, `test_topbar.py::test_pressing_each_button`, `test_topbar.py::test_the_pocket_highlights_one_button_over_a_backdrop_taken_once`, `test_topbar.py::test_the_toast_drops_over_the_screen_and_expires`

### DEC-1 (was OPEN-1): Custom 4-slot `NUS-006` Suspend Point List drawer

Ruling (owner, 2026-11-09, superseding initial **(a)**): **(b)** — build our own 4-slot `NUS-006`
cartridge **Suspend Point List** drawer (`n64mini.console.suspend`), opened from `D-Pad Down` on the
carousel or after pressing `Reset` inside an emulator game, with slow floating hover, `C-Down` save
drop + sound, and `B` / `C-Left` cartoon poof vanish + sound. See AC-T13.

### DEC-2 (was OPEN-2): The Showcase ↔ Full switch is an on-console toggle script

Ruling: **(b)**, chosen over the two-entry system view. See AC-T14.

### DEC-3 (was OPEN-3): Original ↔ Port is the per-game emulator option, with a per-card toggle

Ruling: **(b)**, plus a toggle on each card that flips between the two, like the Showcase
toggle but per game. See AC-T15.

### DEC-4 (was OPEN-4): The engine's help prompts replace the painted legend; ribbon and top-bar keys go

Ruling: as recommended. The painted controls legend, the mini-thumbnail ribbon and the labelled
top-bar keys (CRT / VI, CONTROLS, OPTIONS, MANUAL, SHOWCASE) are removed from the background.
The bars and plates stay as decoration. The engine's real help prompts are styled N64, and its
clock and battery overlays are hidden (AC-T16). Engine screenshots showed the painted legend
colliding with the engine's own help bar, and promising buttons that do something else.

### DEC-6: No Ports system, and one system only

Ruling (owner, 2026-11-06): Batocera's Ports system is a user-facing list and isn't used. Nothing
goes in `roms/ports`, and native ports live in a private folder. The console shows N64 only,
with no other systems and no built-in collections (`CollectionSystemsAuto` empty,
`StartupOnGameList` on; `Settings.cpp`). The Showcase ⇄ Full switch is a card in the N64
carousel instead (AC-T14).

### DEC-7: The Showcase leaves out what's missing, and the console starts in it

Ruling (owner, 2026-11-06): a Showcase game with no ROM on the console is left out rather than
stopping the switch, and a fresh console starts in the Showcase, as the SNES Classic does.

### DEC-5 (was OPEN-5): Original branding in the repo, real marks only on the owner's console

Ruling (revised the same day): the repo ships an original N64-era mark of our own, and the
owner's real marks stay out of git. Planned shape:

- The brand moves out of the generated background into its own layer, `art/brand.png`,
  generated and gated like the rest of the art.
- The owner's own version lives in a git-ignored `local/` folder.
- The deploy step that copies the theme to the console uses `local/brand.png` instead, when
  it exists. The swap happens at deploy time, not in the working tree, so the committed art
  and its byte-for-byte test stay clean.

Built (2026-11-06; emblem replaced 2026-11-11): the background no longer paints the cube and
wordmark; `art/brand.png` carries an original cube emblem (`frames.build_cube_emblem`, AC-T21),
its own image layer in the top bar,
and `frontend/run bundle` puts `local/brand.png` (git-ignored, 220x70) in its place when it
exists (`draw_n64_cube` has been removed from `theme_assets/shapes.py` and `vulture_allowlist.py`).
Still to do before the repo goes public: the tracked mock-up
`frontend/assets/golden_reference.png` also shows the marks.

Owner ruling (2026-11-11): the header emblem is replaced by the original cube (AC-T21). The
bottom wordmark's red superscript `64` (`front_bezel.svg`) and the system-view badge
(`nintendo_logo.svg`, with `CLASSIC` lettering) stay as they are, as does the CAD rear plaque ring.

### DEC-8 (was OPEN-6): Duplicate ROM dumps deleted from `N64/ROMS`

Ruling (owner, 2026-11-07): **(c)** — delete the duplicate ROM dumps from `N64/ROMS/`, keeping one
canonical dump per game ranked by the Showcase preference rule (US `(U)` first, `.z64` > `.v64` >
`.n64`, verified `[!]` first), while keeping distinct releases (*Ocarina of Time — Master Quest*
and *Pokémon Snap Station — Kiosk*) separate. 150 duplicate ROMs were removed, leaving 316 unique
games in `N64/ROMS/`.

---

## Open questions (for the human)

*(None currently open; see resolved decisions above.)*

### The options offered for 1, 2, 3, 5 and 6 follow for the record.

### OPEN-6: Duplicate dumps in the full library

The owner's folder often holds several dumps of one game (`.z64`/`.v64`/`.n64`, regions), and
the engine lists each one (engine fact 11). The Showcase already picks one per game.

- **(a) Leave Full as it is:** every file is listed, as Batocera does by default.
- **(b) Hide the extra dumps in Full too,** using the Showcase's preference rule (US, `.z64`,
  verified). This uses the same `hidden` mechanism and the same exact undo.
- **(c) Tidy the ROM folder itself:** a one-off clean-up, outside the theme.

**Recommendation: (b)** if the duplicates bother you on the console; (a) costs nothing for now.

### OPEN-1: Suspend points. Accept Batocera's built-in screen, or build our own?

The SNES Classic shows exactly 4 slots in a drawer under the carousel. Batocera's screen
(engine fact 2) is hard-coded: 6 visible cells, unlimited slots, and only colours, fonts and
frame are themeable.

- **(a) Use Batocera's screen, styled N64** through the menu theme. No code, and suspend points
  work today. It won't look like a 4-slot drawer.
- **(b) Build a custom 4-slot drawer.** This isn't possible in a theme. It would mean patching and
  maintaining our own EmulationStation build, or writing a separate overlay app.

**Recommendation: (a).** Revisit (b) only once everything else is done.

### OPEN-2: How does the Showcase (30) ↔ Full switch work?

- **(a) Two entries in the system view:** a "Showcase" custom collection holding the 30 games,
  and the full N64 system. The console boots into Showcase (the engine's `StartupSystem`
  setting), and "switching" means moving one step in the system view. No code at all.
- **(b) A script on the console** that rewrites the gamelist's `hidden` flags and reloads the
  engine. That gives a true toggle, but needs Python on the device (unverified) and edits user
  data.
- **(c) The engine's built-in filters.** Per-user, and not a one-button switch.

**Recommendation: (a).** Native, robust, and nothing to maintain.

### OPEN-3: How does Original N64 ↔ Native Port work?

The planned pill switch on each card isn't possible: a theme can't draw an interactive control,
or show data the engine doesn't have.

- **(a) Separate entries.** The port appears as its own game, in Batocera's "Ports" system or
  next to the ROM, with a `PORT` label in its name or genre. Simple, but two cards per game.
- **(b) The engine's per-game emulator option.** Batocera lets you pick the emulator or core for
  each game in its game menu. A port would be registered as an "emulator" for that game. It
  needs verifying that Batocera can launch an external port binary that way.
- **(c) Our own launcher** that wraps the launch and reads a per-game choice (from a manifest
  like `docs/04` §3). Most flexible, but it's on-device code, and the choice is made in a menu,
  not on the card.

**Recommendation:** verify (b) first, because it keeps one card per game. Fall back to (a).

### OPEN-5: Nintendo branding in a public repo

The generated background paints a "NINTENDO" wordmark, the N64 cube logo and "4MB EXP PAK",
and so does the golden mock-up. In a public repository, that's a trademark risk.

- **(a) Replace them with original branding** (a name and mark of our own), keeping the N64-era
  style.
- **(b) Keep them**, and keep the repo private.

**Recommendation: (a).**
