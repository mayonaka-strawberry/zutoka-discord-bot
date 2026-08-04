"""Counter-based deterministic RNG for game-state chance events.

The game state stores only two ints (key, counter). Every chance event
derives its outcome purely from (key, counter) and increments the counter,
so clones share identical futures and replays are exact. Implemented as
splitmix64 streams + Fisher-Yates in pure Python: no generator objects to
clone, no numpy per-call construction overhead, platform-stable.
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
    """A decorrelated 64-bit seed from (key, salt); used to give each game in
    a series its own engine seed derived from one persisted series seed."""
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
    """Deterministic Fisher-Yates shuffle of `items` for chance event `counter`.

    Callers must bump the state's rng counter by 1 after use.
    """
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
