"""Exact even-odd region -> convex piece decomposition (production path).

The legacy SDK1 port answers only scalar area/intersection queries and has no
face decomposition.  This module builds an exact vertical decomposition using
the unmodified SDK2 convex Polygon port only, never SDK1:

1. Every input segment is split at all intersection and incidence points,
   producing an exact rational arrangement of atomic segments.  Each segment
   gathers its nodes independently, so the subdivision is invariant to input
   ring/segment ordering.
2. The distinct vertex x-coordinates bound vertical slabs.  Inside a slab no
   segment starts, ends or crosses, so each non-vertical segment spans the whole
   slab and meets every interior vertical line exactly once.
3. On generic vertical lines (the slab midpoint and points just inside each
   boundary), crossing y-values delimit open intervals.  An even-odd horizontal
   ray-cast over the ORIGINAL input segments selects the filled intervals;
   counting source segments (not split atomic pieces) matches the SDK1 evaluator
   and reproduces coincident-edge cancellation.
4. Filled intervals sweep the slab into convex cells.  When the filled-band
   count is identical on both sides the k-th intervals pair by order into
   trapezoids; where a band tapers to a boundary vertex a boundary-level sweep
   collapses that side into a triangle.  Every emitted cell is a positive-area
   convex SDK2 Polygon, and pieces meet only along edges or at points, so the
   pieces of a logical cell have pairwise disjoint interiors.

The independent exact checker in :mod:`gridarchive.decompose_check` (which may
use the retained SDK1 port) verifies area conservation, disjointness,
containment and window transfers; it is not on this write path.  The migration
runs that checker on every converted cell, so any decomposition that is not
exact is rejected loudly rather than stored.  Axis-aligned grids and the
documented even-odd shapes (holes, self-crossings, coincident edges, point
contacts, collinear points) are covered exactly; the order-based band pairing is
exact whenever the filled-band count stays constant across a slab.

Only exact Fraction arithmetic is used; SDK1 is never imported here.
"""
from fractions import Fraction

from .errors import ValidationError
from .vendor.sdk2 import Polygon


def _cross(a, b, c):
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _on_segment(p, a, d):
    if _cross(a, (a[0] + d[0], a[1] + d[1]), p) != 0:
        return False
    t = _dot(_sub(p, a), d)
    return 0 <= t <= _dot(d, d)


def _segments(rings):
    segments = []
    for ring in rings:
        points = [(Fraction(x), Fraction(y)) for x, y in ring]
        for a, b in zip(points, points[1:]):
            if a != b:
                segments.append((a, b))
    if not segments:
        raise ValidationError("region requires at least one non-degenerate edge")
    return segments


def _arrangement(segments):
    """Atomic undirected segments with multiplicity and all vertex x-values.

    Nodes for each segment are gathered independently from every other segment,
    so the resulting subdivision depends only on the geometric set of input
    segments, never on their ordering.  Coincident segments are accumulated
    into one atomic edge through a multiplicity count (even-odd cancellation).
    """
    node_sets = [{a, b} for a, b in segments]
    for i in range(len(segments)):
        a, b = segments[i]
        d = _sub(b, a)
        for j in range(len(segments)):
            if i == j:
                continue
            c, e = segments[j]
            f = _sub(e, c)
            denominator = _cross((0, 0), d, f)
            if denominator:
                t = _cross(a, c, f) / denominator
                u = _cross(a, c, d) / denominator
                if 0 <= t <= 1 and 0 <= u <= 1:
                    node_sets[i].add((a[0] + t * d[0], a[1] + t * d[1]))
            elif _cross(a, c, d) == 0:
                for p in (c, e):
                    if _on_segment(p, a, d):
                        node_sets[i].add(p)
    multiplicity = {}
    x_nodes = set()
    for (a, b), nodes in zip(segments, node_sets):
        d = _sub(b, a)
        scale = _dot(d, d)
        # Deterministic ordering even for coincident/collinear nodes: primary
        # key is projection onto the segment, then full lexicographic point
        # order, so the atomic split never depends on set iteration order.
        ordered = sorted(nodes,
                         key=lambda p: (_dot(_sub(p, a), d) / scale, p))
        for u, v in zip(ordered, ordered[1:]):
            key = tuple(sorted((u, v)))
            multiplicity[key] = multiplicity.get(key, 0) + 1
            x_nodes.add(u[0])
            x_nodes.add(v[0])
    return multiplicity, sorted(x_nodes)


