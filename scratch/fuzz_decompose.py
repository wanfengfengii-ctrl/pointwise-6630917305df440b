"""Randomized fuzz validation of decompose against the SDK1 oracle."""
import random
from fractions import Fraction as F

from gridarchive.decompose import decompose
from gridarchive.vendor.sdk1 import Region
from gridarchive.vendor.sdk2 import Polygon
from gridarchive.samples import rectangle


def rand_point(rng, bound=4):
    return (F(rng.randint(-bound, bound), rng.choice([1, 1, 1, 2, 3])),
            F(rng.randint(-bound, bound), rng.choice([1, 1, 1, 2, 3])))


def rand_ring(rng):
    n = rng.randint(3, 6)
    pts = [rand_point(rng) for _ in range(n)]
    pts.append(pts[0])
    return pts


def rect_ring(rng):
    x1, x2 = sorted((rng.randint(-3, 3), rng.randint(-3, 3)))
    y1, y2 = sorted((rng.randint(-3, 3), rng.randint(-3, 3)))
    if x1 == x2:
        x2 += 1
    if y1 == y2:
        y2 += 1
    return rectangle(x1, y1, x2, y2)


rng = random.Random(20261116)
windows = [rectangle(-4, -4, 4, 4), rectangle(-2, -1, 2, 3),
           rectangle("1/2", "1/3", 3, 2), rectangle(-3, 0, 1, 1),
           rectangle(0, 0, 1, 1)]
window_regions = [Region([w]) for w in windows]
window_polys = [Polygon(w) for w in windows]

runs = 0
zero_cases = 0
for trial in range(400):
    ring_count = rng.randint(1, 3)
    rings = [rng.choice([rect_ring, rand_ring])(rng) for _ in range(ring_count)]
    ref = Region(rings)
    ref_area = ref.area()
    try:
        pieces = decompose(rings)
    except Exception as exc:
        if ref_area == 0:
            zero_cases += 1
            continue
        raise AssertionError(("unexpected failure", rings, exc))
    if ref_area == 0:
        raise AssertionError(("produced pieces for zero area", rings))
    runs += 1
    total = sum((p.area() for p in pieces), F(0))
    assert total == ref_area, ("area", rings, total, ref_area)
    for i, p in enumerate(pieces):
        assert isinstance(p, Polygon) and p.area() > 0
        for q in pieces[i + 1:]:
            assert p.intersection_area(q) == 0, ("disjoint", rings)
    for wpoly, wregion in zip(window_polys, window_regions):
        got = sum((p.intersection_area(wpoly) for p in pieces), F(0))
        want = ref.intersection_area(wregion)
        assert got == want, ("window", rings, got, want)

print(f"fuzz ok: {runs} positive cases, {zero_cases} zero-area rejections")
