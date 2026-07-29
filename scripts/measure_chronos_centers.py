"""
Measurement script: recovers the 18 chronos ring centres from the printed board art and
reports how far CHRONOS_CENTERS sits from them.

This is where the constants in zutomayo/ui/board_renderer.py come from. The calibration
scripts show you whether the coin looks right; this one tells you by how much, so the
constants can be re-derived rather than trusted.

Two glyph kinds need two different fits:

  day suns          A solid disc surrounded by detached ray triangles, so the disc is its
                    own connected component and is symmetric. Its centroid is its centre.

  night moons       A phase, so the lit pixels are not symmetric about the slot and the
                    centroid sits wherever the terminator leaves it. What is measured
                    instead is the moon's disc, by fitting a circle of KNOWN radius to the
                    glyph's outer limb. Holding the radius fixed is the point: a gibbous
                    moon's terminator is an ellipse arc of similar curvature, and a
                    free-radius fit happily lands on it instead of on the limb.

  night new moons   Slots 0 and 8 are dashed rings with no disc at all, so those two are a
                    least-squares circle through the dash centroids.

There is a second trap on top of the fixed radius, and it is the one that actually shipped a
wrong answer. On the two gibbous phases the terminator is close enough to a circle of the
disc's own radius that placing the disc on EITHER side of the glyph scores almost the same:
142 inliers against 141 on slot 3. The winner there is decided by noise, so the score cannot
be trusted to choose. Where the runner-up comes within AMBIGUOUS_MARGIN of the winner, this
script ignores the score and asks the ring instead -- it interpolates the slot's angle about
the rotational centre from its nearest unambiguous neighbours and keeps whichever candidate
is closer. That separates them cleanly, 0.4 degrees against 3.9. Both candidates are printed
whenever this happens, so the choice is visible rather than silent.

Scoring. For a candidate centre, take the outermost glyph pixel in each 10-degree sector.
On a correct centre those outer radii form a flat plateau at the disc radius over the whole
limb. The score is how many sectors sit on that plateau. The reference radius is the 90th
percentile of the sector radii, NOT the median -- on a gibbous phase the terminator drags
the median well off the limb and a median-based score rates a correct centre as a failure.

Expect the day slots to score 33-36 out of 36. The night slots score lower, and legitimately
so: a crescent has no glyph pixels at all across a third of its sectors, and a gibbous
terminator occupies half of them. For the night half the discriminator is the fitted disc
radius agreeing across all seven moons, not the raw sector count.

Run from project root:
python scripts/measure_chronos_centers.py

Prints only; writes no files.
"""

import sys
from collections import deque
from pathlib import Path


# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import numpy as np
from PIL import Image