def _y_at(edge, x):
    """Interpolated y of a non-vertical edge at x inside its closed span."""
    a, b = edge
    if a[0] == b[0] or not (min(a[0], b[0]) <= x <= max(a[0], b[0])):
        return None
    if x == a[0]:
        return a[1]
    if x == b[0]:
        return b[1]
    t = (x - a[0]) / (b[0] - a[0])
    return a[1] + t * (b[1] - a[1])


def _crossing_ys(multiplicity, x):
    """Sorted y-values where non-vertical atomic segments meet the line x."""
    values = set()
    for edge in multiplicity:
        y = _y_at(edge, x)
        if y is not None:
            values.add(y)
    return sorted(values)


def _parity(segments, x, y):
    """Even-odd fill at a generic point via a rightward horizontal ray.

    Crossing counts use the ORIGINAL input segments, not the split atomic
    segments: a ray crossing one source edge must count exactly once even
    though arrangement subdivision broke that edge into several atomic pieces.
    Coincident source edges each contribute, reproducing the input even-odd
    rule exactly (matching the SDK1 scalar evaluator).
    """
    crossings = 0
    for a, b in segments:
        if a[1] == b[1]:
            continue
        if (a[1] > y) != (b[1] > y):
            hit = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if hit > x:
                crossings += 1
    return crossings % 2 == 1


def _emit(polygons, points):
    for triangle in _fan_triangulate(points):
        polygons.append(Polygon(triangle))


def _filled_intervals_at(multiplicity, segments, x):
    """Exact filled (low, high) intervals crossing a generic vertical line."""
    levels = _crossing_ys(multiplicity, x)
    return [(lo, hi) for lo, hi in zip(levels, levels[1:])
            if _parity(segments, x, (lo + hi) / 2)]


def _boundary_intervals(multiplicity, segments, sample_x, boundary_x):
    """Filled intervals on a slab boundary, with exact boundary corner heights.

    Fill state and interval structure are read on a generic sample line just
    inside the slab, so only edges that actually continue into the slab count
    (edges ending at the boundary from the other side cannot contaminate the
    levels).  The interval y-limits are then mapped to the boundary line's own
    crossing y-values, giving the exact polygon corners.
    """
    sample_levels = _crossing_ys(multiplicity, sample_x)
    boundary_levels = _crossing_ys(multiplicity, boundary_x)
    intervals = []
    for lo, hi in zip(sample_levels, sample_levels[1:]):
        if not _parity(segments, sample_x, (lo + hi) / 2):
            continue
        # Snap each generic-line limit to the boundary crossing reached by the
        # carrying edges; fall back to the limit itself for a taper vertex.
        lo_edges = [e for e in multiplicity if _y_at(e, sample_x) == lo]
        hi_edges = [e for e in multiplicity if _y_at(e, sample_x) == hi]
        lo_h = sorted({v for e in lo_edges for v in [_y_at(e, boundary_x)]
                       if v is not None})
        hi_h = sorted({v for e in hi_edges for v in [_y_at(e, boundary_x)]
                       if v is not None})
        b_lo = lo_h[0] if lo_h else lo
        b_hi = hi_h[-1] if hi_h else hi
        # A zero-height interval is the boundary trace of a band cancelled by
        # coincident edges of even multiplicity; the band still fills the slab
        # interior up to the boundary, so drop the empty trace and let the
        # sweep inherit the surviving side's full height.
        if b_hi > b_lo:
            intervals.append((b_lo, b_hi))
    return intervals


