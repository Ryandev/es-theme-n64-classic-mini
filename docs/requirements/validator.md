# Requirements: theme validator (`n64mini-validate-theme`)

The repo-resident specification for what the validator must report. It serves three
consumers (see [`../MFIC.md`](../MFIC.md)):

- **Tests** cite these `AC-V…` ids, and `frontend/tests/test_requirements.py` gates the links.
- **The blind approver** ([`.claude/agents/blind-approver.md`](../../.claude/agents/blind-approver.md))
  derives black-box tests from *this file and the vendored engine source only*, never from
  the implementation.
- **The engine oracle** (planned): the ACs grounded in an engine log line are the ones the
  real EmulationStation's `es_log.txt` can confirm or refute.

## The contract: stricter than the engine is allowed, looser never is

**A theme is valid if it works on the console.** The engine (Batocera's EmulationStation,
vendored in `frontend/vendor/`) defines what "works" means.

- **Never looser.** The validator must never pass a theme the engine would reject, warn about
  or silently drop part of. Every engine warning must have a matching validator issue. This
  direction is a bug whenever it happens.
- **Stricter is allowed, but only on purpose.** The validator may reject some themes the engine
  accepts, as house rules. Each such rule is grounded as `project rule` with its reason, and its
  message must say what the engine actually does, not claim something false. (For example,
  "not found" for a file the engine *would* find is a bug, not strictness.)
- **Accidental strictness is a bug.** A false alarm on a theme that works and isn't covered by
  a declared project rule is fixed in the validator, not argued in the spec.

- **What can't be checked statically is reported, not skipped.** Anything that depends on
  the console at runtime gets W005 (AC-V17). Per-game `{x:y}` bindings in the types that support
  them are the one deliberate exception: they're the engine's normal mechanism, so warning on
  every one would bury real problems.

**How the engine reads XML text** (pugixml defaults, which apply everywhere below):
whitespace-only text is dropped, so `<x>   </x>` is the same as `<x></x>`. An element's text is its
**first** non-whitespace text or CDATA node only, so a value split by a comment or a CDATA
section is whatever comes before the split (and a value that *starts* with a CDATA section is
that section's content). Non-whitespace text in a place that expects elements (directly under `<theme>`, a
view, an element, a storyboard, a `<subset>` or a `<feature>`) is a node with an empty name,
which the engine warns about or drops. It's reported with the code for that location.

The blind approver tests against this contract. The planned engine oracle checks the "never
looser" direction mechanically: for any theme the validator passes, the engine's `es_log.txt`
must contain no theme warnings.

## Format (machine-checked, keep it exact)

Each requirement is a `### AC-V<nn>: <title> (<CODE>)` heading followed by:

- `**Grounding:**` either `engine` with a file and a **verbatim** string from
  `frontend/vendor/batocera-emulationstation/<file>` (the gate checks it still exists, so an
  upstream change that removes the behaviour turns the gate red), or `project rule` with the reason.
  The engine logs a warning and *drops* the node for most of these. We report them as errors
  because a dropped node is always a theme bug.
- `**Tests:**` one or more `file::test_function` references under `frontend/tests/`.

Inputs are a `theme.xml` plus whatever it includes. "Reports" means the issue appears with the
given code, the file and (where an element exists) its line number. An element's line is the
line where its start tag begins (the `<`), even when the tag spans several lines (BLIND-32).

---

### AC-V01: Malformed XML is reported (E001)

A file that is not well-formed XML is reported, with the parser's line number. Nothing else is
checked in that file. This is stricter than pugixml, which tolerates some malformed input (an
unknown entity such as `&nbsp;` is left in place). That's a declared project rule: such a
theme depends on parser leniency (run 4, ambiguity 6).

- **Grounding:** engine `ThemeData.cpp`: `XML parsing error`
- **Grounding:** project rule. Well-formed XML only, even where pugixml is lenient.
- **Tests:** `test_validate.py::test_each_rule_fires_alone`

