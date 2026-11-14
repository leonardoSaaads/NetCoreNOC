"""A QR code, drawn by the appliance itself, for the authenticator enrolment (ADR #482).

Two-factor sign-in shows the authenticator app a QR code holding the ``otpauth://`` link. The code
is computed **here, on the server**, and travels to the console as a grid of modules that the
console draws as inline SVG. Three reasons:

* **No new dependency.** The standard library has no QR encoder; this module is the part of
  ISO/IEC 18004 an ``otpauth://`` link needs: byte mode, error correction level M, versions 1 to 20.
* **No third-party script near a secret.** The link holds the account's TOTP secret; the only code
  that sees it is this module and the console's own renderer.
* **The page's CSP stays as it is.** ``img-src 'self'`` forbids a ``data:`` image; an inline
  ``<svg>`` of rectangles is markup, not an image fetch.

The algorithm follows the standard: the data codewords, Reed-Solomon error correction over
GF(256) per block, interleaving, the function patterns (finders, separators, timing, alignment,
format and version information), the zig-zag placement, and the mask with the lowest penalty.

Pure. ``encode(text)`` → a square list of rows, ``True`` for a dark module, without the quiet zone
(the renderer adds four modules of margin).
"""

from __future__ import annotations

__all__ = ["MAX_VERSION", "QrError", "encode"]

#: The largest version drawn: 77 x 77 modules, 666 bytes at level M — far above any link we make.
MAX_VERSION = 20

#: Error correction level M: about 15 % of the code may be damaged and still read.
#: Per version (index 0 is unused): ECC codewords per block, and the number of blocks.
_ECC_PER_BLOCK = (
    -1,
    10,
    16,
    26,
    18,
    24,
    16,
    18,
    22,
    22,
    26,
    30,
    22,
    22,
    24,
    24,
    28,
    28,
    26,
    26,
    26,
)
_BLOCKS = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16)
#: Level M's two format bits.
_FORMAT_M = 0
_PENALTY_N1, _PENALTY_N2, _PENALTY_N3, _PENALTY_N4 = 3, 3, 40, 10


class QrError(ValueError):
    """The text does not fit the largest version drawn."""


def _raw_modules(version: int) -> int:
    """Modules left for data and error correction once every function pattern is drawn."""
    result = (16 * version + 128) * version + 64
    if version >= 2:
        aligns = version // 7 + 2
        result -= (25 * aligns - 10) * aligns - 55
        if version >= 7:
            result -= 36
    return result


def _data_capacity(version: int) -> int:
    """Data codewords at level M."""
    return _raw_modules(version) // 8 - _ECC_PER_BLOCK[version] * _BLOCKS[version]


def _count_bits(version: int) -> int:
    return 8 if version <= 9 else 16


def _gf_multiply(x: int, y: int) -> int:
    """Multiplication in GF(2^8) modulo x^8 + x^4 + x^3 + x^2 + 1."""
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_multiply(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coefficient in enumerate(divisor):
            result[i] ^= _gf_multiply(coefficient, factor)
    return result


def _codewords(payload: bytes, version: int) -> list[int]:
    """Mode, count, data, terminator and padding, as data codewords, then interleaved with ECC."""
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)  # byte mode
    put(len(payload), _count_bits(version))
    for byte in payload:
        put(byte, 8)
    capacity = _data_capacity(version) * 8
    put(0, min(4, capacity - len(bits)))
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    data = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]

    blocks_n, ecc_len = _BLOCKS[version], _ECC_PER_BLOCK[version]
    raw = _raw_modules(version) // 8
    short_n = blocks_n - raw % blocks_n
    short_len = raw // blocks_n
    divisor = _rs_divisor(ecc_len)
    blocks: list[list[int]] = []
    k = 0
    for i in range(blocks_n):
        length = short_len - ecc_len + (0 if i < short_n else 1)
        chunk = data[k : k + length]
        k += length
        ecc = _rs_remainder(chunk, divisor)
        if i < short_n:
            chunk.append(0)  # a placeholder, skipped when interleaving
        blocks.append(chunk + ecc)
    out: list[int] = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_n:
                out.append(block[i])
    return out


