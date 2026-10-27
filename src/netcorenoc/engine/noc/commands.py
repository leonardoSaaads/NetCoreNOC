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
_FORBIDDEN_CHARS = re.compile(r"[;|&><`\\\x00-\x1f\x7f]|\$\(")
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
