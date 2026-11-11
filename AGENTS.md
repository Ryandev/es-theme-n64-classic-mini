# Working in this repo (humans and agents)

These rules apply repo-wide. Python specifics live in [`frontend/AGENTS.md`](frontend/AGENTS.md).
Several are adapted from workspace3's `.claude/rules/` (MFIC, fail-loud, fix-as-you-go,
premise-and-frame, debugging). Kept deliberately short: a rule nobody reads is ceremony.

## The gate

```bash
frontend/run check   # what the pre-commit hook and CI run; `frontend/run help` for the rest
```

- **Never pipe a gate into `tail`, `head` or `grep`.** The pipeline's exit code is the last
  command's, so a failed gate reads as green. Write the output to a file, check `$?`, then
  search the file:
  `frontend/run check > /tmp/check.log 2>&1; echo "exit=$?"`
- "Done" means the gate passed on the final state. Re-run it after the last edit, not before.

## Checks must be able to fail (verification independence)

The principle is MFIC (Mechanically-Falsifiable Independent Control). The four properties,
what counts as independence, how this repo applies it and the known gaps are in
[`docs/MFIC.md`](docs/MFIC.md).

Here agents write the code, the tests *and* the reviews, so a wrong assumption gets
repeated in all three and everything stays green. Rank what a check is grounded in,
strongest first, and aim as high as the behaviour allows:

1. **A real system.** Compare against the real thing on the same input, for example the real
   EmulationStation's `es_log.txt` against our validator.
2. **A contract or invariant.** A property the output must satisfy for any input (the
   Hypothesis tests, "adding one bogus child adds exactly one E031").
3. **Self-consistency.** Round-trips and golden snapshots. These prove *unchanged*, not *correct*.
4. **A value the author picked.** Weakest: it encodes the author's assumption, wrong ones included.

- **Red-confirm new tests and gates.** Break the code (or plant the defect), watch the check
  fail, restore it. `frontend/run mutate` does this mechanically for `src/`, and
  `frontend/tests/test_gates.py` does it for the gates themselves.
- **A gate's strength is which files it opens.** File-set gates take their files from git
  (`frontend/run files …`) and abort on an empty set. A gate that checked nothing reports
  exactly what a gate that found nothing reports.
- **Never grade a design against itself.** The first harness scored 99.97% by comparing the
  reference image with crops of the same image. A score is only meaningful against an
  input the producer didn't derive from the thing being scored.

### The MFIC workflow for validator behaviour

Any change to what the validator reports follows this order:

1. **Spec first.** Add or edit the `AC-V…` requirement in
   [`docs/requirements/validator.md`](docs/requirements/validator.md). Ground it in a verbatim
   engine string from `frontend/vendor/`, or mark it `project rule` and say why.
2. **Tests cite the spec.** Add tests and list them on the requirement's `Tests:` line.
   `frontend/tests/test_requirements.py` fails if a rule code has no requirement, a requirement
   has no tests, a cited test doesn't exist, or a cited engine string vanished upstream.
3. **Implement.**
4. **Blind check.** Run the blind approver: a fresh agent following
   [`.claude/agents/blind-approver.md`](.claude/agents/blind-approver.md). It writes black-box
   tests into `frontend/tests/blind/` from the spec and engine source alone.
5. **A human adjudicates disagreements.** A failing blind test is marked
   `xfail(strict=True, reason="BLIND-n: …")` and goes to the human. **Nobody edits a blind test to
   make it pass**, and neither the author nor any agent "fixes" one without that ruling. Once
   ruled on, fix the side that was wrong (validator, spec or test). The strict `xfail` then
   *unexpectedly passes*, which fails the suite until the marker is removed. Remove only the
   marker, and leave a one-line comment recording the ruling.
6. **The contract decides rulings.** A theme that works on the console is valid. The validator
   may be stricter than the engine (a declared `project rule` with an honest message), never
   looser. See the contract in [`docs/requirements/validator.md`](docs/requirements/validator.md).

