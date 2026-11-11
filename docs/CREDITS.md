# Credits & Media References

External media, fonts, and upstream projects used or referenced by the **N64 Classic Mini** console build.

---

## 1. Startup Boot Video

| Asset | Title & Creator | Source | Console Path |
|---|---|---|---|
| `local/splash.mp4` (optional, supplied by you) | ***Nintendo 64 Startup Remake*** by **Meurotte** (1920x1080 @ 24 fps, H.264 + AAC) | <https://www.youtube.com/watch?v=D9aFp7SzSVc> | `/userdata/splash/splash.mp4` |

- Installed by the `splash` component in [`bundle/n64mini.json`](../bundle/n64mini.json) (`frontend/run bundle` substitutes `local/splash.mp4` when present, per `DEC-5`).
- Played by `/etc/init.d/S28splash` (`mpv`) and held to completion by `/etc/init.d/S30splashscreencontrol` (`splash_autoplay`), with all Batocera boot branding, framebuffer logos (`/usr/share/batocera/splash/boot-logo*.png`), version subtitles (`splash.screen.subtitle=0`), and EmulationStation loading screens (`es.customsargs=--no-splash` and [`themes/n64-classic-mini/splash.xml`](../themes/n64-classic-mini/splash.xml)) disabled (`AC-S09`).

---

## 2. Bundled Fonts (`themes/n64-classic-mini/fonts/`)

| Font | Role | Licence |
|---|---|---|
| `DejaVuSans-Bold.ttf` | Title banner (`md_name`, matching the sans-serif Classic Mini banner typeface) and menu titles (`AC-T07`) | [`LICENSE-DejaVu.txt`](../themes/n64-classic-mini/fonts/LICENSE-DejaVu.txt) (Bitstream Vera / Arev permissive) |
| `DejaVuSans.ttf` | Help system prompts, system game count, and body UI text (`AC-T03`) | [`LICENSE-DejaVu.txt`](../themes/n64-classic-mini/fonts/LICENSE-DejaVu.txt) (Bitstream Vera / Arev permissive) |
| `DejaVuSerif-Bold.ttf` | Display accents (`AC-T03`) | [`LICENSE-DejaVu.txt`](../themes/n64-classic-mini/fonts/LICENSE-DejaVu.txt) (Bitstream Vera / Arev permissive) |

---

## 3. Native N64 PC Ports & Tools

See [`docs/ports.md`](ports.md) for the full port matrix, pinned release URLs, SHA-256 checksums, and ROM SHA-1 digests:

- **Snowboard Kids 2: Recompiled** (`cdlewis/snowboardkids2-recomp`)
- **SpaghettiKart — Mario Kart 64** (`HarbourMasters/SpaghettiKart` + `HarbourMasters/Torch`)
- **Banjo: Recompiled — Banjo-Kazooie** (`BanjoRecomp/BanjoRecomp`)
- **Bomberman 64: Recompiled** (`RevoSucks/BM64Recomp`)
- **Zelda 64: Recompiled — Majora's Mask** (`Zelda64Recomp/Zelda64Recomp`)
- **Donkey Kong 64: Recompiled** (`Rainchus/Donkey-Kong-64-Recompiled`)
- **Star Fox 64: Recompiled** (`sonicdcer/Starfox64Recomp`)
- **N64Recomp & RT64** (`N64Recomp/N64Recomp`, `rt64/rt64`)
