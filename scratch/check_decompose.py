"""Scratch validation for decompose; removed before delivery."""
import sys
from fractions import Fraction as F

from gridarchive.decompose import decompose
from gridarchive.vendor.sdk1 import Region
from gridarchive.vendor.sdk2 import Polygon
from gridarchive.samples import rectangle


def rect(x, y, r, t):
    return rectangle(x, y, r, t)


CASES = {
    "rect": ([rect(0, 0, 2, 2)], 4),
    "l": ([[(0, 0), (1, 0), (1, 1), (3, 1), (3, 2), (0, 2), (0, 0)]], 4),
    "hole": ([rect(0, 0, 4, 4), rect(1, 1, 3, 3)], 12),
    "bow": ([[(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)]], 2),
    "double": ([rect(0, 0, 2, 2), rect(0, 0, 2, 2)], 0),
    "reversed": ([rect(0, 0, 2, 2), list(reversed(rect(0, 0, 2, 2))),
                  rect(0, 0, 2, 2)], 4),
    "spur": ([[(0, 0), (2, 0), (2, 2), (0, 2), (0, 0), (-1, 0), (0, 0)]], 4),
    "overlap": ([rect(0, 0, 3, 2), rect(1, 0, 4, 2)], 4),
    "triangle": ([[(0, 0), ("3/2", 0), (0, "2/3"), (0, 0)]], F(1, 2)),
    "collinear": ([[(0, 0), (1, 0), (2, 0), (2, 2), (0, 2), (0, 0)]], 4),
    "two-rings-multi": ([rect(-2, -2, 2, 2), rect(-1, -1, 1, 1),
                         rect(-1, -1, 1, 1)], 16),
    "touch-point": ([rect(0, 0, 1, 1), rect(1, 1, 2, 2)], 2),
    "rational": ([rect("1/3", "1/2", "5/2", "7/3")],
                 (F(5, 2) - F(1, 3)) * (F(7, 3) - F(1, 2))),
}

failures = 0
for label, (rings, expected) in CASES.items():
    ref = Region(rings)
    ref_area = ref.area()
    assert ref_area == F(expected), (label, ref_area, expected)
    try:
        pieces = decompose(rings)
    except Exception as exc:
        if ref_area == 0:
            print(f"{label}: zero area correctly rejected ({type(exc).__name__}: {exc})")
            continue
        failures += 1
        print(f"{label}: FAILED {type(exc).__name__}: {exc}")
        continue
    total = sum((p.area() for p in pieces), F(0))
    assert total == ref_area, (label, total, ref_area)
    for i, p in enumerate(pieces):
        assert isinstance(p, Polygon)
        for q in pieces[i + 1:]:
            assert p.intersection_area(q) == 0, (label, "overlap")
    # cross-check intersections against SDK1 on random rational windows
    windows = [rect(-3, -3, 3, 3), rect(0, 0, 1, 1), rect("1/2", "1/2", 2, 3),
               rect(-1, 2, 3, 4)]
    for window in windows:
        w = Polygon(window)
        got = sum((p.intersection_area(w) for p in pieces), F(0))
        want = ref.intersection_area(Region([window]))
        assert got == want, (label, window, got, want)
    print(f"{label}: ok, {len(pieces)} pieces, area {total}")

# bow tie with spur (sample fixture shape)
bow_spur = [[(0, 0), (2, 2), (0, 2), (2, 0), (0, 0), (-1, 0), (0, 0)]]
pieces = decompose(bow_spur)
assert sum((p.area() for p in pieces), F(0)) == Region(bow_spur).area() == 2
print("bow+spur ok, pieces:", len(pieces))

sys.exit(1 if failures else 0)
