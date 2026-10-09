"""Draw the sign-in artwork, `src/netcorenoc/ui/login-bg.svg` (v0.30.0, ADR #446).

A dark NOC canvas: a wireframe globe, a mesh of network elements and their links, four elements in
alarm with the links they touch, and one dashed "situation" around two of them. Seeded, so the file
is reproducible byte for byte — `tests/ui/test_login_art.py` regenerates it and compares — and
written without any dependency, because the console loads no third-party image (`img-src 'self'`).

    python tools/login_art.py                 # rewrite the shipped file
    python tools/login_art.py --stdout        # print it instead
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "netcorenoc" / "ui" / "login-bg.svg"
WIDTH, HEIGHT, SEED = 1600, 1000, 20261009
GLOBE = (520, 520, 360)
#: Where the sign-in screen writes its words; no alarm glow is drawn under them.
TEXT_BOX = (0, 280, 640, 660)

Point = tuple[float, float]

DEFS = (
    '<defs><radialGradient id="bg" cx="30%" cy="45%" r="85%">'
    '<stop offset="0" stop-color="#10284a"/><stop offset=".55" stop-color="#0a1830"/>'
    '<stop offset="1" stop-color="#050b16"/></radialGradient>'
    '<radialGradient id="glow" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#4a9eff" '
    'stop-opacity=".35"/><stop offset="1" stop-color="#4a9eff" stop-opacity="0"/></radialGradient>'
    '<radialGradient id="hot" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#ff9f43" '
    'stop-opacity=".55"/><stop offset="1" stop-color="#ff6b5e" stop-opacity="0"/></radialGradient>'
    '<linearGradient id="link" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4a9eff" '
    'stop-opacity=".55"/><stop offset="1" stop-color="#4cc38a" stop-opacity=".35"/>'
    "</linearGradient>"
    '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" '
    'fill="none" stroke="#4a9eff" stroke-opacity=".05" stroke-width="1"/></pattern></defs>'
)


def _globe() -> list[str]:
    cx, cy, r = GLOBE
    out = [f'<circle cx="{cx}" cy="{cy}" r="{r + 140}" fill="url(#glow)"/>']
    out.append('<g fill="none" stroke="#6fb3ff" stroke-width="1">')
    out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" stroke-opacity=".28"/>')
    for lat in range(-60, 90, 20):
        y = cy - r * math.sin(math.radians(lat))
        rx = r * math.cos(math.radians(lat))
        out.append(
            f'<ellipse cx="{cx}" cy="{y:.0f}" rx="{rx:.0f}" ry="{rx * 0.18:.0f}" '
            'stroke-opacity=".12"/>'
        )
    for lon in range(0, 180, 20):
        rx = abs(r * math.cos(math.radians(lon)))
        out.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{rx:.0f}" ry="{r}" stroke-opacity=".12"/>')
    out.append("</g>")
    return out


def _nodes(rnd: random.Random) -> list[Point]:
    cx, cy, r = GLOBE
    nodes: list[Point] = []
    for _ in range(26):
        t = rnd.random() * 2 * math.pi
        d = r * math.sqrt(rnd.random()) * 0.92
        nodes.append((cx + d * math.cos(t), cy + d * math.sin(t)))
    nodes.extend((rnd.uniform(900, 1580), rnd.uniform(40, 960)) for _ in range(30))
    for _ in range(8):
        y = rnd.choice([rnd.uniform(20, 140), rnd.uniform(880, 990)])
        nodes.append((rnd.uniform(40, 900), y))
    return nodes


def _under_text(p: Point) -> bool:
    x0, y0, x1, y1 = TEXT_BOX
    return x0 <= p[0] < x1 and y0 < p[1] < y1


def draw() -> str:
    """The whole SVG document, deterministically."""
    rnd = random.Random(SEED)  # nosec B311 - decoration, not security
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        'preserveAspectRatio="xMidYMid slice">',
        "<title>NetCoreNOC</title>",
        DEFS,
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="url(#bg)"/>',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="url(#grid)"/>',
        *_globe(),
    ]
    nodes = _nodes(rnd)
    edges: set[tuple[int, int]] = set()
    for i, p in enumerate(nodes):
        near = sorted(range(len(nodes)), key=lambda j: math.dist(p, nodes[j]))[1:4]
        edges.update((min(i, j), max(i, j)) for j in near if math.dist(p, nodes[j]) < 330)
    clear = [k for k, p in enumerate(nodes) if not _under_text(p)]
    alarm = {clear[1], clear[5], 31, 44}
    out.append('<g fill="none" stroke-linecap="round">')
    for i, j in sorted(edges):
        (px, py), (qx, qy) = nodes[i], nodes[j]
        bend = rnd.uniform(-0.18, 0.18)
        cx, cy = (px + qx) / 2 - (qy - py) * bend, (py + qy) / 2 + (qx - px) * bend
        hot = i in alarm or j in alarm
        stroke = (
            'stroke="#ff9f43" stroke-opacity=".55" stroke-width="1.6"'
            if hot
            else 'stroke="url(#link)" stroke-width="1.1"'
        )
        out.append(f'<path d="M{px:.0f} {py:.0f}Q{cx:.0f} {cy:.0f} {qx:.0f} {qy:.0f}" {stroke}/>')
    out.append("</g>")
    for k, (x, y) in enumerate(nodes):
        if k in alarm:
            out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="34" fill="url(#hot)"/>')
            out.append(
                f'<circle cx="{x:.0f}" cy="{y:.0f}" r="13" fill="none" stroke="#ff9f43" '
                'stroke-opacity=".7" stroke-width="1.4"/>'
            )
            out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="4.5" fill="#ffb347"/>')
            continue
        size = rnd.choice([2.2, 2.8, 3.4])
        dot = f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{size}" fill="#9fd0ff" fill-opacity=".85"/>'
        out.append(dot)
        if rnd.random() < 0.35:
            out.append(
                f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{size * 3.2:.1f}" fill="none" '
                'stroke="#4a9eff" stroke-opacity=".25"/>'
            )
    first, second = (nodes[k] for k in sorted(alarm)[:2])
    mx, my = (first[0] + second[0]) / 2, (first[1] + second[1]) / 2
    out.append(
        f'<circle cx="{mx:.0f}" cy="{my:.0f}" r="{math.dist(first, second) / 2 + 60:.0f}" '
        'fill="none" stroke="#ffb347" stroke-opacity=".35" stroke-dasharray="6 8"/>'
    )
    out.append("</svg>")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--stdout", action="store_true", help="print the SVG instead of writing it")
    args = parser.parse_args(argv)
    svg = draw()
    if args.stdout:
        sys.stdout.write(svg)
    else:
        OUT.write_text(svg, encoding="utf-8")
        print(f"wrote {OUT} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
