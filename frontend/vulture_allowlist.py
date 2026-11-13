# Names vulture reports as unused that are used in ways it can't see. Read by
# `./run lint`. Excluded from ruff, since bare names are its format. Every entry
# needs a reason, and the list should only shrink.

similarity_pct  # TypedDict field, read by key (metrics.py Score)
within_tol_pct  # TypedDict field, read by key (metrics.py Score)
coverage  # TypedDict field, read by key (ratchet.py Baseline)
mutation  # TypedDict field, read by key (ratchet.py Baseline)
files  # TypedDict field, read by key (ratchet.py _CoverageJson)
summary  # TypedDict field, read by key (ratchet.py _FileEntry)
covered_lines  # TypedDict field, read by key (ratchet.py _FileSummary)
num_statements  # TypedDict field, read by key (ratchet.py _FileSummary)
covered_branches  # TypedDict field, read by key (ratchet.py _FileSummary)
num_branches  # TypedDict field, read by key (ratchet.py _FileSummary)
_CoverageJson  # used inside a cast("...") string
_MutmutStats  # used inside a cast("...") string
pytestmark  # pytest reads this module-level name (test_gates.py)
_Licences  # used inside a cast("...") string (test_licences.py)
allowed  # TypedDict field, read by key (test_licences.py _Licences)
exceptions  # TypedDict field, read by key (test_licences.py _Licences)
bundled  # TypedDict field, read by key (test_licences.py _Licences)
empty_root  # TypedDict field, read by key (console/showcase.py _State)
StartElementHandler  # expat callback, called from C (console/showcase.py _check_gamelist)
_no_leftover_processes  # autouse session fixture, run by pytest (tests/conftest.py)
