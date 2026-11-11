#!/usr/bin/env bash
set -euo pipefail

# Provisions the standalone Batocera EmulationStation live test harness
# inside <repo>/test_harness/live/

ROOT_DIR="$(cd "$(dirname "$0")/../../../.." && pwd)"
HARNESS_DIR="${ROOT_DIR}/test_harness/live"
BIN_DIR="${HARNESS_DIR}/bin"
HOME_DIR="${HARNESS_DIR}/home"
ES_CFG_DIR="${HOME_DIR}/.emulationstation"
ROMS_DIR="${HOME_DIR}/roms/n64"
SAVES_DIR="${HOME_DIR}/saves/n64"

mkdir -p "${BIN_DIR}" "${ES_CFG_DIR}/themes" "${ROMS_DIR}/images" "${SAVES_DIR}"

if [ ! -f "${BIN_DIR}/emulationstation.exe" ]; then
    echo "[*] Downloading standalone batocera-emulationstation (EmulationStation-Win64.zip)..."
    curl -L --fail -o "${HARNESS_DIR}/EmulationStation-Win64.zip" \
        "https://github.com/RetroBat-Official/emulationstation/releases/download/continuous-master/EmulationStation-Win64.zip"
    unzip -q -o "${HARNESS_DIR}/EmulationStation-Win64.zip" -d "${BIN_DIR}"
    rm -f "${HARNESS_DIR}/EmulationStation-Win64.zip"
fi

# Link our n64-classic-mini theme and reference snes-mini theme into the sandbox
mkdir -p "${ROOT_DIR}/themes/n64-classic-mini"
rm -rf "${ES_CFG_DIR}/themes/n64-classic-mini"
cp -r "${ROOT_DIR}/themes/n64-classic-mini" "${ES_CFG_DIR}/themes/n64-classic-mini"

# Create es_systems.cfg with Nintendo 64 configured. Launching runs the console's own launch
# decision (n64mini-launch) inside WSL in dry-run mode: it records what it would run in
# test_harness/live/launches.jsonl instead of starting an emulator or port (AC-T15).
LAUNCHER="${ROOT_DIR}/frontend/.venv/bin/n64mini-launch"
LAUNCH_LOG="${HARNESS_DIR}/launches.jsonl"
cat > "${ES_CFG_DIR}/es_systems.cfg" << EOF
<?xml version="1.0"?>
<systemList>
  <system>
    <name>n64</name>
    <fullname>Nintendo 64</fullname>
    <path>~/roms/n64</path>
    <extension>.z64 .n64 .v64</extension>
    <command>C:\Windows\System32\wsl.exe -d ${WSL_DISTRO_NAME:-Ubuntu} -e ${LAUNCHER} --ports ${HARNESS_DIR}/ports.json --dry-run ${LAUNCH_LOG} -system %SYSTEM% -rom %ROM%</command>
    <emulators>
      <emulator name="libretro"><cores><core default="true">mupen64plus-next</core></cores></emulator>
    </emulators>
    <platform>n64</platform>
    <theme>n64</theme>
  </system>
</systemList>
EOF

# The Windows engine defaults its saves folder to /userdata/saves. With a settings file present in
# its home folder, it derives saves (and other folders) from that file's location instead:
# <file dir>/../saves (Paths::loadCustomConfiguration). The name depends on the build.
: > "${ES_CFG_DIR}/emulatorLauncher.cfg"
: > "${ES_CFG_DIR}/emulationstation.ini"

# Batocera's own save-state rules (AC-T18), pinned to a release tag. GPL data from batocera.linux, used only by
# this local harness and never committed (test_harness/live is git-ignored).
ES_SAVESTATES_REF="batocera-43.1"  # the current release; same file as on master (2026-11-06)
if [ ! -f "${ES_CFG_DIR}/es_savestates.cfg" ]; then
    curl -fsSL -o "${ES_CFG_DIR}/es_savestates.cfg" \
        "https://raw.githubusercontent.com/batocera-linux/batocera.linux/${ES_SAVESTATES_REF}/package/batocera/emulationstation/batocera-emulationstation/es_savestates.cfg"