7. **A blind run only counts against the spec it read.** Every wording change to the
   requirements is a new version. "Re-verified" means a fresh blind run against the *current*
   spec commit, not an earlier run. Name the commit the run used.

A principle is integrated when a gate enforces it, not when a document describes it.

## Safety

- **Never delete files, tests or significant code without the human's approval.** If something
  looks unused (`vulture` flags it), raise it rather than removing it.
- **Never weaken a check without approval.** That covers removing or loosening a lint rule,
  strictness setting, gate stage or ratchet baseline. `tests/test_test_hygiene.py` enforces part
  of it: every skip or `xfail` must give a reason, and every `xfail` must be strict.
- **Never change a test's assertion to match broken behaviour.** Fix the code, or get a ruling.

## Dependencies

A new third-party package must have a permissive licence (MIT, BSD, Apache…, or MPL-2.0). GPL
and AGPL need the human's explicit approval, and they may never be a runtime dependency (anything
in `[project] dependencies` ships). Record each approved exception, with its reason, in
`frontend/pyproject.toml` `[tool.n64mini.licences]`. `tests/test_licences.py` enforces this.
Prefer a maintained library to hand-rolling one, but don't add a framework for a few dozen lines.
For a small library implementing a fixed algorithm, judge it on its open bugs, usage and
correctness. A lack of recent releases means little there.

## Reviewing changes

`/review` (`.claude/commands/review.md`) is a two-pass review, adapted from workspace3:

- **Pass A, blind (can block).** Runs only when the validator or its spec changed. The blind
  approver regenerates `frontend/tests/blind/` from the spec and engine source, never seeing the
  diff. Any failing blind test blocks.
- **Pass B, sighted (advisory).** Five lens agents (`.claude/agents/review-lens-*.md`) read the
  diff in parallel, each looking for one kind of problem. Every finding is verified by the main
  session before it's reported. A finding that doesn't survive checking is dropped.
- **Disagreement goes to the human.** Pass A failing while Pass B is clean is the most valuable
  signal, so it's never resolved automatically.

## Agent conduct

- **The user's design is the plan.** When the human names a design, restate it, then build it.
  Anything you'd add goes under a separate "Also suggest" list, built only on a go-ahead.
- **Label every objection:** *checker-forced* (name the failing lint, type or test), *rule-cited*
  (name the rule), or *taste*. Taste doesn't override the human's design.
- **Before declining a suggestion,** look for the smallest version of it that removes your
  objection.
- **Verify before presenting.** No speculation stated as fact; say what you checked.
- **Comments explain why, not what.** The code says what.

## Fail loud

