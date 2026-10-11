"""CurseForge file fingerprints (modified Murmur2, seed 1).

See https://docs.curseforge.com/ — fingerprints strip whitespace bytes
(9, 10, 13, 32) before hashing with Murmur2 and seed ``1``.
"""
from __future__ import annotations


def _murmur2_32(data: bytes, seed: int) -> int:
    """32-bit MurmurHash2 (unsigned)."""
    length = len(data)
    m = 0x5BD1E995
    r = 24
    h = (seed ^ length) & 0xFFFFFFFF

    idx = 0
    while length >= 4:
        k = int.from_bytes(data[idx : idx + 4], "little")
        k = (k * m) & 0xFFFFFFFF
        k ^= (k >> r) & 0xFFFFFFFF
        k = (k * m) & 0xFFFFFFFF
        h = (h * m) & 0xFFFFFFFF
        h ^= k
        idx += 4
        length -= 4

    if length == 3:
        h ^= data[idx + 2] << 16
    if length >= 2:
        h ^= data[idx + 1] << 8
    if length >= 1:
        h ^= data[idx]
        h = (h * m) & 0xFFFFFFFF

    h ^= (h >> 13) & 0xFFFFFFFF
    h = (h * m) & 0xFFFFFFFF
    h ^= (h >> 15) & 0xFFFFFFFF
    return h & 0xFFFFFFFF


def curseforge_fingerprint(file_bytes: bytes) -> int:
    """Return the CurseForge fingerprint for ``file_bytes``."""
    filtered = bytes(b for b in file_bytes if b not in (9, 10, 13, 32))
    return _murmur2_32(filtered, 1)