### AC-V02: The root element must be `<theme>` (E002)

- **Grounding:** engine `ThemeData.cpp`: `Missing <theme> tag!`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`

### AC-V03: The entry file declares a usable `<formatVersion>` (E003)

The entry `theme.xml` must contain `<formatVersion>`, its value must be a number, and it must
be at least the engine minimum (3). Included files may omit it. Fractional versions are allowed. The engine
parses it with `strtod`, which accepts more (`1e1`, `6abc`). The validator accepts plain decimals
only (`7`, `6.5`), not `1e1` or `nan`, a declared project rule (ruling on BLIND-16).
**E003 is fatal on the console:** the engine refuses the whole theme. The validator still runs
the other checks, for diagnostics.

- **Grounding:** engine `ThemeData.cpp`: `<formatVersion> tag missing!`
- **Grounding:** engine `ThemeData.cpp`: `throw error << "Theme uses format version`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_included_files_may_omit_format_version`, `test_validate.py::test_fractional_format_version_is_accepted`, `test_validate.py::test_format_version_is_a_plain_decimal`

### AC-V04: Only known top-level elements under `<theme>` (E004)

Valid children are `formatVersion`, `variables`, `include`, `view`, `customView`, `subset` and
`feature`. In format 7 the engine walks these in document order and silently skips anything else.
With `formatVersion` 6 or lower it only reads `include`, `view`, `customView` and `feature`, so a
`<subset>` there is silently ignored and also reported. Inside `<feature>`, only `<view>` children
are read; anything else is reported too.

Non-whitespace text directly under `<theme>`, and any child of `<subset>` other than `<include>`,
is silently ignored by the engine, so it's reported (rulings on BLIND-17 and BLIND-18).

