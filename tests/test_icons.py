"""The icon set is exactly what the console renders — no more, no fewer (VII.3, #236).

**A set of forty when twenty-five render is forty to maintain.** This is the guard that makes that
sentence enforceable rather than an intention: it walks `icons.js` for the names the module can
draw, walks every other console module for the names something asks for, and fails in **both**
directions.

The direction that matters is the unused one. A missing icon shows up the moment somebody opens the
screen — `Icon` renders nothing, deliberately, so the label stands alone and the gap is visible. An
*unused* icon is invisible forever, which is exactly how the seventeen Unicode glyphs came to span
four Unicode blocks with nobody noticing they were no longer a family.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "src" / "netcorenoc" / "ui"
ICONS = UI / "app" / "icons.js"

#: `name: {` at the top level of the ICONS table, plus the quoted form for hyphenated names.
_DEFINED = re.compile(r'^  (?:"([a-z-]+)"|([a-z-]+)): \{', re.M)

#: An `<${Icon} …/>` element, up to its close. **Anchored on the element**, not on a bare
#: `name="…"`: the first version of this matched every `name` attribute in the tree and reported
#: `<input name="username">` as an icon the console asked for. A guard that reads the whole file
#: for an attribute name is reading the wrong thing.
_ELEMENT = re.compile(r"<\$\{Icon\}(.*?)/>", re.S)

#: Every quoted string inside that element — which covers `name="x"`, `name=${"x"}` and the
#: ternary `name=${shown ? "eye-off" : "eye"}`. Both arms of a ternary are uses: an icon rendered
#: only when a control is toggled is still rendered.
_QUOTED = re.compile(r'"([a-z-]+)"')

#: The two lookup tables that map a state to an icon name: the registry's `icon:` field and
#: `shell.THEME_ICON`. A name reaching `Icon` through a table is as used as a literal one.
_TABLE = re.compile(r'(?:\bicon|[A-Z_]*ICON[A-Z_]*)\s*[:=]\s*(?:\{([^}]*)\}|"([a-z-]+)")')
_TABLE_VALUE = re.compile(r':\s*"([a-z-]+)"')


def _modules() -> dict[str, str]:
    return {
        str(path.relative_to(UI)): path.read_text(encoding="utf-8")
        for path in sorted(UI.rglob("*.js"))
        if "vendor" not in path.parts
    }


def defined() -> set[str]:
    return {a or b for a, b in _DEFINED.findall(ICONS.read_text(encoding="utf-8"))}


def used() -> dict[str, set[str]]:
    """Icon name -> the modules that ask for it."""
    out: dict[str, set[str]] = {}
    for name, source in _modules().items():
        if name == "app/icons.js":
            continue  # the definition is not a use
        found: set[str] = set()
        for element in _ELEMENT.findall(source):
            found |= set(_QUOTED.findall(element))
        for block, single in _TABLE.findall(source):
            found |= set(_TABLE_VALUE.findall(block)) if block else {single}
        for icon in found:
            out.setdefault(icon, set()).add(name)
    return out


def test_the_table_and_the_call_sites_were_both_found() -> None:
    """Guard the guard. Two regexes that matched nothing would make every assertion below pass."""
    assert len(defined()) >= 20, f"the icon table did not parse: {sorted(defined())}"
    assert len(used()) >= 17, f"the call sites did not parse: {sorted(used())}"


def test_every_icon_in_the_set_is_rendered_somewhere() -> None:
    """**VII.3.** The direction no screen and no reviewer would ever notice."""
    unused = sorted(defined() - set(used()))
    assert not unused, (
        f"{len(unused)} icon(s) are defined and rendered nowhere: {unused}. Delete them — an icon "
        f"nobody draws is a drawing somebody has to keep consistent with the rest of the family."
    )


def test_every_icon_the_console_asks_for_exists() -> None:
    """The other direction. `Icon` renders nothing for an unknown name, so this is what says so."""
    missing = {name: sorted(where) for name, where in used().items() if name not in defined()}
    assert not missing, f"the console asks for icons that icons.js cannot draw: {missing}"


def test_every_view_in_the_registry_names_an_icon() -> None:
    """The sidebar renders one per view, so a view without one is a gap in the navigation."""
    registry = (UI / "app" / "registry.js").read_text(encoding="utf-8")
    ids = re.findall(r'id: "([a-z]+)", label: "[^"]+", icon: "([a-z-]+)"', registry)
    assert len(ids) == 17, f"expected 17 views with icons, parsed {len(ids)}: {ids}"
    known = defined()
    for view_id, icon in ids:
        assert icon in known, f"view {view_id!r} names icon {icon!r}, which is not in the set"
    assert len({icon for _v, icon in ids}) == 17, "two views share an icon; they are not the same"


def test_the_family_is_one_geometry() -> None:
    """What makes seventeen marks a family rather than seventeen drawings (#236).

    Asserted against the **one** `<svg>` the module emits: one viewBox, one stroke width, one cap
    and join, `currentColor`, and no fill. The seventeen Unicode glyphs came from four Unicode
    blocks and rendered at whatever weight the operator's font stack chose; this is the property
    that replaces that, and it is checkable where "the console has an identity" is not.
    """
    source = ICONS.read_text(encoding="utf-8")
    # The module's own prose mentions `<svg>`, so count what it EMITS: the tagged template.
    assert source.count("html`<svg") == 1, "more than one svg is more than one set of family rules"
    for required in (
        'viewBox="0 0 24 24"',
        'stroke-width="1.5"',
        'stroke="currentColor"',
        'stroke-linecap="round"',
        'stroke-linejoin="round"',
        'fill="none"',
        'aria-hidden="true"',
    ):
        assert required in source, f"the shared svg does not set {required}"
    # No path may carry its own colour or weight: that is where a family stops being one.
    assert not re.search(r'\bfill="(?!none)', source), "an icon sets its own fill"
    weights = re.findall(r'stroke-width="([\d.]+)"', source)
    assert weights == ["1.5"], f"more than one stroke weight in the family: {weights}"


def test_no_icon_is_the_only_signal_it_carries() -> None:
    """`aria-hidden` at every call site, which is the rule the glyphs already followed.

    An icon that carried meaning alone would fail the same operator the severity rules are written
    for — and severity is the precedent: colour AND glyph AND text, never one of the three.
    """
    source = ICONS.read_text(encoding="utf-8")
    assert 'aria-hidden="true"' in source, "the shared svg is not hidden from assistive technology"
    assert "aria-label" not in source, (
        "an icon carries its own label, which means a call site is relying on the icon to say "
        "something its text does not"
    )