A fallback (a default, a skip, swallow-and-continue) is legitimate only when **both** hold:
the missing state is **expected** (an optional property left out), **and** there is a
**real value** to use (the engine's documented default). Otherwise raise. A preview or
check that silently drops what it can't handle still produces output and a score, so the
defect reads as green. If in doubt, throw.

## Fix as you go

A bounded gap you find mid-task (a stale doc, a gate that can't see a file, a missing
test) is part of the task. Fix it in the same change. Prefer a **forcing mechanism**
over a one-off cleanup: the ratchets in `frontend/.quality_baseline.json` make every
future change pay its own share. Defer only when the fix is genuinely unbounded, or
needs a decision only the human can make, and say which.

## Before planning non-trivial work, answer four questions

1. **Surfaces:** where does this run (dev machine, CI, the Batocera device)?
2. **Constraints:** which ones am I treating as fixed, and is the reason still true?
3. **Net code:** what does this delete or replace? "Nothing" is fine, but say it.
4. **Scaffolding:** is this a workaround for a missing capability? What would the real
   fix cost?

A question you can't answer goes to the human, not into the plan as an assumption. A
one-line answer ("docs only, no surfaces affected") is complete. Don't pad it.

## Debugging

Reproduce first. State 2–3 ranked hypotheses before searching. Separate symptom from
cause. Before proposing a fix, explain the full chain from root cause to the observed
symptom. If you can't, you haven't found it. Every bug fix gets a regression test that
fails without the fix.

## When something goes wrong, commit the lesson

When a check, a tool or an agent produces a wrong result, find the root cause and add a
rule, test or gate **to this repo** so it can't recur. Repo knowledge goes in the repo
(this file, `frontend/AGENTS.md`, `docs/`), not in one agent's private memory.

Lessons recorded so far:

| Incident | Now prevented by |
|---|---|
| Harness scored 99.97% against crops of its own reference image | "Never grade a design against itself" (above); renderer fails loud |
| The spell checker and shellcheck silently skipped files outside `frontend/` | git-derived file sets + empty-set guard, pinned by `test_gates.py` |
| Shell selector skipped `.githooks/pre-commit` (dot in a *directory* name) | basename matching; `test_gates.py` lists required files |
| A ratchet test overwrote the real baseline (default argument bound at import time) | `--baseline` flag; tests use a `tmp_path` baseline |
| Float edge: `59.9 - 60` failed a 0.1 tolerance | delta rounded before comparing; pinned by `test_ratchet.py` |
| A gate run piped into `tail` hid its exit code | "Never pipe a gate" (above) |
| The validator (and the first blind run) treated Python's number syntax (`1e3`, `inf`) and stripped whitespace as valid. The engine reads both as 0, so the validator was *looser* than the engine | AC-V11 number grammar copied from the engine's parser; raw text checked untrimmed; blind approver told to derive grammars from the C++ |
| Engine variables were a hand-typed prefix list (`theme.`, `lang`…), looser than the engine | Extracted from `ThemeData.cpp`/`SystemData.cpp` into the grammar, gated by `test_grammar.py` |
| The licence gate passed `shellcheck-py` (MIT wrapper) while the `shellcheck` program it bundles is GPL-3.0 | The gate now detects bundled native programs and requires each one's licence in `[tool.n64mini.licences.bundled]` |
| `timeout` on the Windows EmulationStation only stopped the WSL side; three engine processes were left running on the desktop | `frontend/run engine` starts it via PowerShell, then stops that exact Windows PID (also on exit) |
| Raw `es_log.txt` reads cost thousands of lines of font noise per run | `frontend/tools/es_log.py` reduces the log to theme findings mapped to validator codes |
| The validator only checked the target system, but the engine also loads the theme for 14 built-in collections; the real engine's log was the first check to catch it | AC-V22: collections extracted from `CollectionSystemManager.cpp` and checked; the theme uses `./$system/` with a `default/` fallback; `./run engine` reports 0 findings |
| Asked to integrate MFIC, the agent wrote a document about it instead | The MFIC workflow (above): requirements gate + blind approver; "a principle is integrated when a gate enforces it" |
| The preview placed carousel cards in fixed slots copied from the mock-up, so it agreed with the design while the engine would have ignored the tile sizes, tinted the frames `AAAAEE` and zeroed a normalised tile padding (`Vector4f::operator*=` is a no-op upstream) | The preview's carousel is ported from the vendored `ImageGridComponent`/`GridTileComponent`, with each quirk pinned by a red-confirmed test (`test_carousel.py`) and the XML tied to the frame generator (`test_n64_theme.py`) |
| Issues on a multi-line start tag pointed at its closing `>`: lxml's `sourceline` is where a start tag *ends*, and every check inherited it (BLIND-32) | Lines come from the source's own start tags, paired with the parsed elements and failing loud if they don't line up; pinned by `test_issues_point_at_the_line_where_the_start_tag_begins` |
| The engine sandbox looked fine while hiding two gaps. Its box art was copied from a folder that no longer existed, skipped silently, so the engine showed its generic cartridge. A setting the engine never reads (`n64.GamelistViewStyle`) stood in for the theme's missing `defaultView`, so the real console might never have opened the carousel | The setup writes the preview's own placeholder art and fails if an image is missing. The theme declares `defaultView="grid"` (pinned by `test_theme_opens_game_lists_in_the_carousel`). The sandbox sets only settings found in the vendored engine source. `./run shot` checks the result on the real engine |
| An engine screenshot captured the owner's terminal instead: the tool copied the screen where the engine window was, and another window lay on top | `n64mini-capture-live` takes the engine's PID and has Windows render that window (`PrintWindow`); `check_capture` rejects a wrong-size or blank image, pinned by `test_capture_check_rejects_the_wrong_window_or_a_blank` |
| The owner's ROM folder (`N64/`, copyrighted games) sat in the working tree un-ignored: one `git add -A` from being committed, and the file-set gates were scanning it | `N64/` is git-ignored, and `test_no_game_rom_can_be_committed` fails if any `.z64/.n64/.v64` is among the files a commit could include (red-confirmed by un-ignoring it). The sandbox mirrors ROMs by name only, as empty files |
| The Batocera VM's engine crash-looped through every early VM test (no OpenGL there) and nothing said so: the only sign was 160% CPU, until setup reported the engine never answered | The VM's display uses virgl on WSL's render node, and `frontend/run vm start` fails unless the engine is running and answering its HTTP API |
| Setup wrote the engine's settings, then quit the engine to reload them; the engine wrote its old settings back as it quit. The agent had concluded it doesn't save on exit from a search for `saveFile`, missing `ViewController::saveState`. Only the real engine in the VM showed it | Start-up settings are set while the engine is suspended (`bundle/update.sh`), pinned by `test_start_up_settings_are_set_while_the_engine_is_suspended` against the vendored `main.cpp`/`ViewController.cpp` and Batocera's scripts; the VM install is checked by screenshot |
| The Showcase/Full card's toggle, started from the card, hung the engine's launch screen: the toggle (waiting for the launch to end) kept the launch's output pipe open, which is what the engine waits on | The toggle detaches and lets go of the pipe (`detach_output`), pinned by `test_detaching_lets_go_of_the_launch_pipe`, which runs it in a real subprocess |
| After the toggle rewrote the gamelist, the engine wrote its stale copy of the just-launched card back over it: an entry changed by a launch is saved from the engine's memory | The card's new face is set in the engine's memory through its API before the reload (`test_the_card_gets_its_new_face_in_the_engine_before_the_reload`); both were caught only by the VM run |
| A test USB stick made as a bare FAT filesystem was silently ignored by Batocera's storage manager (it only mounts partitions), and FAT makes every file look executable | The stick is built with a partition table; each bundled component declares its executable files and setup sets modes itself (`test_installed_modes_come_from_the_manifest_not_the_stick`) |
| The docs test found Markdown with `rglob`, so it scanned git-ignored trees too and failed on a Torch build's vendored docs (`test_harness/build/`) | `test_docs.py` takes its files from git like the other file-set gates, and asserts that ignored trees are absent (red-confirmed with a planted file) |
| The coverage ratchet failed on one run and passed on the next with the same code: a branch (restoring `<gameList/>`) was only reached when Hypothesis happened to draw an empty list | A fixed-input test for that branch, and a rule in `frontend/AGENTS.md`: every branch gets a fixed-input test; property tests add breadth, not coverage |
| A `<storyboard>` in `gamesplash.xml` froze at `t=0` (leaving `launch_splash.png` stuck at card size) and `flip_expand_frames()` in `launch.py` wasn't wired into `n64mini-launch.sh`: `Splash` in `Splash.cpp` is not a `GuiComponent` and has no `update(int deltaTime)` method | `test_gamesplash_shows_the_boot_frame_on_black_without_game_screenshot` asserts against `Splash.cpp` that `gamesplash.xml` has no `<storyboard>`, and `n64mini-launch.sh` / `launch.py` (`--animate`) runs the SDL2 card-flip, cubic ease-out expansion and cross-fade animation on launch |
| Initial suspend-slot launch passed `-state_slot <0..3> -state_file <path>`, which Batocera 43.1's `emulatorlauncher.py` rejects (`-state_filename`) and `mupenConfig.py` would have used to overwrite `.st<slot+1>` on the next Reset | Verified on the live 43.1 VM: `launch.consume_resume_args` passes `-state_slot 8 -state_filename <path>` (`--savestate` in `mupenGenerator.py`), pinned by `test_consume_resume_args_and_decide_resume_slot` |
| A front-pod dome built with `cq.Solid.makeTorus(6.0, 6.5)` (`r_minor > r_major`, a self-intersecting spindle torus) passed `BRepAlgoAPI_Check` while producing a dimpled ring and broken Coin3D triangles in FreeCAD | `cad/lib/printable.py` `check_manifold` rejects any `GeomAbs_Torus` face with `MinorRadius > MajorRadius` (red-confirmed), `SH_OUTSIDE` in `console_check.py` checks front-pod dome convexity against `n64_shell.py`, and `part/upper_shell.py` uses a non-degenerate sphere (`makeSphere`) |
| Deleting a tracked file (before staging the deletion) failed the spell gate: the git-derived file set still listed the missing path | `candidates()` lists only files that exist, pinned by `test_a_deleted_tracked_file_is_not_in_the_file_sets` (red-confirmed) |
| Three ways to lose a suspend point passed every gate, one with a test asserting the bug (EOF → `["b"]`): a closed drawer input pressed B (deleting the Reset state), boot deleted every unfiled `.pending` (and `.tmp` files that can be moved slots), and Reset staged the stale `.st9` left by a resume before mupen flushed the new one | The poller reports `INPUT_CLOSED`, which closes without acting; boot deletes no save files (the real script is run with `rm` recorded); staging takes only states written since the press (`--since-ns`). Each red-confirmed: `test_closed_input_closes_the_drawer_without_deleting_the_reset_suspend_point`, `test_booting_deletes_no_save_files`, `test_a_stale_resume_copy_is_not_staged_as_the_new_suspend_point` |
| The Reset button never worked through hotkeygen, though AC-T18's status said it was checked on the VM: hotkeygen runs the command with `os.system` and reads no keys until it returns, so the script's own `hotkeygen --send save_state`/`exit` queued behind it, nothing was saved and `open` waited forever. It was only ever run by hand. Separately, mupen saved into whichever slot it last used, a player's `.st0..3` included | Found by pressing the hotkey on the VM. The script detaches (`test_the_reset_command_returns_before_it_sends_keys_through_hotkeygen`, whose fake hotkeygen blocks like the real one); every emulator launch pins `-state_slot 9` and setup turns `n64.incrementalsavestates` off (`test_an_unflipped_game_goes_to_emulatorlauncher_with_saves_pinned_to_slot_9`). A status that says "verified on the VM" names the input path used |
| In the private workspace (theme as a git submodule), the pre-commit gate failed 8 tests that pass outside the hook: git runs hooks with `GIT_DIR`/`GIT_INDEX_FILE` pointing into `.git/modules/`, and the gate's git calls and the tests' throwaway repos inherited them | `.githooks/pre-commit` unsets the hook's git variables before running the gate; checked by committing from the submodule |

## Git

- `git config core.hooksPath .githooks` once per clone.
- Commit messages: `type(scope): description`, for example `fix(validator): …` or
  `feat(theme): …` or `docs(mfic): …`. Types: feat, fix, docs, test, refactor, chore, ci. The
  body says *why*, and names any ruling (BLIND-n) or requirement (AC-V…) involved.
- Commit or push only when asked. Never `--no-verify` without saying so;
  `N64MINI_SKIP_GATE=1` exists for WIP commits and must be named in the message.
- `docs/references/` is local-only research material (git-ignored). Code must never depend on it.
