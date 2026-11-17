"""Atomic SDK1 -> SDK2 storage migration.

``upgrade(path, fault=None)`` publishes, in a single SQLite transaction, the
complete v2 business storage for every SDK1 grid revision while leaving the
SDK1 tables untouched as read-only audit data:

* every geometric revision, its head, observation versions, fixed task bindings
  and all historical results/receipts are carried across with their original
  ids and raw request spellings;
* each legacy cell's even-odd rings are converted to exact convex SDK2 pieces;
* the independent checker validates each converted region before commit.

A fault raised before commit leaves the original database fully usable; a
retry after success is a no-op that adds no identities, receipts or results.
"""

import sqlite3

from .codec import dump, pack
from .decompose import decompose
from .decompose_check import cross_check
from .v2_schema import V2_SCHEMA, V2_VERSION


def _read_legacy_grids(db):
    return db.execute("SELECT id,name,body FROM grids ORDER BY id").fetchall()


def _legacy_cell_rings(db, grid_id):
    row = db.execute("SELECT body FROM grids WHERE id=?", (grid_id,)).fetchone()
    import json
    return json.loads(row[0])


def _polygon_payload(polygon):
    """JSON-serialisable canonical representation of an SDK2 polygon.

    Vertices are stored as nested [numerator, denominator] rational pairs so the
    exact Fraction geometry round-trips without float loss.
    """
    return {"vertices": [[pack(v[0]), pack(v[1])]
                         for v in polygon.vertices]}


def _convert_grid(db, grid_id, name, raw_body):
    """Convert one grid revision's cells into ordered SDK2 piece rows."""
    cells = _legacy_cell_rings(db, grid_id)
    piece_rows = []
    for cell, rings in cells.items():
        pieces = decompose(rings)
        # Independent exact verification before anything is written.
        cross_check(rings, pieces)
        for ordinal, polygon in enumerate(pieces):
            piece_rows.append((grid_id, cell, ordinal,
                               dump(_polygon_payload(polygon))))
    return piece_rows


def upgrade(path, fault=None):
    """Migrate the SDK1 database at ``path`` to the SDK2 target storage.

    ``fault`` is an optional callable invoked with an event name inside the
    migration transaction before commit (``migration.before_commit``), matching
    the existing fault-injection convention.  A raised exception rolls the whole
    migration back.
    """
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            already_migrated = db.execute(
                "SELECT 1 FROM v2_grids LIMIT 1").fetchone() if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_grids'"
            ).fetchone() else None
            if already_migrated is not None:
                return  # already migrated with data: idempotent no-op
            # Create tables with individual statements (not executescript, which
            # would force an implicit commit) so the DDL participates in the
            # transaction and a fault rolls the schema back too.
            for statement in V2_SCHEMA.split(";"):
                statement = statement.strip()
                if statement:
                    db.execute(statement)
            _publish_v2(db, fault)
            db.execute(
                "UPDATE metadata SET value=? WHERE key='version'",
                (V2_VERSION,))
    finally:
        db.close()


def _publish_v2(db, fault):
    legacy = _read_legacy_grids(db)
    # Phase 1: grids, their convex pieces, and the grid namespace (heads and
    # grid receipts).  Every converted region is independently checked first.
    for grid_id, name, body in legacy:
        db.execute("INSERT INTO v2_grids(id,name,kind,body) VALUES(?,?,?,?)",
                   (grid_id, name, "grid", body))
        for piece_row in _convert_grid(db, grid_id, name, body):
            db.execute(
                "INSERT INTO v2_pieces(grid_id,cell_key,ordinal,body)"
                " VALUES(?,?,?,?)", piece_row)
        _copy_grid_namespace(db, grid_id)
    # Phase 2: observation fields and their receipts.
    for field_id, grid, body in db.execute(
            "SELECT id,grid,body FROM fields ORDER BY id").fetchall():
        db.execute("INSERT INTO v2_fields(id,grid,body) VALUES(?,?,?)",
                   (field_id, grid, body))
        for request, payload, version in db.execute(
                "SELECT request,payload,version FROM field_receipts"
                " WHERE version=? ORDER BY rowid", (field_id,)).fetchall():
            db.execute(
                "INSERT INTO v2_field_receipts(request,payload,version)"
                " VALUES(?,?,?)", (request, payload, version))
    # Phase 3: historical results must exist before jobs reference them.
    for result_id, job_id, body in db.execute(
            "SELECT id,job,body FROM results ORDER BY id").fetchall():
        db.execute("INSERT INTO v2_results(id,job,body) VALUES(?,?,?)",
                   (result_id, job_id, body))
    # Phase 4: fixed task bindings (jobs) referencing the now-present rows.
    for job_id, name, source, target, field, result in db.execute(
            "SELECT id,name,source,target,field,result FROM jobs"
            " ORDER BY id").fetchall():
        db.execute(
            "INSERT INTO v2_jobs(id,name,source,target,field,result)"
            " VALUES(?,?,?,?,?,?)",
            (job_id, name, source, target, field, result))
    if fault is not None:
        fault("migration.before_commit")


def _copy_grid_namespace(db, grid_id):
    for request, payload, revision in db.execute(
            "SELECT request,payload,revision FROM grid_receipts"
            " WHERE revision=? ORDER BY rowid", (grid_id,)).fetchall():
        db.execute(
            "INSERT INTO v2_grid_receipts(request,payload,revision)"
            " VALUES(?,?,?)", (request, payload, revision))
    # Heads live in a dedicated v2 table because their revision foreign key must
    # reference v2_grids (the v1 heads table references the audit grids table).
    for name, revision in db.execute(
            "SELECT name,revision FROM heads WHERE revision=?",
            (grid_id,)).fetchall():
        db.execute(
            "INSERT OR REPLACE INTO v2_heads(name,revision) VALUES(?,?)",
            (name, revision))