- **Grounding:** engine `ThemeData.cpp`: `name == "include"`
- **Grounding:** engine `ThemeData.cpp`: `if (mVersion <= 6)`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_subset_is_ignored_before_format_7`, `test_validate.py::test_feature_only_reads_views`, `test_validate.py::test_stray_text_and_subset_children_are_reported`

### AC-V05: Every include resolves to a file (E010)

`<include>` paths resolve relative to the including file, after `${variable}` substitution
(`${system.theme}` is the system being validated). Includes inside `<subset>` are checked too.
The text is used **exactly as written**: the engine doesn't trim it, so surrounding whitespace
or a newline makes the file "not found". An empty or whitespace-only include is skipped,
because pugixml drops whitespace-only text. A path that depends on the system is covered by
AC-V22. The path
must start with `./` (AC-V21). An include whose path depends on a variable only known on the
console is **not** skipped silently: see AC-V17.

- **Grounding:** engine `ThemeData.cpp`: `not found! (resolved to`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_includes_resolve_system_variable_and_share_variables`, `test_validate.py::test_padded_include_path_is_not_found`

### AC-V06: An include cycle is reported once and does not recurse forever (E011)

- **Grounding:** project rule. The engine has no cycle guard; a cycle would recurse until it crashed.
- **Tests:** `test_validate.py::test_include_cycle_is_reported_once`

### AC-V07: Views and their direct elements need a non-empty `name` (E020)

Applies to `<view>`, `<customView>` and every element that is a **direct child** of a view.
Elements nested inside another element may omit `name`; the engine then keys them by tag.
An element with no name is skipped by the engine before its type is looked at, so it reports
E020 **only**, never also E030.

A `name` attribute that is present but empty, or made only of separators (`" , "`), is also
E020. The engine silently ignores such a
view; reporting it is a project rule, and the message says "empty", not "missing".

Non-whitespace text directly inside a view is a nameless node: the engine logs
`Element of type "" missing "name" attribute!`, so it's E020 too (ruling on BLIND-19).

- **Grounding:** engine `ThemeData.cpp`: `View missing \"name\" attribute!`
- **Grounding:** engine `ThemeData.cpp`: `missing \"name\" attribute!`
- **Grounding:** project rule. An empty view name is always a mistake, and the engine gives no signal.
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_nested_elements_may_omit_name`, `test_validate.py::test_nameless_unknown_element_reports_only_e020`, `test_validate.py::test_empty_view_name_says_empty`, `test_validate.py::test_stray_text_and_subset_children_are_reported`

### AC-V08: `<view name>` lists only supported views (E021)

The name attribute may list several views separated by commas or whitespace. Each must be one of
the engine's supported views. A `<customView>` name is **not** usable in `<view name>`: the engine
ignores it there. Views inside `<feature>` are checked too.

`<customView inherits="x">` must name a view or custom view defined earlier. Otherwise the engine
silently ignores the inheritance (ruling on BLIND ambiguity, run 3). "Earlier" follows the
engine's order: document order in format 7. With `formatVersion` 6 or lower the engine reads
every `<view>` in a file before any `<customView>`, so a view later in the same file counts
(ruling on BLIND-27).

- **Grounding:** engine `ThemeData.cpp`: `sSupportedViews.find(viewKey)`
- **Grounding:** engine `ThemeData.cpp`: `if (baseviewit == mViews.cend())`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_view_cannot_target_a_custom_view_name`, `test_validate.py::test_custom_view_must_inherit_a_known_view`, `test_validate.py::test_format_6_reads_views_before_custom_views`

### AC-V09: Elements are known element types (E030)

- **Grounding:** engine `ThemeData.cpp`: `Unknown element of type`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_nested_structures_follow_engine_rules`

### AC-V10: Child properties are known for their element (E031)

A child of an element must be a property of that element type, looked up through its base class
(`clock` inherits `text`, for example), or one of the engine's special children: `storyboard`,
`itemTemplate` (on grid/carousel/textlist types), shader children, any child of `menuIcons`,
`animate` on `imagegrid`, or a nested element of a known type. `<text>` inside `<text>` is a
property unless its **first** child is an element: the engine looks only at the first child.
So `<text>hello<pos/></text>` is a property whose `<pos>` is silently dropped, and that's
reported as E031 (ruling on BLIND-11). `menuShader` and `fadeShader` are special children only of
`menuBackground`; elsewhere they're unknown properties (BLIND-20). Non-whitespace text
directly inside an element is a nameless child, which the engine reports as
`Unknown property : <element>.` (BLIND-21).

- **Grounding:** engine `ThemeData.cpp`: `Unknown property : `
- **Grounding:** engine `ThemeData.cpp`: `findPropertyFromBaseClass(root.name(), name, type)`
- **Grounding:** engine `ThemeData.cpp`: `if (node.first_child() && !std::string(node.first_child().name()).empty())`
- **Grounding:** engine `ThemeData.cpp`: `(name == "menuShader" || name == "fadeShader") && std::string(root.name()) == "menuBackground"`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_nested_structures_follow_engine_rules`, `test_properties.py::test_adding_one_unknown_property_adds_exactly_one_e031`, `test_validate.py::test_text_in_text_follows_the_first_child`, `test_validate.py::test_menu_shaders_only_under_menu_background`, `test_validate.py::test_stray_text_and_subset_children_are_reported`

### AC-V11: Property values parse as their declared type, as the engine parses them (E040)

The engine parses typed values with its own hand-written parsers, which are much narrower than
"a number" in most languages. **Anything they can't parse silently becomes 0 or false, with no
warning**, so everything outside the grammar below is an error. The raw text is checked
exactly as written: the engine does not trim XML text.

**Number:** an optional `+`/`-`, then digits with at most one `.`, and at least one digit
(`5`, `-0.5`, `.5`, `5.`). Not allowed: exponents (`1e3`), `inf`, `nan`, commas (`0.5,0.5`),
tabs, newlines, any other character. (The engine reads all of those as 0.)

| Type | Valid text |
|---|---|
| FLOAT | exactly one number |
| NORMALIZED_PAIR | one or two numbers separated by spaces (one applies to both axes) |
| NORMALIZED_RECT | one, two or four numbers separated by spaces |

Number lists have **no leading or trailing whitespace**. Some positions silently zero a value:
the pair parser splits at the *first* space, so `" 0.5 0.5"` reads X as 0, and `"0.5 "` reads Y
as 0. Spaces between numbers are fine.
| BOOLEAN | exactly `true`, `false`, `1`, `0`, `yes` or `no` (any case), no surrounding whitespace. The engine only reads the first character, so `" true"` is false. |
| COLOR | exactly `RRGGBB`, `RRGGBBAA`, `#RRGGBB`, `#RRGGBBAA`, a lower-case HTML colour name, `transparent`, or empty, with no surrounding whitespace |
| STRING | anything |

An **empty** FLOAT, pair, rect or boolean is an error too. That's a project rule: the engine
reads it as 0 or false without complaint, but an empty typed value is almost always a mistake.
A FLOAT with more than one number, or a pair with three, is also an error by project rule:
the engine silently uses the first value(s).

A `{x:y}` runtime binding (for example `{game:image}`) is per-game data and isn't type-checked
in the types where the engine supports bindings: STRING, FLOAT, COLOR, BOOLEAN and PATH. A
NORMALIZED_PAIR or NORMALIZED_RECT has no binding support (the engine parses the braces as
numbers and gets 0), so a binding there is an error. (Ruling on BLIND-3, 2026-11-05.)

Some inputs the engine tolerates are rejected anyway, as declared project rules: spaces around
a FLOAT or RECT, booleans the engine reads by first letter only (`y`, `tru`), an empty typed
value, and extra numbers. A value that depends on a runtime variable is covered by AC-V17.
Attributes that name a property (`<text pos="0 0">`) are checked the same way.

A value split by a comment or CDATA section is checked as the engine reads it: only the first
non-whitespace text or CDATA node, so `0.5<![CDATA[ 0.7]]>` is checked as `0.5` (rulings on
BLIND-22 and BLIND-28). A whitespace-only value counts as empty.

- **Grounding:** engine `StringUtil.cpp`: `if (*str < '0' || *str > '9')`
- **Grounding:** engine `StringUtil.cpp`: `char first = string[0];`
- **Grounding:** engine `Vector2f.cpp`: `size_t divider = _input.find(' ');`
- **Grounding:** engine `Vector4f.cpp`: `while (*str == ' ')`
- **Grounding:** engine `ThemeData.cpp`: `Utils::HtmlColor::parse(str)`
- **Grounding:** engine `ThemeData.cpp`: `case NORMALIZED_RECT:`
- **Grounding:** project rule. Tolerated whitespace, first-letter booleans, empty typed values and extra numbers are rejected although the engine accepts them.
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_values_the_engine_accepts`, `test_validate.py::test_numbers_the_engine_reads_as_zero_are_errors`, `test_validate.py::test_whitespace_the_engine_does_not_trim_is_an_error`, `test_properties.py::test_any_engine_number_pair_is_a_valid_pos`, `test_properties.py::test_any_bare_hex_colour_is_valid`, `test_properties.py::test_non_engine_number_syntax_is_rejected`, `test_validate.py::test_binding_in_pair_or_rect_is_an_error`, `test_validate.py::test_split_values_are_read_like_the_engine`

### AC-V12: Relative asset paths exist (E041)

A relative PATH property must point at an existing file, resolved the way the engine resolves it:

1. relative to the directory of the file the property is written in;
2. **inside an included file only**, if that fails and the value starts with `.`, relative to the
   entry `theme.xml`'s directory (the engine's fallback to `mPaths.front()`).

It must be a **file**: a directory doesn't count (run 4, ambiguity 9). The text form of a shader
(`<shader>./crt.glsl</shader>`) is a PATH too, and is checked the same way. It's reported only
if neither resolves. The message is
`<prop>='<value as written>' not found (<path tried> [or <path tried>])`. `none`, empty values
and `{x:y}` runtime bindings are allowed. `{random…}` is allowed only where the engine supports
it (AC-V18). A path built from `${themePath}`, `${currentPath}` or `${root}` is inside the theme,
so it's checked like a relative path, not warned about as absolute. (Ruling on BLIND-1,
2026-11-05: the validator previously ignored step 2 and reported working themes.)

- **Grounding:** engine `ThemeData.cpp`: `(str[0] == '.' || str[0] == '~') && mPaths.size() > 1`
- **Grounding:** project rule. Reporting the missing file at all is stricter than the engine, which loads silently and draws nothing, so a missing asset is invisible until someone looks.
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_values_the_engine_accepts`, `test_validate.py::test_include_asset_falls_back_to_theme_root`, `test_validate.py::test_theme_path_variables_are_checked_not_warned`, `test_validate.py::test_asset_path_must_be_a_file`

### AC-V13: `#RRGGBB` colours are warned about (W001)

`HtmlColor::parse` only adds the opaque alpha byte when the string length is 6, so `#RRGGBB`
(length 7) comes out with alpha 0. Bare `RRGGBB` and `#RRGGBBAA` are fine.

- **Grounding:** engine `HtmlColor.cpp`: `if (len == 6)`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_ok_respects_warnings_as_errors`

### AC-V14: Absolute asset paths are warned about (W002)

- **Grounding:** project rule. A path outside the theme depends on the device and can't be checked here.
- **Tests:** `test_validate.py::test_each_rule_fires_alone`

### AC-V15: Undefined `${variables}` are warned about (W003)

A variable is defined by either form inside a `<variables>` block. Includes share one scope in
document order: a variable defined in an included file is available only after that
`<include>`. A file's own `<variables>` blocks are read **before** its views, so a block placed
after a view in the same file still applies to it:

- `<name>value</name>`
- `<variable name="name" value="value"/>` (the engine's attribute form; ruling on BLIND-2, 2026-11-05)

`${themePath}` and `${root}` are the entry `theme.xml`'s folder. `${currentPath}` is the folder
of the file **being read**: the engine resets it for each include (ruling on BLIND-13 and
BLIND-14). All three are resolved statically. `settings.*` only covers setting names without a
dot, because the engine skips any setting whose name contains one (BLIND-15).

The engine also defines its own variables. The set is **extracted from the vendored engine
source** into the grammar (`engine_variables`, `engine_variable_prefixes`), never typed by hand:
exact names such as `lang`, `themePath`, `screen.ratio`, `global.help` and `system.fullName`,
plus the open families `settings.*` and `subset.*`. Anything else is undefined.

- **Grounding:** engine `ThemeData.cpp`: `key == "variable" && node.attribute("name") && node.attribute("value")`
- **Grounding:** engine `ThemeData.cpp`: `mVariables["root"]`
- **Grounding:** engine `ThemeData.cpp`: `std::string variableName = "settings." + name;`
- **Grounding:** engine `ThemeData.cpp`: `mVariables["subset." + subsetAttr] = nameAttr;`
- **Grounding:** engine `SystemData.cpp`: `sysData["screen.ratio"]`
- **Grounding:** engine `SystemData.cpp`: `auto name = "system." + property.first;`
- **Grounding:** engine `ThemeData.cpp`: `mVariables["currentPath"] = Utils::FileSystem::getParent(mPaths.back());`
- **Grounding:** engine `ThemeData.cpp`: `if (name.find(".") != std::string::npos)`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_variables_are_resolved_before_value_checks`, `test_validate.py::test_extra_variables_are_used`, `test_validate.py::test_variable_attribute_form_defines_a_variable`, `test_validate.py::test_variables_after_a_view_still_apply`, `test_validate.py::test_only_real_engine_variables_are_defined`, `test_grammar.py::test_engine_variables_are_extracted`, `test_validate.py::test_current_path_is_the_including_files_folder`, `test_validate.py::test_settings_variables_have_one_segment`

What the engine does with an undefined variable isn't in the vendored source, so W003 is a
warning rather than an error.

### AC-V16: Unknown attributes on elements are warned about (W004)

`name`, `extra`, `importProperties` and the filter attributes (`if`, `lang`, `region`,
`ifSubset`, `verticalScreen`, `tinyScreen`, `ifHelpPrompts`, `ifCheevos`, `ifArch`,
`ifNotArch`) are allowed. Filter attributes decide at runtime whether a node applies, but the
validator checks the node either way, so they never skip a check and don't cause W005. The engine
silently skips any other attribute that isn't a property.

- **Grounding:** engine `ThemeData.cpp`: `findPropertyFromBaseClass(root.name(), name, type)`
- **Grounding:** engine `ThemeData.cpp`: `node.attribute("ifArch")`
- **Tests:** `test_validate.py::test_each_rule_fires_alone`, `test_validate.py::test_values_the_engine_accepts`, `test_validate.py::test_arch_filters_are_known_attributes`

### AC-V17: Checks skipped because of runtime values are reported, never silent (W005)

Some things can only be known on the console. When the validator can't check one of them, it
reports W005 naming the reason, and checks nothing else about that value:

- an include path, or any non-STRING property value, that depends on an engine variable whose
  value only exists on the console (for example `${screen.ratio}`, `${global.help}`,
  `$language` or `$country`). `$system` is known: see AC-V22;
- an `if="…"` expression. The engine evaluates these at load time, and an invalid one drops the
  node. The expression parser (`MathExpr.cpp`) is vendored, but the validator doesn't yet
  implement it, so every non-empty `if` is reported as unchecked (interim ruling on BLIND-10,
  2026-11-05);
- `<feature supported="manufacturer">`, which only applies if the console sets a manufacturer.

`${system.theme}`, `${system.name}` and the theme-path variables (AC-V15) are known, and never
cause W005. A STRING property never causes W005, because nothing about it is checked.

- **Grounding:** engine `ThemeData.cpp`: `relPath.find("$") != std::string::npos && relPath.find("${") == std::string::npos`
- **Grounding:** engine `ThemeData.cpp`: `expression is invalid`
- **Grounding:** project rule. The contract forbids passing silently what can't be checked.
- **Grounding:** engine `ThemeData.cpp`: `start_pos = result.find("$language");`
- **Tests:** `test_validate.py::test_runtime_include_is_reported_not_skipped`, `test_validate.py::test_runtime_value_is_reported_not_skipped`, `test_validate.py::test_known_system_variables_never_cause_w005`, `test_validate.py::test_if_expressions_are_reported_unchecked`

### AC-V18: `{random…}` is used only where the engine supports it (E042)

A PATH value starting with `{random` is honoured only if **all** of these hold; otherwise the
engine logs a warning and drops the property (ruling on BLIND-4, 2026-11-05):

1. the element is an extra (`extra="true"` or `extra="static"`);
2. the element is an `<image>` or a `<video>`;
3. its parent is a `<view>` whose `name` is exactly `system`. A shared name like
   `name="system, grid"` doesn't qualify;
4. a `<video>` uses exactly `{random}`, and an `<image>` uses `{random}` or one of
   `{random:thumbnail}`, `{random:marquee}`, `{random:image}`, `{random:fanart}`,
   `{random:titleshot}`.

- **Grounding:** engine `ThemeData.cpp`: `random is only supported in extras`
- **Grounding:** engine `ThemeData.cpp`: `random is only supported in systemview`
- **Grounding:** engine `ThemeData.cpp`: `unknow random element `
- **Tests:** `test_validate.py::test_random_is_only_allowed_where_the_engine_supports_it`

### AC-V19: `<feature supported>` names one supported feature (E005)

`<feature>` must have a `supported` attribute naming exactly one of the engine's supported
features (a single value, not a list). Otherwise the engine skips its views, with a warning when
the attribute is missing and silently when the value is unknown (rulings on BLIND-6 and BLIND-7,
2026-11-05). `manufacturer` is supported but depends on the console (AC-V17).

- **Grounding:** engine `ThemeData.cpp`: `Feature missing \"supported\" attribute!`
- **Grounding:** engine `ThemeData.cpp`: `if (sSupportedFeatures.find(supportedAttr) != sSupportedFeatures.cend())`
- **Tests:** `test_validate.py::test_feature_needs_one_supported_value`

### AC-V20: A storyboard has at least one usable animation (E032)

Each `<animation>` in a `<storyboard>` needs a `property` attribute naming a property of **the
element itself** (looked up without base classes, as the engine does) or starting with
`shader.`. Otherwise the engine skips that animation, with a warning for an unknown property and
silently for a missing one. A storyboard left with no usable `<animation>` and no `<sound>` is
discarded with a warning, which is also reported (ruling on BLIND-9, 2026-11-05). Children
other than `<animation>` and `<sound>` are silently ignored by the engine, so they're reported
too. A `<sound>` is usable only if its `path` is set and the file exists, resolved like any
relative path (it must start with `./`). Otherwise the engine skips it, reported as E032
(ruling on BLIND-24). An
animation's `from` and `to` are parsed with the property's type, so they're checked against
AC-V11's grammar for that type (`shader.*` is a FLOAT) (BLIND-12). A storyboard holding only
text is not the "remove" form, so it's reported (BLIND-25). A completely empty `<storyboard event="x"/>` is valid: the engine treats it as "remove the
storyboard for this event".

- **Grounding:** engine `ThemeStoryboard.cpp`: `Unknown storyboard property type`
- **Grounding:** engine `ThemeStoryboard.cpp`: `return animations.size() > 0;`
- **Tests:** `test_validate.py::test_storyboard_needs_a_usable_animation`, `test_validate.py::test_storyboard_sounds_and_values_are_checked`

### AC-V21: Relative paths start with `./` (E043)

The engine only resolves a path relative to the theme when it starts with `./` (or `.\`). A
bare relative path (`art/bg.png`, `config.xml`) or a `../` path is passed through unchanged, so
on the console it resolves against the program's working directory, not the theme. That applies
to include paths and to PATH property values, after variable substitution. Write `./art/bg.png`
and `./../shared/x.png`. A `.\` path is the same as `./` (the engine converts `\` to `/`;
BLIND-26). A path built from `${themePath}`, `${root}` or `${currentPath}` is absolute but
inside the theme, so it's checked for existence, in includes too (BLIND-23). Other absolute and
`~/` paths are covered by AC-V14.

- **Grounding:** engine `FileSystemUtil.cpp`: `if ((_path[0] == '.') && (_path[1] == '/' || _path[1] == '\\'))`
- **Grounding:** engine `FileSystemUtil.cpp`: `return getGenericPath(_path);`
- **Tests:** `test_validate.py::test_paths_must_start_with_dot_slash`, `test_validate.py::test_backslash_dot_paths_resolve`, `test_validate.py::test_theme_path_includes_resolve`

### AC-V22: System-dependent includes work for every system the theme is loaded for (E010)

The engine loads the theme once per system, including its built-in collection systems
(`auto-allgames`, `auto-favorites`, `custom-collections` and the rest, **extracted from
`CollectionSystemManager.cpp`**, never typed by hand). So an include whose path depends on the
system must resolve for the validated system **and** every built-in collection:

- `${system.theme}` is the collection's theme folder (`auto-allgames`); `${system.name}` is its
  declared name (`all`, `favorites`, `collections`), also extracted. A missing folder for any
  of those systems is E010, naming the systems it fails for. (The real engine found this: the theme's
  `./${system.theme}/custom.xml` failed for three collections, 2026-11-05.)
- `$system` (no braces) is the engine's designed fallback, with two quirks taken from the
  source. **For a system that has a compatibility alias** (extracted; for example
  `auto-favorites` → `favorites`, `msx` → `msx1`), `resolveSystemVariable` tests whether the
  *relative* path exists before the theme folder is applied, which it never does, so a relative
  `$system` path always uses the **alias** and never the system's own folder (ruling on
  BLIND-29). Then, when the path has no `${`, it falls back to **`default`**. **Once the
  `default` fallback is used by an include in the entry file, `$system` means `default` for the
  rest of that system's load** (`mSystemThemeFolder = "default"`; ruling on BLIND-30). So `./$system/custom.xml` with a `default/custom.xml` works
  for every system, including genre and user collections whose names can't be known in advance.
  That's the form to use.

Genre and user-made collections can't be enumerated statically. Only the `$system` form with a
`default` folder covers them.

**Known gap:** `$system` and `${system.theme}` in PATH *property* values (not includes) aren't
yet checked for each collection, only for the validated system (run 4, ambiguity 2).

- **Grounding:** engine `CollectionSystemManager.cpp`: `FileSorts::FILENAME_ASCENDING,    "auto-allgames"`
- **Grounding:** engine `ThemeData.cpp`: `path = Utils::FileSystem::resolveRelativePath(resolveSystemVariable("default", relPath), Utils::FileSystem::getParent(mPaths.back()), true);`
- **Grounding:** engine `ThemeData.cpp`: `mSystemThemeFolder = "default";`
- **Tests:** `test_validate.py::test_system_dependent_include_is_checked_for_every_collection`, `test_validate.py::test_dollar_system_falls_back_to_default`, `test_grammar.py::test_collection_systems_are_extracted`, `test_validate.py::test_aliased_systems_use_the_alias_folder`, `test_validate.py::test_default_fallback_sticks_for_the_rest_of_the_load`, `test_grammar.py::test_collection_names_are_extracted`


### AC-V23: A gridtile padding below one pixel is reported (W006)

A `<gridtile>`'s `padding` looks like the other paddings: written as values below 1, it should
be a fraction of the screen. The engine does try to scale it when its values are below 1, but
it multiplies by a `Vector2f`, and `Vector4f::operator*=(const Vector2f&)` throws its result
away. So a gridtile padding is always used as pixels, and `0.01` is a hundredth of a pixel.

The validator reports a `<gridtile>` `padding` that has any non-zero value whose magnitude is
below 1. That holds whichever of the 1-, 2- or 4-value forms is used, after variable
substitution, and whether the padding is a child element (`<padding>`) or an attribute
(`<gridtile padding="…">`), which the engine treats the same way (AC-V11; BLIND-31). The issue
is reported on the padding's own line: the `<padding>` element's, or the `<gridtile>`'s when
it's an attribute. Either way that's where the start tag begins. Zero is fine, and so is any value of 1 or more, because both mean what they say.
Other elements' paddings, including the `imagegrid`'s own, do scale and are never reported
under this rule. A padding that's already invalid as a rect (AC-V11) gets that error instead.

The theme still loads, so this is a warning. It's stricter than the engine, which says nothing,
because the padding the author wrote is not the padding the console uses.

- **Grounding:** engine `GridTileComponent.cpp`: `properties.Padding *= screen;`
- **Grounding:** engine `Vector4f.cpp`: `mX * _other.x(), mY * _other.y(), mZ * _other.x(), mW * _other.y();`
- **Grounding:** project rule. The engine accepts the value silently, but applies it as pixels.
- **Tests:** `test_validate.py::test_gridtile_padding_below_a_pixel_is_reported`, `test_validate.py::test_gridtile_padding_attribute_is_checked_too`
