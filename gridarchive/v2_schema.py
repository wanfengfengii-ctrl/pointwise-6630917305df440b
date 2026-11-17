"""Version-2 target storage for convex-piece geometry.

The original SDK1 tables (grids/heads/grid_receipts/fields/field_receipts/jobs/
results) are kept byte-for-byte as read-only audit tables.  The v2 schema stores
each logical cell as an ordered collection of SDK2 convex Polygon pieces:

* ``v2_grids``       - grid revision header (mirrors the grids row identity)
* ``v2_pieces``      - one convex piece row, linked to its cell and grid
* ``v2_grid_receipts`` / ``v2_field_receipts`` - request namespace receipts
* the v2 field/job/result tables reuse the same shape and semantics as v1 but
  read and write through the convex representation.

Pieces belong to a *logical cell* (the revision id + cell key); they are never
treated as standalone business units.  All geometry is exact Fraction/rational
data serialized through the typed codec in :mod:`gridarchive.codec`.
"""

V2_VERSION = "2"

V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS v2_grids(
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL,
 kind TEXT NOT NULL,
 body TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS v2_pieces(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 grid_id INTEGER NOT NULL REFERENCES v2_grids(id),
 cell_key TEXT NOT NULL,
 ordinal INTEGER NOT NULL,
 body TEXT NOT NULL,
 UNIQUE(grid_id, cell_key, ordinal));
CREATE INDEX IF NOT EXISTS idx_v2_pieces_cell
 ON v2_pieces(grid_id, cell_key);

CREATE TABLE IF NOT EXISTS v2_grid_receipts(
 request TEXT PRIMARY KEY,
 payload TEXT NOT NULL,
 revision INTEGER NOT NULL REFERENCES v2_grids(id));

CREATE TABLE IF NOT EXISTS v2_heads(
 name TEXT PRIMARY KEY,
 revision INTEGER NOT NULL REFERENCES v2_grids(id));

CREATE TABLE IF NOT EXISTS v2_fields(
 id INTEGER PRIMARY KEY,
 grid INTEGER NOT NULL REFERENCES v2_grids(id),
 body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_v2_fields_grid ON v2_fields(grid);

CREATE TABLE IF NOT EXISTS v2_field_receipts(
 request TEXT PRIMARY KEY,
 payload TEXT NOT NULL,
 version INTEGER NOT NULL REFERENCES v2_fields(id));

CREATE TABLE IF NOT EXISTS v2_jobs(
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL UNIQUE,
 source INTEGER NOT NULL REFERENCES v2_grids(id),
 target INTEGER NOT NULL REFERENCES v2_grids(id),
 field INTEGER NOT NULL REFERENCES v2_fields(id),
 result INTEGER REFERENCES v2_results(id));

CREATE TABLE IF NOT EXISTS v2_results(
 id INTEGER PRIMARY KEY,
 job INTEGER NOT NULL,
 body TEXT NOT NULL);
"""

AUDIT_TABLES = ("grids", "heads", "grid_receipts", "fields",
                "field_receipts", "jobs", "results")
