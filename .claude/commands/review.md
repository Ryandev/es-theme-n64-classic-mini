---
description: Two-pass MFIC review of the current change. Pass A, a blind approver (blocks), runs if the validator or its spec changed. Pass B is five sighted lenses (advisory), each finding verified before it's reported. Optional argument is the git base ref.
---

<!--
  Adapted from workspace3's /review-next-pr and rules/mfic-two-pass-review.md, cut down for a
  one-person GitHub repo: no Bitbucket, Jira or cron, and 5 lenses instead of 30. This is a
  slash command, not a subagent, because subagents can't launch subagents, so the fan-out has to
  happen in the main session.
-->

Your output is a **review report**, nothing else. Don't fix anything you find. Don't commit.
Don't widen the scope. On any unexpected script failure, print its stderr and stop.

## Step 1: Context (deterministic)

```bash
cd frontend && uv run python tools/review_context.py ${ARGUMENTS:+--base "$ARGUMENTS"}
```

- **exit 2:** nothing to review. Print its stderr line and stop.
- **exit 1:** print the stderr and stop.
- **exit 0:** stdout is JSON. Bind `CTX` (`ctx`), `NEEDS_BLIND` (`needs_blind_pass`) and note
  `base`, `head` and `files` for the report.

## Step 2: Pass A, blind (only if `NEEDS_BLIND`; can block)

Spawn **one** fresh agent (`subagent_type: "blind-approver"` if it's listed, otherwise
`general-purpose`). Tell it to:

- read and follow `.claude/agents/blind-approver.md` from disk;
- delete `frontend/tests/blind/test_blind_validator.py` **unread** and write a new one against
  the current `docs/requirements/validator.md`, numbering new disagreements after the highest
  BLIND-n already recorded in `docs/requirements/validator.md` and `AGENTS.md`;
- return its report.

**Don't give it `CTX`, the diff, the commit messages or your own view of the change.** Its value
is that it hasn't seen them.

Then run, writing to a file and checking the exit code (never pipe into `tail`):

```bash
cd frontend && uv run pytest tests/blind -q -rxX > "$CTX/pass-a.log" 2>&1; echo "exit=$?"
```

**Pass A verdict:** `BLOCK` if any blind test fails or any strict `xfail` (BLIND-n) is open,
otherwise `PASS`. Skipped (no validator change) is recorded as `NOT RUN`.

## Step 3: Pass B, sighted (advisory)

In **one message**, spawn the five lenses in parallel. Use `subagent_type: "review-lens-<name>"`
if it's listed, otherwise `general-purpose` told to read and follow
`.claude/agents/review-lens-<name>.md` from disk. Give each one only `CTX` and its lens name:

`correctness`, `test-quality`, `engine-contract`, `docs-rules`, `simplicity`

Each writes `CTX/findings-<name>.md`.

## Step 4: Verify every finding (main session)

For each finding, open the cited `path:line` and check the claim yourself. Where it's cheap,
prove it: run the validator on a tiny theme, or run the named test. Mark each one:

- **VERIFIED:** you reproduced the failure or confirmed it in the code.
- **DROPPED:** it didn't survive checking. Give the one-line reason.

Merge duplicates that share a root cause. A lens finding is never reported unverified.

## Step 5: Report

Write `CTX/report.md`:

```markdown
# Review <head> (base <base>)

**Pass A (blind):** PASS | BLOCK | NOT RUN, with counts and the open BLIND-n list
**Pass B (sighted):** N verified findings (B blocker, M major, m minor), D dropped

## Disagreements (for the human)
<Pass A BLOCK while Pass B found nothing, or a lens contradicting a blind test.
These are the highest-value signals. Never resolve them yourself.>

## Findings
<verified findings, most severe first, each with its lens, path:line, failure and evidence>

## Dropped
<each dropped finding, one line: lens, claim, why it didn't survive>
```

Then print the report to the user, followed by one line: `Report: <CTX>/report.md`.

Posting to a GitHub PR (`gh pr comment`) is outward-facing, so ask first. Never post
automatically.

## Rules

- A human adjudicates every disagreement and every BLIND-n. Nothing is auto-resolved.
- Don't re-derive or soften a lens's verdict to make the report look cleaner. Verify it or
  drop it, and say why.
- Re-running is cheap. If the change moves while you review, start again from Step 1.
