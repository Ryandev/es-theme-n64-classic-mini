"""DEC-6: the engine's settings, merged into es_settings.cfg (console/es_settings.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from n64mini.console import es_settings as es

# What the engine writes (Settings::saveFile through pugixml), owner settings included.
ENGINE_FILE = """<?xml version="1.0"?>
<config>
\t<bool name="DrawClock" value="false" />
\t<int name="MusicVolume" value="90" />
\t<float name="GameOnGameListZoom" value="1.5" />
\t<string name="Language" value="en_GB" />
\t<string name="ThemeSet" value="Carbon &amp; &quot;Co&quot;" />
</config>
"""

SYSTEMS = """<?xml version="1.0"?>
<systemList>
  <system>
    <fullname>Nintendo 64</fullname>
    <name>n64</name>
    <emulators><emulator name="libretro"><cores><core>mupen64plus-next</core></cores>
    </emulator></emulators>
  </system>
  <system>
    <name>snes</name>
  </system>
  <system><name> nes </name></system>
</systemList>
"""


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    user, stock = tmp_path / "user", tmp_path / "stock"
    user.mkdir()
    stock.mkdir()
    (stock / "es_systems.cfg").write_text(SYSTEMS)
    (user / "es_systems_mine.cfg").write_text("<systemList><system><name>pico8</name></system>")
    (user / "es_settings.cfg").write_text("<config/>")  # not a systems file
    return user, stock


def test_reading_keeps_every_setting_with_its_type_and_unescaped_value(tmp_path: Path) -> None:
    path = tmp_path / "es_settings.cfg"
    path.write_text(ENGINE_FILE)
    assert es.read(path) == {
        "DrawClock": ("bool", "false"),
        "MusicVolume": ("int", "90"),
        "GameOnGameListZoom": ("float", "1.5"),
        "Language": ("string", "en_GB"),
        "ThemeSet": ("string", 'Carbon & "Co"'),
    }
    assert es.read(tmp_path / "missing.cfg") == {}


def test_rendering_what_was_read_gives_back_the_engines_file(tmp_path: Path) -> None:
    path = tmp_path / "es_settings.cfg"
    path.write_text(ENGINE_FILE)
    assert es.render(es.read(path)) == ENGINE_FILE


def test_a_file_this_reader_does_not_understand_is_refused_not_dropped(tmp_path: Path) -> None:
    path = tmp_path / "es_settings.cfg"
    path.write_text('<config>\n<string name="A" value="1" />\n<list name="B"/>\n</config>\n')
    with pytest.raises(es.SettingsError, match="unexpected content '<list name=\"B\"/>'"):
        es.read(path)


@given(
    st.dictionaries(
        st.text(st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")), max_size=12),
        st.tuples(
            st.sampled_from(["bool", "int", "string"]),
            st.text(st.characters(codec="utf-8", exclude_categories=("Cs", "Cc")), max_size=12),
        ),
        max_size=6,
    )
)
def test_any_settings_survive_a_write_and_read(entries: dict[str, tuple[str, str]]) -> None:
    """Invariant: what's written is read back exactly, whatever the names and values hold."""
    with tempfile.TemporaryDirectory() as folder:  # hypothesis can't reuse a tmp_path
        path = Path(folder) / "es_settings.cfg"
        path.write_text(es.render(entries))
        assert es.read(path) == entries


def test_system_names_come_from_every_systems_file(tmp_path: Path) -> None:
    user, stock = _dirs(tmp_path)
    assert es.system_names([user, stock, tmp_path / "absent"]) == ["n64", "nes", "pico8", "snes"]


def test_wanted_types_our_settings_and_hides_every_other_system(tmp_path: Path) -> None:
    dirs = _dirs(tmp_path)
    settings: dict[str, es.Value] = {"StartupOnGameList": True, "ThemeSet": "n64", "Volume": 3}
    assert es.wanted(settings, "n64", dirs) == {
        "StartupOnGameList": ("bool", "true"),
        "ThemeSet": ("string", "n64"),
        "Volume": ("int", "3"),
        "HiddenSystems": ("string", "nes;pico8;snes"),
    }
    assert es.wanted({"Off": False}, "n64", dirs)["Off"] == ("bool", "false")


def test_wanted_refuses_a_missing_system_or_a_hand_set_hidden_list(tmp_path: Path) -> None:
    dirs = _dirs(tmp_path)
    with pytest.raises(es.SettingsError, match="no system named 'gc' on this console"):
        es.wanted({"A": "1"}, "gc", dirs)
    with pytest.raises(es.SettingsError, match="HiddenSystems is computed"):
        es.wanted({"HiddenSystems": "nes"}, "n64", dirs)


def test_merging_sets_ours_and_keeps_the_owners(tmp_path: Path) -> None:
    path, dest = tmp_path / "es_settings.cfg", tmp_path / "new.cfg"
    path.write_text(ENGINE_FILE)
    es.merge(path, {"ThemeSet": ("string", "n64-classic-mini"), "X": ("bool", "true")}, dest)
    merged = es.read(dest)
    assert merged["ThemeSet"] == ("string", "n64-classic-mini")
    assert merged["X"] == ("bool", "true")
    assert merged["Language"] == ("string", "en_GB")  # the owner's own setting stays
    assert path.read_text() == ENGINE_FILE  # the original is left for the swap


def test_the_version_names_exactly_the_settings() -> None:
    a = es.version({"A": ("bool", "true"), "B": ("string", "x")})
    assert a == es.version({"B": ("string", "x"), "A": ("bool", "true")})  # order doesn't count
    assert a != es.version({"A": ("bool", "false"), "B": ("string", "x")})
    assert a.startswith("content:")


def test_an_escaped_name_is_read_back_unescaped(tmp_path: Path) -> None:
    """Regression (found by the property above): names were read still escaped."""
    path = tmp_path / "es_settings.cfg"
    path.write_text('<config>\n\t<string name="A &amp; B" value="x" />\n</config>\n')
    assert es.read(path) == {"A & B": ("string", "x")}


def test_merge_conf_sets_keys_in_batocera_conf_and_keeps_others(tmp_path: Path) -> None:
    path, dest = tmp_path / "batocera.conf", tmp_path / "new.conf"
    es.merge_conf(path, {"n64.bezel": "none"}, dest)
    assert dest.read_text() == "n64.bezel=none\n"
    path.write_text("audio.bgmusic=1\n#n64.bezel=consoles\n")
    es.merge_conf(path, {"n64.bezel": "none", "n64.smooth": "0"}, dest)
    assert dest.read_text() == "audio.bgmusic=1\nn64.bezel=none\nn64.smooth=0\n"
