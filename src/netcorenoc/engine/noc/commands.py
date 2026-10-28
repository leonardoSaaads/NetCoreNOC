"""What an agent or a playbook may run on a device: read-only commands, and nothing else (ADR #462).

The owner's rule for this release: **read only**. A command is a single line whose first word is a
read verb — `show` (Cisco, Juniper, Ciena SAOS, Nokia, Arista), `display`/`dis` (Huawei, H3C),
`get` (some appliances), `ping` and `traceroute`/`tracert` (with the diagnostic access level or
above) — and which carries nothing a shell or a device CLI would treat as a second command or a
redirection: no `;`, `|`, `&`, `>`, `<`, backquote, `$(`, newline or control character, and no
word that writes anywhere on the line (`configure`, `commit`, `reload`, `delete`, `clear`…). The
same rule answers the API, the playbook validator and the playbook runner: a playbook that names
a write is refused when it is stored, and a command that becomes one after a value is substituted
into it is refused when it runs.

**MikroTik RouterOS** (ADR #469) has no read verb in front: a command is a menu path and then the
command, `/interface ethernet print detail`. A line that begins with `/` is read as RouterOS and
may run when its command is `print`, `monitor … once` or (diagnostic) `ping`, with no write word
anywhere on it — each part of a hyphenated word counted, so `reset-counters` is `reset` — and no
bracket or brace, which RouterOS evaluates as a nested command (`[/system script run x]`).

**Why a deny-list of words on top of an allow-list of verbs**: `show` on some CLIs takes
sub-commands that act (`show tech-support | redirect`), and text a device returned can reach a
playbook's next command through a substituted value. Refusing every write word anywhere on the
line is cheap and closes the case where the verb is honest and the rest is not.

Pure.
"""

from __future__ import annotations

import re

__all__ = ["MAX_COMMAND", "READ_VERBS", "CommandError", "check"]

MAX_COMMAND = 512

#: The first word of a permitted command, and the least access level it needs.
READ_VERBS: dict[str, str] = {
    "show": "view_only",
    "display": "view_only",
    "dis": "view_only",
    "get": "view_only",
    "ping": "diagnostic",
    "traceroute": "diagnostic",
    "tracert": "diagnostic",
}
_RANK = {"view_only": 0, "diagnostic": 1, "operator": 2, "root": 3}

#: A word anywhere on the line that writes, restarts, or escalates. Matched whole, case-folded.
_WRITES = frozenset(
    {
        "configure",
        "config-mode",
        "conf",
        "commit",
        "rollback",
        "reload",
        "reboot",
        "restart",
        "shutdown",
        "delete",
        "del",
        "erase",
        "format",
        "write",
        "wr",
        "copy",
        "move",
        "rename",
        "clear",
        "reset",
        "set",
        "unset",
        "undo",
        "save",
        "request",
        "debug",
        "undebug",
        "system-view",
        "sys",
        "enable",
        "su",
        "sudo",
        "kill",
        "rm",
        "mkdir",
        "install",
        "upgrade",
        "load",
        "merge",
        "replace",
        "activate",
        "deactivate",
        "redirect",
        "tee",
        "exec",
        "bash",
        "sh",
        "shell",
        "start",
        "telnet",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        "tftp",
        "curl",
        "wget",
    }
)
#: RouterOS's own acts, beyond the words every CLI shares above.
_ROS_WRITES = frozenset(
    {
        "add",
        "remove",
        "edit",
        "comment",
        "disable",
        "export",
        "import",
        "run",
        "execute",
        "file",
        "fetch",
        "download",
        "send",
        "upload",
        "password",
        "bandwidth-test",
        "flood-ping",
        "make-supout",
    }
)
_ROS_READS = {"print": "view_only", "monitor": "view_only", "ping": "diagnostic"}
_FORBIDDEN_CHARS = re.compile(r"[;|&><`\\\x00-\x1f\x7f]|\$\(")
_ROS_FORBIDDEN = re.compile(r"[\[\]{}]")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


class CommandError(ValueError):
    """A command this appliance will not send, with the sentence why."""


def check(command: str, access_level: str) -> str:
    """The command, stripped, if it may run under ``access_level``; else a :class:`CommandError`."""
    line = command.strip()
    if not line:
        raise CommandError("command: empty")
    if len(line) > MAX_COMMAND:
        raise CommandError(f"command: at most {MAX_COMMAND} characters")
    if _FORBIDDEN_CHARS.search(line):
        raise CommandError("command: one line, with no ; | & > < ` \\ $( or control character")
    if line.startswith("/"):
        return _routeros(line, access_level)
    words = [w.lower() for w in _WORD.findall(line)]
    verb = line.split()[0].lower()
    least = READ_VERBS.get(verb)
    if least is None:
        raise CommandError(
            f"command: read-only — it must begin with one of {', '.join(sorted(READ_VERBS))}"
        )
    writes = sorted(set(words[1:]) & _WRITES)
    if writes:
        raise CommandError(f"command: read-only — {writes[0]!r} writes or escalates")
    if _RANK.get(access_level, -1) < _RANK[least]:
        raise CommandError(f"command: {verb!r} needs the {least} access level or above")
    return line


def _routeros(line: str, access_level: str) -> str:
    """A RouterOS line: a menu path, then `print`, `monitor … once` or `ping`, and nothing that
    writes."""
    if _ROS_FORBIDDEN.search(line):
        raise CommandError("command: RouterOS — no [ ] { }: they run a nested command")
    words = [w.lower() for w in _WORD.findall(line)]
    parts = {p for w in words for p in (w, *w.split("-"))}
    writes = sorted(parts & (_WRITES | _ROS_WRITES))
    if writes:
        raise CommandError(f"command: read-only — {writes[0]!r} writes or escalates")
    verb = next((w for w in words if w in _ROS_READS), None)
    if verb is None or (verb == "monitor" and "once" not in words):
        raise CommandError(
            "command: RouterOS, read-only — a menu path then print, monitor … once, or ping"
        )
    least = _ROS_READS[verb]
    if _RANK.get(access_level, -1) < _RANK[least]:
        raise CommandError(f"command: {verb!r} needs the {least} access level or above")
    return line
