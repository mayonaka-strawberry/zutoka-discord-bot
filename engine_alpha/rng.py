"""Counter-based RNG: each chance event is a pure function of (key, counter), so clones
share every future. splitmix64 plus Fisher-Yates in pure Python.
"""

from __future__ import annotations

_MASK = (1 << 64) - 1


def _splitmix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & _MASK
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
    return (z ^ (z >> 31)), x


def derive_seed(key: int, salt: int) -> int:
    """A 64-bit seed decorrelated from (key, salt); gives each game of a series its own seed."""
    x = ((key * 0x2545F4914F6CDD1D) ^ (salt * 0xD1342543DE82EF95)) & _MASK
    value, _ = _splitmix64(x)
    return value


def _stream(key: int, counter: int, count: int) -> list[int]:
    """`count` pseudo-random uint64s for chance event number `counter`."""
    x = ((key * 0x2545F4914F6CDD1D) ^ (counter * 0xD1342543DE82EF95)) & _MASK
    out = []
    for _ in range(count):
        value, x = _splitmix64(x)
        out.append(value)
    return out

def shuffled(items: list[int], key: int, counter: int) -> list[int]:
    """Shuffle `items` for chance event `counter`. The caller must then increment the counter."""
    result = list(items)
    n = len(result)
    if n < 2:
        return result
    randoms = _stream(key, counter, n - 1)
    for i in range(n - 1, 0, -1):
        j = randoms[n - 1 - i] % (i + 1)
        result[i], result[j] = result[j], result[i]
    return result


def random_below(bound: int, key: int, counter: int) -> int:
    """Deterministic integer in [0, bound) for chance event `counter`."""
    return _stream(key, counter, 1)[0] % bound
