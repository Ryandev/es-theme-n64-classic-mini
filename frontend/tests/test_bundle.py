"""The console bundle (bundle/): its files agree with the manifest, the package and the engine.

Each console file names paths on the console. These tests tie every such path to where the
manifest installs things, and the launch override to Batocera's own N64 command, so a rename on
one side can't leave the console calling a file that isn't there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

import pytest
from lxml import etree

from n64mini.console import setup as st
from n64mini.console.showcase import CARD

REPO = Path(__file__).resolve().parents[2]
BUNDLE = REPO / "bundle"
VENDOR = REPO / "frontend" / "vendor"
PACKAGE = REPO / "frontend" / "src" / "n64mini"
USERDATA = PurePosixPath("/userdata")
# Where `frontend/run bundle` takes each bundled source from (its `cp` lines).
SOURCES = {
    "python/n64mini": PACKAGE,
    "theme": REPO / "themes" / "n64-classic-mini",
    "tools/torch": None,  # built at the pinned commit, not in the repo
}


def _components() -> dict[str, st.Component]:
    return {c.name: c for c in st.load_manifest(BUNDLE)}


def _source(comp: st.Component) -> Path | None:
    if comp.source in SOURCES:
        return SOURCES[comp.source]
    return BUNDLE / comp.source


def _installed(name: str, relative: str = "") -> PurePosixPath:
    target = USERDATA / _components()[name].target
    return target / relative if relative else target


def test_every_bundled_source_exists_and_run_bundle_copies_it() -> None:
    run = (REPO / "frontend" / "run").read_text()
    for comp in _components().values():
        if comp.kind == "port":
            continue
        source = _source(comp)
        if source is None:
            assert 'cp "$torch" "$out/tools/torch/torch"' in run
            # MIT: the binary ships with its licence notice.
            assert 'cp "$BUILD_DIR/torch-src/LICENSE" "$out/tools/torch/LICENSE"' in run
            continue
        assert source.exists(), comp.name
        if comp.source.startswith("console/"):
            assert '"$REPO_ROOT/bundle/update.sh" "$REPO_ROOT/bundle/console" "$out/"' in run


def test_every_executable_glob_matches_a_file_in_its_source() -> None:
    for comp in _components().values():
        source = _source(comp)
        if comp.kind != "files" or source is None:
            continue
        for pattern in comp.executable:
            assert [p for p in source.glob(pattern) if p.is_file()], (comp.name, pattern)


def test_the_scripts_the_console_runs_are_declared_executable() -> None:
    comps = _components()
    assert comps["service"].executable == ("*",)
    assert comps["game-selected"].executable == ("*",)
    for script in ("n64mini.sh", "n64mini-launch.sh"):
        assert any(
            PurePosixPath(f"console/data/{script}").match(g) for g in comps["python"].executable
        ), script


def test_the_launch_override_runs_the_installed_wrapper_with_batoceras_own_arguments() -> None:
    """Engine fact 13; SystemData::loadAdditionnalConfig: our <command> replaces the stock one."""
    stock = etree.parse(VENDOR / "batocera-43.1" / "es_systems_n64.xml").getroot()
    ours = etree.parse(BUNDLE / "console" / "es_systems_n64.cfg").getroot().find("system")
    assert ours is not None
    assert ours.findtext("name") == stock.findtext("name") == "n64"
    tags = [child.tag for child in ours if isinstance(child.tag, str)]  # not the comment
    assert tags == ["name", "extension", "command"]  # nothing else overridden
    stock_command = (stock.findtext("command") or "").split()
    command = (ours.findtext("command") or "").split()
    assert stock_command[0] == "emulatorlauncher"
    assert command[0] == str(_installed("python", "console/data/n64mini-launch.sh"))
    assert command[1:] == stock_command[1:]


def test_the_override_lands_where_the_engine_reads_extra_systems() -> None:
    """``es_systems_*.cfg`` in the user ES folder (SystemData::loadAdditionnalConfig)."""
    source = (VENDOR / "batocera-emulationstation" / "SystemData.cpp").read_text()
    assert 'startsWith(Utils::FileSystem::getFileName(customPath), "es_systems_")' in source
    target = _installed("es-systems-n64")
    assert target.parent == USERDATA / "system/configs/emulationstation"
    assert target.name.startswith("es_systems_")
    assert target.suffix == ".cfg"


def test_the_game_selected_script_lands_in_the_engines_event_folder() -> None:
    """``<user ES path>/scripts/<event>/`` (Scripting.cpp fireEvent)."""
    source = (VENDOR / "batocera-emulationstation" / "Scripting.cpp").read_text()
    assert 'Paths::getUserEmulationStationPath() + "/scripts/" + eventName' in source
    assert '"game-selected"' in source
    target = _installed("game-selected")
    assert target.parent == USERDATA / "system/configs/emulationstation/scripts/game-selected"


def _paths_in(text: str) -> list[PurePosixPath]:
    return [PurePosixPath(p) for p in re.findall(r"/userdata/[\w./-]+", text)]


@pytest.mark.parametrize(
    "console_file",
    ["game-selected.sh", "hotkeygen-common_context.conf", "es_systems_n64.cfg"],
)
def test_every_installed_path_a_console_file_names_is_one_the_manifest_installs(
    console_file: str,
) -> None:
    text = (BUNDLE / "console" / console_file).read_text()
    python = _installed("python")
    paths = _paths_in(text)
    assert paths, console_file
    for path in paths:
        assert path.is_relative_to(python), path
        assert (PACKAGE / path.relative_to(python)).is_file(), path


def test_the_flip_hotkey_runs_the_flip_command() -> None:
    keys = json.loads((BUNDLE / "console" / "hotkeygen-common_context.conf").read_text())
    command = keys["n64mini-flip"].split()
    assert command[1:] == ["flip", "flip"]
    assert not command[0].startswith("KEY_")  # hotkeygen: a KEY_ value sends keys, not a command


def test_the_reset_hotkey_maps_f13_to_the_reset_script() -> None:
    """AC-T18: the RP2040 Reset button sends KEY_F13 -> n64mini-reset -> n64mini-reset.sh."""
    mapping = json.loads((BUNDLE / "console" / "hotkeygen-default_mapping.conf").read_text())
    assert mapping["KEY_F2"] == "n64mini-flip"
    assert mapping["KEY_F3"] == "n64mini-library"
    assert mapping["KEY_F13"] == "n64mini-reset"
    keys = json.loads((BUNDLE / "console" / "hotkeygen-common_context.conf").read_text())
    assert keys["n64mini-library"].split()[1:] == ["library", "toggle", "--reload"]
    assert keys["n64mini-reset"] == str(_installed("python", "console/data/n64mini-reset.sh"))


def test_the_service_has_a_name_batocera_accepts_and_finds_the_stick() -> None:
    target = _installed("service")
    assert target.parent == USERDATA / "system/services"
    assert re.fullmatch(r"[_A-Za-z][_A-Za-z0-9]*", target.name)  # batocera-services' own rule
    text = (BUNDLE / "console" / "n64mini-service.sh").read_text()
    assert "for bundle in /media/*/n64mini-bundle; do" in text  # storage manager: /media/<label>
    assert 'bash "$bundle/update.sh"' in text
    # AC-T20: every boot draws the strip, after any update, with or without a stick.
    loop_end = text.index("done")
    strip = text.index("n64mini.sh strip")
    assert loop_end < strip
    assert "exit" not in text[text.index("for bundle") : loop_end]  # an update doesn't skip it


def test_install_enables_the_service_the_manifest_installs() -> None:
    text = (BUNDLE / "install.sh").read_text()
    target = _installed("service")
    assert f"/userdata/system/services/{target.name}" in text
    assert f"batocera-services enable {target.name}" in text


# ---------------------------------------------------------------- DEC-6: the engine's settings
def test_every_setting_is_one_the_engine_has_with_the_type_it_stores() -> None:
    """Names and types from the defaults in Settings.cpp; an unknown name would do nothing."""
    source = (VENDOR / "batocera-emulationstation" / "Settings.cpp").read_text()
    maps = {bool: "mBoolMap", int: "mIntMap", str: "mStringMap"}
    settings = _components()["settings"]
    assert settings.settings
    for name, value in settings.settings:
        assert f'{maps[type(value)]}["{name}"] =' in source, name


def test_the_settings_pick_our_theme_and_our_one_system() -> None:
    comps = _components()
    settings = dict(comps["settings"].settings)
    assert settings["ThemeSet"] == PurePosixPath(comps["theme"].target).name
    assert comps["theme"].target.startswith("themes/")
    system = (
        etree.parse(BUNDLE / "console" / "es_systems_n64.cfg").getroot().findtext("system/name")
    )
    assert comps["settings"].only_system == system == settings["StartupSystem"]
    assert settings["StartupOnGameList"] is True
    assert settings["CollectionSystemsAuto"] == ""  # DEC-6: no built-in collections
    assert settings["GameTransitionStyle"] == "instant"  # hand off immediately to launch flip/fade
    assert settings["kodi.enabled"] is False
    assert settings["kodi.atstartup"] is False
    defaults = (VENDOR / "batocera-43.1" / "configgen-defaults-n64.yml").read_text()
    assert "bezel:              consoles" in defaults  # overridden by n64.bezel=none


def test_what_the_engine_reads_only_at_start_up_restarts_it() -> None:
    comps = _components()
    for name in ("settings", "theme", "es-systems-n64", "es-resources"):
        assert comps[name].restart_engine, name
    assert not comps["python"].restart_engine
    savestate_cpp = (VENDOR / "batocera-emulationstation" / "GuiSaveState.cpp").read_text()
    assert '":/freeslot.svg"' in savestate_cpp
    res_dir = BUNDLE / "console" / "resources"
    for svg_name in ("freeslot.svg", "cartridge.svg"):
        assert (res_dir / svg_name).is_file(), svg_name
    assert _installed("es-resources") == USERDATA / "system/configs/emulationstation/resources"


def test_start_up_settings_are_set_while_the_engine_is_suspended() -> None:
    """The engine writes its whole settings back as it quits, so they're set while it's down.

    Grounded in the vendored sources: the exit path saves (main.cpp -> ViewController::saveState
    -> Settings::saveFile), and Batocera's own suspend quits the engine and holds its launch loop
    until resume.
    """
    es_main = (VENDOR / "batocera-emulationstation" / "main.cpp").read_text()
    assert "ViewController::saveState();" in es_main
    view = (VENDOR / "batocera-emulationstation" / "ViewController.cpp").read_text()
    save_state = view[view.index("void ViewController::saveState()") :]
    assert "Settings::getInstance()->saveFile();" in save_state[: save_state.index("\n}\n")]
    init = (VENDOR / "batocera-43.1" / "S31emulationstation").read_text()
    assert 'curl -s --connect-timeout 2 "http://localhost:1234/quit"' in init  # suspend
    assert 'echo "resume" > "${FIFO_PATH}"' in init  # resume
    loop = (VENDOR / "batocera-43.1" / "emulationstation-standalone").read_text()
    assert 'read < "/tmp/es-resume.fifo"' in loop

    update = (BUNDLE / "update.sh").read_text()
    order = [
        "setup --phase live --engine",
        'pending="$(setup --phase stopped --pending)"',
        '"$engine" suspend',
        "setup --phase stopped || status=1",
        'timeout 10 "$engine" resume',
    ]
    positions = [update.index(step) for step in order]
    assert positions == sorted(positions)
    assert "engine=/etc/init.d/S31emulationstation" in update
    assert "/quit" not in update
    assert "/restart" not in update  # it reboots the console (HttpServerThread.cpp)


def test_the_override_adds_only_the_card_to_batoceras_extensions() -> None:
    """AC-T14: the N64 list shows the Showcase/Full card; every stock extension still works."""
    stock = etree.parse(VENDOR / "batocera-43.1" / "es_systems_n64.xml").getroot()
    ours = etree.parse(BUNDLE / "console" / "es_systems_n64.cfg").getroot()
    stock_ext = (stock.findtext("extension") or "").split()
    our_ext = (ours.findtext("system/extension") or "").split()
    assert our_ext == [*stock_ext, PurePosixPath(CARD).suffix]


def test_the_card_is_installed_under_the_name_the_console_code_expects() -> None:
    assert "library-card" not in _components()
    assert f'rm -f "/userdata/roms/n64/{CARD}"' in (BUNDLE / "update.sh").read_text()


def test_boot_splash_replaces_batoceras_video_and_drops_batocera_boot_branding() -> None:
    """AC-S09: S28splash plays /userdata/splash/*.mp4 when present; S00bootcustom runs
    /boot/boot-custom.sh before S03system-splash draws boot-logo.png; emulationstation-standalone
    passes es.customsargs (--no-splash) to emulationstation."""
    s00 = (VENDOR / "batocera-43.1" / "S00bootcustom").read_text()
    assert "bash /boot/boot-custom.sh $1" in s00
    s03 = (VENDOR / "batocera-43.1" / "S03system-splash").read_text()
    assert 'image="/usr/share/batocera/splash/boot-logo.png"' in s03
    assert 'alt_image="/usr/share/batocera/splash/boot-logo-4x3.png"' in s03
    s28 = (VENDOR / "batocera-43.1" / "S28splash").read_text()
    assert "SPLASH_DIR=/userdata/splash" in s28
    assert 'FM_VIDEO=".*\\.\\(mp4\\)"' in s28
    assert "video_subtitle=$(batocera-settings-get splash.screen.subtitle)" in s28
    assert "--no-sub-auto" in s28
    s30 = (VENDOR / "batocera-43.1" / "S30splashscreencontrol").read_text()
    assert "splash_autoplay" in s30
    standalone = (VENDOR / "batocera-43.1" / "emulationstation-standalone").read_text()
    assert 'CUSTOMESOPTIONS="$(/usr/bin/batocera-settings-get es.customsargs)"' in standalone
    es_main = (VENDOR / "batocera-emulationstation" / "main.cpp").read_text()
    assert 'strcmp(argv[i], "--no-splash") == 0' in es_main

    splash = _components()["splash"]
    assert splash.kind == "file"
    assert _installed("splash") == USERDATA / "splash/splash.mp4"
    run = (REPO / "frontend" / "run").read_text()
    assert 'cp "$REPO_ROOT/local/splash.mp4" "$out/console/splash.mp4"' in run
    assert 'cp "$REPO_ROOT/local/theme.wav" "$out/theme/music/theme.wav"' in run
    update = (BUNDLE / "update.sh").read_text()
    assert 'bash "$bundle/console/boot-custom.sh" install' in update
    boot_custom = (BUNDLE / "console" / "boot-custom.sh").read_text()
    assert "/usr/share/batocera/splash/boot-logo.png" in boot_custom
    assert "/usr/share/batocera/splash/splash.srt" in boot_custom
    assert "/echo Booting Batocera/d" in boot_custom
    assert "/SAY Booting Batocera/d" in boot_custom