fi

# Boot straight into the N64 game list (StartupOnGameList, Settings.cpp). The view itself is
# left to the theme's defaultView, so the harness sees what a console with default settings sees.
cat > "${ES_CFG_DIR}/es_settings.cfg" << 'EOF'
<?xml version="1.0"?>
<config>
  <string name="ThemeSet" value="n64-classic-mini" />
  <string name="StartupSystem" value="n64" />
  <bool name="StartupOnGameList" value="true" />
  <bool name="DrawFramerate" value="false" />
  <bool name="EnableHTTPServer" value="true" />
</config>
EOF

# A keyboard mapping: the HTTP server's POST /input looks button names up in the keyboard's
# config (HttpServerThread.cpp), so without one every input is "unknown". SDL keycodes.
cat > "${ES_CFG_DIR}/es_input.cfg" << 'EOF'
<?xml version="1.0"?>
<inputList>
  <inputConfig type="keyboard" deviceName="Keyboard" deviceGUID="-1">
    <input name="a" type="key" id="13" value="1" />
    <input name="b" type="key" id="8" value="1" />
    <input name="x" type="key" id="127" value="1" />
    <input name="y" type="key" id="1073741897" value="1" />
    <input name="start" type="key" id="1073741882" value="1" />
    <input name="select" type="key" id="1073741883" value="1" />
    <input name="up" type="key" id="1073741906" value="1" />
    <input name="down" type="key" id="1073741905" value="1" />
    <input name="left" type="key" id="1073741904" value="1" />
    <input name="right" type="key" id="1073741903" value="1" />
  </inputConfig>
</inputList>
EOF

# The N64 folder is rebuilt each time (n64mini-sandbox). With the owner's git-ignored ROM folder
# present, it mirrors that library by file name only: empty placeholder files, no game data.
# Otherwise it holds the preview's 5 mock games. Every game gets generated placeholder art.
# SANDBOX_ROMS names another folder of ROM file names to mirror (the README screenshots use free
# homebrew titles); SANDBOX_SUSPEND names the game given sample suspend points.
rm -rf "${ROMS_DIR}"
SANDBOX_ROMS="${SANDBOX_ROMS:-${ROOT_DIR}/N64/ROMS}"
if [ -d "${SANDBOX_ROMS}" ]; then
    rm -rf "${SAVES_DIR}" && mkdir -p "${SAVES_DIR}"
    (cd "${ROOT_DIR}/frontend" && uv run n64mini-sandbox "${ROMS_DIR}" --from "${SANDBOX_ROMS}" \
        --saves "${SAVES_DIR}" --suspend-points "${SANDBOX_SUSPEND:-Banjo-Kazooie (U) (!).v64}")
    # Stand-in installed ports: every catalogue game with a port, as if its port were installed,
    # so badges and launch routing can be seen here (AC-T15). Nothing real is ever run.
    (cd "${ROOT_DIR}/frontend" && uv run python -c \
        "import json, sys; from n64mini.console.showcase import load_catalog; json.dump({g.title: ['stand-in-port', '{rom}'] for g in load_catalog() if g.ports}, open(sys.argv[1], 'w'), indent=1)" \
        "${HARNESS_DIR}/ports.json")
    # Badge the flippable games, exactly as the console's own init does.
    (cd "${ROOT_DIR}/frontend" && uv run n64mini-flip --roms "${ROMS_DIR}" --ports "${HARNESS_DIR}/ports.json" init --badges "${ROOT_DIR}/themes/n64-classic-mini/art") >/dev/null
else
    (cd "${ROOT_DIR}/frontend" && uv run n64mini-sandbox "${ROMS_DIR}")
fi

echo "[+] Live Batocera EmulationStation test harness ready at: ${HARNESS_DIR}"