from engine_alpha.battle import CHRONOS_SIZE, NIGHT_END
from zutomayo.ui.board_renderer import (
    BOARD_NATIVE,
    CHRONOS_CENTERS,
    DAY_CHRONOS_CENTERS,
    MIRROR_X_SUM,
    MIRROR_Y_SUM,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The mat sits at ~53 and the printed glyphs at ~75-79, so anything above this is art.
GLYPH_THRESHOLD = 65

# Radius of a night moon's disc, from the slot 4 full moon, whose limb is unbroken and so
# can be fitted without assuming a radius first.
MOON_DISC_RADIUS = 53.45

# A glyph big enough to be a sun or a moon rather than one dash of a new moon ring.
SOLID_GLYPH_MIN_PIXELS = 800

# How far a candidate centre is searched, and how finely. The search is anchored on the
# glyph's own bounding box rather than on the current constant, so a constant that is
# already wrong cannot pull the window off the answer.
SEARCH_RADIUS = 40
SEARCH_STEP = 0.5

# A boundary point counts as on the limb within this distance of the fitted circle.
LIMB_TOLERANCE = 1.5

# Two optima closer together than this are the same peak sampled twice, not rivals.
PEAK_SEPARATION = 12

# If the runner-up scores at least this fraction of the winner, the two sides of the glyph
# fit equally well and the score is not evidence. Resolve by ring angle instead.
AMBIGUOUS_MARGIN = 0.9

SECTOR_DEGREES = 10


def _label_components(mask: np.ndarray) -> list[np.ndarray]:
    """Every 8-connected run of set pixels, as an array of (row, column) pairs."""
    height, width = mask.shape
    labels = np.zeros((height, width), np.int32)
    components = []

    for start_row in range(height):
        for start_column in range(width):
            if not mask[start_row, start_column] or labels[start_row, start_column]:
                continue
            label = len(components) + 1
            labels[start_row, start_column] = label
            queue = deque([(start_row, start_column)])
            pixels = []
            while queue:
                row, column = queue.popleft()
                pixels.append((row, column))
                for row_step in (-1, 0, 1):
                    for column_step in (-1, 0, 1):
                        next_row, next_column = row + row_step, column + column_step
                        if not (0 <= next_row < height and 0 <= next_column < width):
                            continue
                        if not mask[next_row, next_column]:
                            continue
                        if labels[next_row, next_column]:
                            continue
                        labels[next_row, next_column] = label
                        queue.append((next_row, next_column))
            components.append(np.array(pixels))

    return components


def _boundary(component: np.ndarray) -> np.ndarray:
    """The component's edge pixels, as (x, y) pairs."""
    filled = set(map(tuple, component.tolist()))
    edge = [
        (column, row)
        for row, column in component.tolist()
        if any(
            (row + row_step, column + column_step) not in filled
            for row_step, column_step in ((1, 0), (-1, 0), (0, 1), (0, -1))
        )
    ]
    return np.array(edge, float)


def _fit_circle(points: np.ndarray) -> tuple[float, float, float]:
    """Least-squares circle through (x, y) points, by the algebraic (Kasa) method."""
    x, y = points[:, 0], points[:, 1]
    design = np.c_[x, y, np.ones(len(points))]
    solution, *_ = np.linalg.lstsq(design, x ** 2 + y ** 2, rcond=None)
    center_x, center_y = solution[0] / 2, solution[1] / 2
    radius = np.sqrt(solution[2] + center_x ** 2 + center_y ** 2)
    return center_x, center_y, radius


def _sector_radii(component: np.ndarray, center_x: float, center_y: float) -> np.ndarray:
    """The outermost glyph radius in each sector; NaN where the sector holds no glyph."""
    x = component[:, 1].astype(float)
    y = component[:, 0].astype(float)
    angles = np.degrees(np.arctan2(y - center_y, x - center_x)) % 360
    radii = np.hypot(x - center_x, y - center_y)

    outer = []
    for start in range(0, 360, SECTOR_DEGREES):
        in_sector = (angles >= start) & (angles < start + SECTOR_DEGREES)
        outer.append(radii[in_sector].max() if in_sector.any() else np.nan)
    return np.array(outer)


def _limb_score(component: np.ndarray, center_x: float, center_y: float) -> tuple[int, int, float]:
    """How many sectors sit on the outer radius plateau, and where that plateau is."""
    outer = _sector_radii(component, center_x, center_y)
    present = outer[~np.isnan(outer)]
    if not len(present):
        return 0, 0, float('nan')
    # The 90th percentile, not the median: on a gibbous phase the terminator fills half the
    # sectors at a much smaller radius and would drag a median off the limb entirely.
    plateau = np.percentile(present, 90)
    return int((np.abs(present - plateau) < LIMB_TOLERANCE).sum()), len(present), float(plateau)


def _fit_disc_by_limb(component: np.ndarray) -> list[tuple[int, float, float]]:
    """The two best-separated discs of MOON_DISC_RADIUS matching the glyph's outer limb.

    Returns (score, x, y) best first. A gibbous phase yields two near-tied entries, one per
    side of the glyph; every other phase yields one clear winner. The caller decides.
    """
    x = component[:, 1].astype(float)
    y = component[:, 0].astype(float)
    edge = _boundary(component)
    near_x = (component[:, 1].min() + component[:, 1].max()) / 2
    near_y = (component[:, 0].min() + component[:, 0].max()) / 2

    scored = []
    for center_y in np.arange(near_y - SEARCH_RADIUS, near_y + SEARCH_RADIUS + 0.01, SEARCH_STEP):
        for center_x in np.arange(near_x - SEARCH_RADIUS, near_x + SEARCH_RADIUS + 0.01, SEARCH_STEP):
            # The disc has to contain the glyph. Without this a fit can sit on the
            # terminator with half the moon hanging outside the circle.
            contained = np.hypot(x - center_x, y - center_y) <= MOON_DISC_RADIUS + LIMB_TOLERANCE
            if contained.mean() < 0.999:
                continue
            distance = np.abs(np.hypot(edge[:, 0] - center_x, edge[:, 1] - center_y) - MOON_DISC_RADIUS)
            scored.append((int((distance < LIMB_TOLERANCE).sum()), center_x, center_y))

    scored.sort(reverse=True)
    peaks: list[tuple[int, float, float]] = []
    for score, center_x, center_y in scored:
        if all(np.hypot(center_x - px, center_y - py) > PEAK_SEPARATION for _, px, py in peaks):
            peaks.append((score, center_x, center_y))
        if len(peaks) == 2:
            break
    return peaks


def _ring_angle(point: tuple[float, float]) -> float:
    """A slot's angle about the board art's rotational centre, in degrees."""
    return float(np.degrees(np.arctan2(point[1] - MIRROR_Y_SUM / 2, point[0] - MIRROR_X_SUM / 2)))


def _fit_dashed_ring(components: list[np.ndarray], near_x: int, near_y: int):
    """The circle through a new moon's dash centroids, and its worst residual."""
    centroids = []
    for component in components:
        if not 25 < len(component) < 200:
            continue
        centroid_x = component[:, 1].mean()
        centroid_y = component[:, 0].mean()
        if abs(centroid_x - near_x) > 70 or abs(centroid_y - near_y) > 70:
            continue
        # The HP track's dashes are long and thin and sit near slots 0 and 8. Dropping
        # anything that is not roughly square keeps them out of the fit.
        width = np.ptp(component[:, 1]) + 1
        height = np.ptp(component[:, 0]) + 1
        if not 0.55 < width / height < 1.8:
            continue
        centroids.append((centroid_x, centroid_y))

    if len(centroids) < 6:
        return None
    points = np.array(centroids)
    center_x, center_y, radius = _fit_circle(points)
    residual = np.abs(np.hypot(points[:, 0] - center_x, points[:, 1] - center_y) - radius).max()
    return center_x, center_y, radius, len(points), residual


def _solid_glyph_near(components: list[np.ndarray], near_x: int, near_y: int):
    """The largest solid glyph whose centroid lies near a slot, or None for a new moon."""
    candidates = [
        component
        for component in components
        if len(component) >= SOLID_GLYPH_MIN_PIXELS
        and abs(component[:, 1].mean() - near_x) < 60
        and abs(component[:, 0].mean() - near_y) < 60
    ]
    return max(candidates, key=len) if candidates else None


def main() -> None:
    board = Image.open(PROJECT_ROOT / 'zutomayo/images/board.png').convert('L')
    if board.size != (BOARD_NATIVE, BOARD_NATIVE):
        raise SystemExit(f'expected a {BOARD_NATIVE}x{BOARD_NATIVE} board, got {board.size}')

    print(f'Board: {board.size[0]}x{board.size[1]} native, glyph threshold {GLYPH_THRESHOLD}')
    print('Labelling connected components (a few seconds)...')
    components = _label_components(np.array(board) > GLYPH_THRESHOLD)
    print(f'{len(components)} components found')
    print()

    rotational_x = MIRROR_X_SUM / 2
    rotational_y = MIRROR_Y_SUM / 2

    # Pass one: fit every glyph. Ambiguous slots are left unresolved, because resolving them
    # needs the angles of the slots either side, which are not all known yet.
    measured: dict[int, tuple[float, float]] = {}
    glyphs: dict[int, np.ndarray] = {}
    notes: dict[int, str] = {}
    rivals: dict[int, tuple[int, float, float]] = {}

    for slot in range(CHRONOS_SIZE):
        constant_x, constant_y = CHRONOS_CENTERS[slot]
        half = 'night' if slot <= NIGHT_END else 'day'
        glyph = _solid_glyph_near(components, constant_x, constant_y)

        if glyph is None:
            fit = _fit_dashed_ring(components, constant_x, constant_y)
            if fit is None:
                notes[slot] = 'could not find a glyph or a dashed ring'
                continue
            center_x, center_y, radius, dash_count, residual = fit
            measured[slot] = (center_x, center_y)
            notes[slot] = f'new moon, {dash_count} dashes, max residual {residual:.2f} px'
            continue

        glyphs[slot] = glyph
        if half == 'day':
            # A sun's disc is symmetric, so its centroid is its centre.
            measured[slot] = (glyph[:, 1].mean(), glyph[:, 0].mean())
            notes[slot] = f'sun, {len(glyph)} px'
            continue

        peaks = _fit_disc_by_limb(glyph)
        if not peaks:
            notes[slot] = 'limb fit failed'
            continue
        lit = len(glyph) / (np.pi * MOON_DISC_RADIUS ** 2) * 100
        if len(peaks) > 1 and peaks[1][0] >= peaks[0][0] * AMBIGUOUS_MARGIN:
            # Both sides of the glyph fit. Hold it back for the ring to decide.
            rivals[slot] = peaks
            notes[slot] = f'moon, {lit:.0f}% lit, AMBIGUOUS'
        else:
            measured[slot] = (peaks[0][1], peaks[0][2])
            notes[slot] = f'moon, {lit:.0f}% lit'

    # Pass two: for each ambiguous slot, predict where the ring puts it from the nearest
    # slots that were not ambiguous, and keep the candidate closest to that prediction. The
    # scores are tied to within noise, so the ring is the only evidence that separates them.
    for slot, peaks in rivals.items():
        before = max((s for s in measured if s < slot), default=None)
        after = min((s for s in measured if s > slot), default=None)
        if before is None or after is None:
            measured[slot] = (peaks[0][1], peaks[0][2])
            notes[slot] += ', no neighbours to resolve against, took the higher score'
            continue
        span = after - before
        predicted = (
            _ring_angle(measured[before])
            + (_ring_angle(measured[after]) - _ring_angle(measured[before]))
            * (slot - before) / span
        )
        print(f'Slot {slot} fits equally well on either side of its glyph. '
              f'Slots {before} and {after} put it at {predicted:.2f} deg:')
        ranked = sorted(peaks, key=lambda p: abs(_ring_angle((p[1], p[2])) - predicted))
        for score, center_x, center_y in peaks:
            angle = _ring_angle((center_x, center_y))
            chosen = ' <- taken' if (score, center_x, center_y) == ranked[0] else ''
            print(f'    ({center_x:7.2f},{center_y:7.2f})  {score:4d} limb inliers  '
                  f'angle {angle:8.2f}  off by {abs(angle - predicted):5.2f} deg{chosen}')
        measured[slot] = (ranked[0][1], ranked[0][2])
    if rivals:
        print()

    header = (
        f"{'slot':<5} {'half':<6} {'constant':<14} {'measured':<18} "
        f"{'delta':<16} {'err':>6} {'discR':>7} {'limb':>8}  glyph"
    )
    print(header)
    print('-' * len(header))

    for slot in range(CHRONOS_SIZE):
        if slot not in measured:
            print(f'{slot:<5} {notes.get(slot, "not measured")}')
            continue
        constant_x, constant_y = CHRONOS_CENTERS[slot]
        half = 'night' if slot <= NIGHT_END else 'day'
        center_x, center_y = measured[slot]
        if slot in glyphs:
            on_limb, sectors, plateau = _limb_score(glyphs[slot], center_x, center_y)
            disc = f'{plateau:7.2f}'
            limb = f'{on_limb:>3}/{sectors:<3}'
        else:
            disc = limb = ''
        delta_x = center_x - constant_x
        delta_y = center_y - constant_y
        print(
            f'{slot:<5} {half:<6} {str((constant_x, constant_y)):<14} '
            f'({center_x:8.2f},{center_y:7.2f}) ({delta_x:+6.2f},{delta_y:+6.2f}) '
            f'{np.hypot(delta_x, delta_y):6.2f} {disc:>7} {limb:>8}  {notes[slot]}'
        )

    print()
    print(f'Ring geometry about the rotational centre ({rotational_x}, {rotational_y})')
    print(f"{'slot':<5} {'radius':>8} {'angle':>9} {'step':>8}")
    previous_angle = None
    for slot in range(CHRONOS_SIZE):
        if slot not in measured:
            continue
        center_x, center_y = measured[slot]
        radius = np.hypot(center_x - rotational_x, center_y - rotational_y)
        angle = np.degrees(np.arctan2(center_y - rotational_y, center_x - rotational_x))
        # Slot 9 starts the day arc, so the 8->9 gap is not a step along either ring.
        step = '' if previous_angle is None or slot == min(DAY_CHRONOS_CENTERS) else f'{angle - previous_angle:8.2f}'
        print(f'{slot:<5} {radius:8.2f} {angle:9.2f} {step:>8}')
        previous_angle = angle

    print()
    print(f'Night ring mirror symmetry about x = {rotational_x}')
    for left, right in ((0, 8), (1, 7), (2, 6), (3, 5)):
        if left not in measured or right not in measured:
            continue
        left_x, left_y = measured[left]
        right_x, right_y = measured[right]
        print(
            f'  slots {left}/{right}: x sum {left_x + right_x:8.2f} '
            f'(a perfect mirror about {rotational_x} would be {rotational_x * 2:.2f}), '
            f'y difference {right_y - left_y:+6.2f}'
        )

    worst = max(
        (np.hypot(measured[slot][0] - CHRONOS_CENTERS[slot][0],
                  measured[slot][1] - CHRONOS_CENTERS[slot][1]), slot)
        for slot in measured
    )
    print()
    print(f'Worst slot: {worst[1]}, {worst[0]:.2f} px from its constant')


if __name__ == '__main__':
    main()
