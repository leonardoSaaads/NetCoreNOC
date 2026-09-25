"""The stylesheet's one structural rule, and the defect that made it one (v0.22.0, item 2).

The health control's meters drew their fill at a width unrelated to the percentage beside them.
`health.js` computed `width: 37%` correctly; a later section of `style.css`, written for the model
screen's floor gauge, declared `.meter` and `.meter-fill` again at top level — a three-column grid
— and the cascade handed the health bar to it. No test read it, because the DOM was right and the
pixels were wrong. The model gauge's classes became `.floor*`; this guard is what keeps the next
screen from taking a name that is already drawing something else.

**The rule:** a bare class selector (`.name`, nothing else) is the whole selector of **at most one**
top-level rule. Compound selectors, states and media blocks are free; two top-level `.meter { … }`
blocks are not, because the second one silently rewrites the first wherever both apply.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import netcorenoc

CSS = (Path(netcorenoc.__file__).resolve().parent / "ui" / "style.css").read_text(encoding="utf-8")


def _top_level_selectors(css: str) -> list[str]:
    """Every selector list of a rule at nesting depth zero, comments removed."""
    text = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                out.append(text[start:index].strip())
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                start = index + 1
    return [selector for selector in out if selector and not selector.startswith("@")]


def _bare_classes(css: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for selector in _top_level_selectors(css):
        # The WHOLE selector: a group (`a.tap, .who-name { … }`) shares declarations on purpose.
        if re.fullmatch(r"\.[A-Za-z][\w-]*", selector):
            counts[selector] += 1
    return counts


def test_a_bare_class_is_the_whole_selector_of_at_most_one_top_level_rule() -> None:
    repeated = {name: n for name, n in _bare_classes(CSS).items() if n > 1}
    assert not repeated, (
        f"these classes are each the whole selector of more than one top-level rule, so the later "
        f"block rewrites the earlier wherever both apply: {repeated}"
    )


def test_the_guard_sees_the_collision_it_exists_for() -> None:
    """The control: the item-2 stylesheet shape, reduced to its two rules, is caught."""
    collision = ".meter { display: block; }\n.x { color: red; }\n.meter { display: grid; }\n"
    assert _bare_classes(collision)[".meter"] == 2
    # …and what is allowed is not: compound, stateful and media-scoped selectors.
    allowed = ".meter { a: b; }\n.meter.warn { a: c; }\n.meter:hover { a: d; }\n"
    allowed += "@media (max-width: 600px) { .meter { a: e; } }\n.meter, .gauge { a: f; }\n"
    assert _bare_classes(allowed)[".meter"] == 1


def test_the_model_gauge_no_longer_uses_the_health_meter_names() -> None:
    models = (
        Path(netcorenoc.__file__).resolve().parent / "ui" / "app" / "views" / "parts" / "models.js"
    ).read_text(encoding="utf-8")
    assert "meter" not in models, "the model screen draws with the health control's class names"
    assert "floor" in models
