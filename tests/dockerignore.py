"""A `.dockerignore` matcher, so a guard can answer *"will this COPY find its source?"* (F130).

## Why this module exists

`testbed/Dockerfile.ne` copies three paths — `tools/trap_replay.py`, `testbed/ne` and
`testbed/scenarios` — and the root `.dockerignore` excluded `tools/` and `testbed/`. The image
therefore could not be built: `docker compose up --build` fails with
``"/testbed/scenarios": not found``.

v0.17.0 shipped it anyway because the only thing validating the lab's compose file was
`docker compose config`, **which parses YAML and never reads `.dockerignore`**. That is Appendix
B's "a path validated by the tool that cannot see the failure", and the remedy is a check that
looks at the thing the failing tool cannot: the ignore file and the COPY lines, together.

## Why re-implement the matching rules

The alternative is to run `docker build`, which needs a daemon and a reachable registry. Neither
is available in every environment this repository is built in — v0.17.0's own notes record a
registry that answers 403 — so a guard that needs them is a guard that skips, and a skipped guard
is how this shipped. The rules below are small, and `tests/test_deploy.py` exercises them against
the patterns this repository actually contains.

## The rules, as Docker defines them

* Blank lines and `#` comments are ignored.
* A pattern is matched against the path relative to the build context, with `/` separators.
* `*` matches within one path component; `**` matches across components; `?` matches one
  character that is not a separator.
* A leading `!` negates: the path is re-included.
* **The last pattern that matches decides.** A path is also matched by a pattern that matches any
  of its parent directories, which is what makes `tests/` exclude `tests/test_deploy.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath


def _to_regex(pattern: str) -> re.Pattern[str]:
    """Translate one Docker ignore pattern into an anchored regular expression.

    Written character by character rather than with `fnmatch.translate` because the two disagree
    on exactly the case that matters here: `fnmatch`'s `*` crosses `/`, Docker's does not.
    """
    out = ["^"]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    out.append("$")
    return re.compile("".join(out))


@dataclass(frozen=True)
class _Rule:
    regex: re.Pattern[str]
    negated: bool
    source: str


class DockerIgnore:
    """The parsed ignore file, able to answer whether one context-relative path survives."""

    def __init__(self, rules: list[_Rule]) -> None:
        self.rules = rules

    @classmethod
    def parse(cls, text: str) -> DockerIgnore:
        rules: list[_Rule] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            body = line[1:].strip() if negated else line
            # `tests/` and `tests` are the same rule; the trailing slash is how humans write
            # "a directory" and Docker normalises it away.
            body = body.rstrip("/")
            if not body:
                continue
            rules.append(_Rule(_to_regex(body), negated, line))
        return cls(rules)

    def _last_match(self, path: str) -> _Rule | None:
        """The deciding rule for `path`, considering the path and each of its parents."""
        candidates = [str(PurePosixPath(path))]
        candidates += [str(parent) for parent in PurePosixPath(path).parents if str(parent) != "."]
        decisive: _Rule | None = None
        for rule in self.rules:
            if any(rule.regex.match(candidate) for candidate in candidates):
                decisive = rule
        return decisive

    def excluded(self, path: str) -> bool:
        """Is this context-relative path kept out of the build context?"""
        rule = self._last_match(path)
        return rule is not None and not rule.negated

    def explain(self, path: str) -> str:
        """The rule that decided, for a failure message that names the line to edit."""
        rule = self._last_match(path)
        if rule is None:
            return f"{path}: no pattern matches, so it is included"
        verb = "re-included by" if rule.negated else "EXCLUDED by"
        return f"{path}: {verb} {rule.source!r}"


#: Directives that pull a path out of the build context. `ADD` is here because it has the same
#: context semantics; listing only `COPY` would be a guard narrower than its rule (F112/F113).
_CONTEXT_DIRECTIVES = ("COPY", "ADD")


def copy_sources(dockerfile_text: str) -> list[str]:
    """Every build-context path a Dockerfile copies in, derived from its own text.

    Skips `COPY --from=...`, whose sources come from an earlier build stage or an external image
    rather than from the context, so the ignore file has no say over them. Flags are dropped;
    the final argument is the destination and is not a source.
    """
    sources: list[str] = []
    for raw in _logical_lines(dockerfile_text):
        parts = raw.split()
        if not parts or parts[0].upper() not in _CONTEXT_DIRECTIVES:
            continue
        args = [p for p in parts[1:] if not p.startswith("--")]
        if any(p.lower().startswith("--from=") for p in parts[1:]):
            continue  # not from the build context
        if len(args) < 2:
            continue  # malformed; a syntax check is not this guard's job
        sources.extend(args[:-1])
    return sources


def _logical_lines(text: str) -> list[str]:
    """Dockerfile lines with `\\` continuations joined and comments dropped."""
    joined: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("#"):
            continue
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        joined.append(buffer + line)
        buffer = ""
    if buffer:
        joined.append(buffer)
    return joined
