"""SDK2 convex-piece business storage and remapping.

This is the convex-representation counterpart of :mod:`gridarchive.grids`,
:mod:`gridarchive.fields`, :mod:`gridarchive.jobs` and
:mod:`gridarchive.remap`.  All geometry flows through SDK2 Polygon pieces; the
SDK1 scalar port is never used on this path.

A *logical cell* (a grid revision id plus a cell key) holds an ordered,
interior-disjoint collection of convex Polygon pieces.  Pieces are storage and
remapping participants but never standalone observational or task identities.
"""

import json

from .codec import dump, unpack
from .errors import NotFound, ValidationError
from .vendor.sdk2 import Polygon
from .v2_schema import V2_VERSION


def require_v2(store):
    if store.db.execute(
            "SELECT value FROM metadata WHERE key='version'").fetchone()[0] \
            != V2_VERSION:
        raise ValidationError("archive is not at SDK2 storage version 2")


def _load_polygon(body):
    payload = json.loads(body)
    vertices = tuple((unpack(p[0]), unpack(p[1]))
                     for p in payload["vertices"])
    return Polygon(vertices)


class V2Archive:
    """Version-2 archive reading and writing convex-piece geometry."""

    def __init__(self, store):
        self._store = store

    @property
    def db(self):
        return self._store.db

    # ---- grid revisions -------------------------------------------------
    def read_pieces(self, revision, cell):
        """Return the real SDK2 Polygon pieces stored for a logical cell."""
        rows = self.db.execute(
            "SELECT body FROM v2_pieces WHERE grid_id=? AND cell_key=?"
            " ORDER BY ordinal", (revision, cell)).fetchall()
        if not rows:
            raise NotFound("pieces")
        return [_load_polygon(body) for body, in rows]

    def piece_cells(self, revision):
        """Return the distinct logical cell keys held by a revision."""
        return [row[0] for row in self.db.execute(
            "SELECT DISTINCT cell_key FROM v2_pieces WHERE grid_id=?"
            " ORDER BY cell_key", (revision,)).fetchall()]

    def publish_pieces(self, name, request, cells, pieces_by_cell):
        """Publish a new revision built from SDK2 Polygon pieces.

        ``cells`` maps each cell key to the validated non-empty Polygon list.
        Piece ordering is preserved; a request replay returns the original
        revision without creating new identities.
        """
        require_v2(self._store)
        payload = self._piece_payload(name, request, cells, pieces_by_cell)
        prior = self.db.execute(
            "SELECT revision FROM v2_grid_receipts WHERE request=?",
            (request,)).fetchone()
        if prior is not None:
            return prior[0]
        with self._store.transaction("v2.grid.before_commit"):
            revision = self._next_grid_id()
            self.db.execute(
                "INSERT INTO v2_grids(id,name,kind,body) VALUES(?,?,?,?)",
                (revision, name, "grid", dump(payload)))
            for cell, polygons in pieces_by_cell.items():
                self._validate_native(polygons)
                for ordinal, polygon in enumerate(polygons):
                    self.db.execute(
                        "INSERT INTO v2_pieces(grid_id,cell_key,ordinal,body)"
                        " VALUES(?,?,?,?)",
                        (revision, cell, ordinal,
                         dump(_polygon_payload(polygon))))
            self.db.execute(
                "INSERT INTO v2_grid_receipts(request,payload,revision)"
                " VALUES(?,?,?)", (request, dump(payload), revision))
            self.db.execute(
                "INSERT OR REPLACE INTO v2_heads(name,revision) VALUES(?,?)",
                (name, revision))
        return revision

    def head(self, name):
        row = self.db.execute(
            "SELECT revision FROM v2_heads WHERE name=?", (name,)).fetchone()
        if row is None:
            from .errors import NotFound
            raise NotFound("grid name")
        return row[0]

    def _validate_native(self, polygons):
        if not isinstance(polygons, (list, tuple)) or not polygons:
            raise ValidationError("native pieces must be a non-empty list")
        for polygon in polygons:
            if not isinstance(polygon, Polygon):
                raise ValidationError("native input requires SDK2 Polygon")
            if polygon.area() <= 0:
                raise ValidationError("piece must have positive area")
        for i, a in enumerate(polygons):
            for b in polygons[i + 1:]:
                if a.intersection_area(b) > 0:
                    raise ValidationError(
                        "pieces of one cell must have disjoint interiors")

    def _piece_payload(self, name, request, cells, pieces_by_cell):
        return {"name": name, "request": request,
                "cells": {cell: [_polygon_payload(p)
                                 for p in pieces_by_cell[cell]]
                          for cell in pieces_by_cell}}

    def _next_grid_id(self):
        row = self.db.execute(
            "SELECT COALESCE(MAX(id),0)+1 FROM v2_grids").fetchone()
        return row[0]

    # ---- receipts -------------------------------------------------------
    def grid_receipt(self, request):
        row = self.db.execute(
            "SELECT revision FROM v2_grid_receipts WHERE request=?",
            (request,)).fetchone()
        if row is None:
            raise NotFound("grid receipt")
        return row[0]

    def field_receipt(self, request):
        row = self.db.execute(
            "SELECT version FROM v2_field_receipts WHERE request=?",
            (request,)).fetchone()
        if row is None:
            raise NotFound("field receipt")
        return row[0]


def _polygon_payload(polygon):
    """Canonical JSON-friendly rational representation of an SDK2 polygon."""
    from .codec import pack
    return {"vertices": [[pack(v[0]), pack(v[1])]
                         for v in polygon.vertices]}