def _slab_polygons(multiplicity, segments, x0, x1):
    """Convex cells covering the filled area inside one vertical slab.

    When the filled-band count is identical on both boundary-side generic lines
    (the common case, guaranteed when no band tapers to a boundary vertex), the
    k-th intervals pair by order and sweep exact trapezoids.  Where a band
    disappears at a boundary (unequal counts), fall back to the boundary-level
    taper sweep that collapses the missing side onto its real vertex.
    """
    width = x1 - x0
    left_sample_x, right_sample_x = x0 + width / 4, x1 - width / 4
    left_generic = _filled_intervals_at(multiplicity, segments, left_sample_x)
    right_generic = _filled_intervals_at(multiplicity, segments, right_sample_x)
    if len(left_generic) != len(right_generic):
        return _taper_sweep(multiplicity, segments, x0, x1,
                            left_sample_x, right_sample_x)
    left_intervals = _boundary_intervals(
        multiplicity, segments, left_sample_x, x0)
    right_intervals = _boundary_intervals(
        multiplicity, segments, right_sample_x, x1)
    cells = []
    for k in range(len(left_generic)):
        l_interval = left_intervals[k] if k < len(left_intervals) else None
        r_interval = right_intervals[k] if k < len(right_intervals) else None
        if l_interval is not None and r_interval is not None:
            cells.append([(x0, l_interval[0]), (x1, r_interval[0]),
                          (x1, r_interval[1]), (x0, l_interval[1])])
        elif r_interval is not None:
            apex = _taper_apex(_crossing_ys(multiplicity, x0),
                               r_interval[0], r_interval[1])
            cells.append([(x0, apex), (x1, r_interval[0]),
                          (x1, r_interval[1]), (x0, apex)])
        elif l_interval is not None:
            apex = _taper_apex(_crossing_ys(multiplicity, x1),
                               l_interval[0], l_interval[1])
            cells.append([(x0, l_interval[0]), (x1, apex),
                          (x1, apex), (x0, l_interval[1])])
    return cells


def _taper_sweep(multiplicity, segments, x0, x1, left_sample_x, right_sample_x):
    """Boundary-level sweep used when a band tapers away at a boundary."""
    left_intervals = _boundary_intervals(
        multiplicity, segments, left_sample_x, x0)
    right_intervals = _boundary_intervals(
        multiplicity, segments, right_sample_x, x1)
    left_generic = _filled_intervals_at(multiplicity, segments, left_sample_x)
    right_generic = _filled_intervals_at(multiplicity, segments, right_sample_x)
    left_levels = _crossing_ys(multiplicity, x0)
    right_levels = _crossing_ys(multiplicity, x1)
    grid = sorted({y for intervals in (left_intervals, right_intervals)
                   for lo, hi in intervals for y in (lo, hi)})
    cells = []
    for lo, hi in zip(grid, grid[1:]):
        y = (lo + hi) / 2
        left_filled = any(a < y < b for a, b in left_intervals)
        right_filled = any(a < y < b for a, b in right_intervals)
        if not left_filled and not right_filled:
            continue
        if left_filled and right_filled:
            cells.append([(x0, lo), (x1, lo), (x1, hi), (x0, hi)])
        elif right_filled:
            if any(a < y < b for a, b in left_generic):
                cells.append([(x0, lo), (x1, lo), (x1, hi), (x0, hi)])
            else:
                apex = _taper_apex(left_levels, lo, hi)
                cells.append([(x0, apex), (x1, lo), (x1, hi), (x0, apex)])
        else:
            if any(a < (lo + hi) / 2 < b for a, b in right_generic):
                cells.append([(x0, lo), (x1, lo), (x1, hi), (x0, hi)])
            else:
                apex = _taper_apex(right_levels, lo, hi)
                cells.append([(x0, lo), (x1, apex), (x1, apex), (x0, hi)])
    return cells


def _taper_apex(levels, lo, hi):
    """Boundary vertex a one-sided filled band tapers to.

    The apex is constrained to the band's own height span so a cell can never
    extend into an adjacent empty band; it is the boundary crossing nearest to
    the band limit, falling back to the limit itself when no crossing qualifies.
    """
    in_span = [v for v in levels if lo <= v <= hi]
    if not in_span:
        return lo
    return min(in_span, key=lambda v: (min(abs(v - lo), abs(v - hi)), v))


def _fan_triangulate(points):
    """Fan of convex triangles from a simple polygon's first vertex."""
    unique = []
    for point in points:
        if not unique or point != unique[-1]:
            unique.append(point)
    if len(unique) >= 3 and unique[0] == unique[-1]:
        unique.pop()
    if len(set(unique)) < 3:
        return []
    try:
        Polygon(unique)
        return [unique]
    except ValidationError:
        return [[unique[0], unique[i], unique[i + 1]]
                for i in range(1, len(unique) - 1)]


def decompose(rings):
    """Decompose legacy even-odd rings into validated convex Polygon pieces."""
    segments = _segments(rings)
    multiplicity, x_nodes = _arrangement(segments)
    polygons = []
    for left, right in zip(x_nodes, x_nodes[1:]):
        for cell in _slab_polygons(multiplicity, segments, left, right):
            _emit(polygons, cell)
    if not polygons:
        raise ValidationError("cell has zero filled area")
    return polygons
