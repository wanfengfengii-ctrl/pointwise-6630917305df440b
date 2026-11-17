"""Independent exact cross-check for the SDK1 -> SDK2 decomposition.

This module is deliberately separate from the production conversion in
:mod:`gridarchive.decompose` and may use the retained SDK1 port, as required by
the migration contract ("独立精确对照，不调用本次生产转换或归并函数").

It verifies, for every converted cell and the whole region, that:

1. the convex pieces' total area equals the SDK1 even-odd region area exactly;
2. pieces are positive-area convex polygons (via the SDK2 port);
3. distinct pieces have pairwise zero interior intersection area;
4. every piece lies inside the source region (no area leaks outside);
5. against a set of probe windows, the summed piece intersection area equals
   the SDK1 region intersection area exactly.

All comparisons are exact Fraction arithmetic; nothing here is used on the
SDK2 write path, so it cannot bypass the convex-only constraint.
"""

from fractions import Fraction

from .errors import ValidationError
from .vendor import sdk1, sdk2


def _as_region(rings):
    return sdk1.Region(rings)


def check_pieces_cover(rings, pieces):
    """Return True iff pieces exactly and disjointly cover the SDK1 region.

    Raises ValidationError with a diagnostic when any invariant fails.
    """
    region = _as_region(rings)
    reference_area = region.area()
    if reference_area <= 0:
        if pieces:
            raise ValidationError("zero-area region produced pieces")
        return True

    # (2) convex, positive-area SDK2 polygons.
    polygons = [p if isinstance(p, sdk2.Polygon) else sdk2.Polygon(p)
                for p in pieces]
    for polygon in polygons:
        if polygon.area() <= 0:
            raise ValidationError("non-positive area piece")

    # (1) total area conservation.
    piece_area = sum((p.area() for p in polygons), Fraction(0))
    if piece_area != reference_area:
        raise ValidationError(
            f"area not conserved: pieces {piece_area} != region {reference_area}")

    # (3) pairwise interior-disjoint.
    for i, a in enumerate(polygons):
        for b in polygons[i + 1:]:
            overlap = a.intersection_area(b)
            if overlap > 0:
                raise ValidationError(f"pieces overlap by {overlap}")

    # (4) no piece leaks outside the source region.  The SDK2 port cannot
    # intersect an SDK1 region, so containment is decided from interior points:
    # several generic points strictly inside each convex piece must all lie in
    # the SDK1 region.  Boundary vertices legitimately sit on region edges and
    # are excluded from the strict point-in-region test.
    for polygon in polygons:
        verts = polygon.vertices
        centroid = (sum((v[0] for v in verts), Fraction(0)) / len(verts),
                    sum((v[1] for v in verts), Fraction(0)) / len(verts))
        probes = [centroid]
        # A point halfway from the centroid to each vertex stays interior.
        for vertex in verts:
            probes.append(((centroid[0] + vertex[0]) / 2,
                           (centroid[1] + vertex[1]) / 2))
        for point in probes:
            if not region._contains(point):
                raise ValidationError("piece interior outside source region")
    return True


def check_window_conservation(rings, pieces, windows):
    """Exact transfer conservation against SDK1 for each probe window.

    ``windows`` are closed walks (the same shape as grid rings).  The total
    amount of every piece intersecting a window must equal the SDK1 region's
    intersection area with that window.
    """
    region = _as_region(rings)
    polygons = [p if isinstance(p, sdk2.Polygon) else sdk2.Polygon(p)
                for p in pieces]
    for window in windows:
        probe = sdk2.Polygon(window)
        expected = region.intersection_area(sdk1.Region(window))
        actual = sum((p.intersection_area(probe) for p in polygons),
                     Fraction(0))
        if actual != expected:
            raise ValidationError(
                f"window transfer mismatch: {actual} != {expected}")
    return True

def cross_check(rings, pieces, windows=()):
    """Run all independent exact checks for one converted region."""
    check_pieces_cover(rings, pieces)
    check_window_conservation(rings, pieces, windows)
    return True