def _alignment_positions(version: int, size: int) -> list[int]:
    if version == 1:
        return []
    aligns = version // 7 + 2
    step = (version * 4 + aligns * 2 + 1) // (aligns * 2 - 2) * 2
    positions = [size - 7 - i * step for i in range(aligns - 1)] + [6]
    return sorted(positions)


class _Grid:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.function[y][x] = True

    def draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = _alignment_positions(self.version, size)
        last = len(positions) - 1
        for i, ax in enumerate(positions):
            for j, ay in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # reserves the format areas; redrawn with the chosen mask
        if self.version >= 7:
            remainder = self.version
            for _ in range(12):
                remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
            bits = self.version << 12 | remainder
            for i in range(18):
                dark = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self.set(a, b, dark)
                self.set(b, a, dark)

    def draw_format(self, mask: int) -> None:
        data = _FORMAT_M << 3 | mask
        remainder = data
        for _ in range(10):
            remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
        bits = (data << 10 | remainder) ^ 0x5412
        size = self.size

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, size - 15 + i, bit(i))
        self.set(8, size - 8, True)  # the dark module

    def draw_codewords(self, codewords: list[int]) -> None:
        size = self.size
        total = len(codewords) * 8
        i = 0
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            for vertical in range(size):
                y = size - 1 - vertical if upward else vertical
                for j in range(2):
                    x = right - j
                    if not self.function[y][x] and i < total:
                        self.dark[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        for y in range(self.size):
            for x in range(self.size):
                if not self.function[y][x] and _MASKS[mask](x, y):
                    self.dark[y][x] = not self.dark[y][x]

    def penalty(self) -> int:
        size, dark = self.size, self.dark
        score = 0
        lines = [list(row) for row in dark] + [
            [dark[y][x] for y in range(size)] for x in range(size)
        ]
        finder_like = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])
        for line in lines:
            run, colour = 0, None
            for module in line:
                if module == colour:
                    run += 1
                else:
                    if run >= 5:
                        score += _PENALTY_N1 + run - 5
                    run, colour = 1, module
            if run >= 5:
                score += _PENALTY_N1 + run - 5
            bits = [int(m) for m in line]
            for start in range(size - 10):
                if bits[start : start + 11] in finder_like:
                    score += _PENALTY_N3
        for y in range(size - 1):
            for x in range(size - 1):
                colour = dark[y][x]
                if colour == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                    score += _PENALTY_N2
        total = size * size
        dark_count = sum(sum(row) for row in dark)
        k = (abs(dark_count * 20 - total * 10) + total - 1) // total - 1
        return score + k * _PENALTY_N4


_MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def _version_for(length: int) -> int:
    for version in range(1, MAX_VERSION + 1):
        header = 4 + _count_bits(version)
        if header + length * 8 <= _data_capacity(version) * 8:
            return version
    raise QrError(f"{length} bytes do not fit a version-{MAX_VERSION} QR code")


def encode(text: str, *, mask: int | None = None) -> list[list[bool]]:
    """The modules of a QR code for ``text`` (UTF-8, byte mode, level M). ``mask`` forces one of
    the eight masks; otherwise the one with the lowest penalty is drawn."""
    payload = text.encode("utf-8")
    version = _version_for(len(payload))
    codewords = _codewords(payload, version)
    best: tuple[int, list[list[bool]]] | None = None
    for candidate in range(8) if mask is None else (mask,):
        grid = _Grid(version)
        grid.draw_function_patterns()
        grid.draw_codewords(codewords)
        grid.apply_mask(candidate)
        grid.draw_format(candidate)
        score = grid.penalty()
        if best is None or score < best[0]:
            best = (score, [list(row) for row in grid.dark])
    assert best is not None
    return best[1]
