"""Conservative convex-piece remapping on the SDK2 storage path.

The v2 remap consumes only SDK2 Polygon geometry.  Source logical cells are
merged per target cell by summing exact Polygon intersection areas, then
allocating each source total by the source cell's complete source area:

    transfers[s][t] = v(s) * A(s, t) / A(s)

where A(s, t) is the summed intersection area of all pieces of source cell s
with all pieces of target cell t, computed through the SDK2 clipping port.
Missing-area, uncovered-area and the full Fraction result set match the v1
contract, but no SDK1 scalar evaluator is involved.
"""

from fractions import Fraction

from .codec import pack
from .errors import NotFound, ValidationError
from .v2_archive import V2Archive, require_v2


class V2Remap:
    """Exact convex remapping between two SDK2 grid revisions."""

    def __init__(self, archive, source, target):
        self._archive = archive
        self.source = source
        self.target = target
        self._values = {}
        self._source_areas = {}
        self._overlap = {}

    def put_field(self, request, grid, values):
        require_v2(self._archive._store)
        if grid != self.source:
            raise ValidationError("field must bind the remap source")
        cells = set(self._archive.piece_cells(grid))
        if set(values) != cells:
            raise ValidationError("field must cover exactly the source cells")
        for key, value in values.items():
            self._values[key] = None if value is None else Fraction(value)
        return self._version(request, grid, values)

    def _version(self, request, grid, values):
        store = self._archive._store
        prior = store.db.execute(
            "SELECT version FROM v2_field_receipts WHERE request=?",
            (request,)).fetchone()
        if prior is not None:
            return prior[0]
        body = {"grid": grid, "values": {
            cell: None if value is None else pack(Fraction(value))
            for cell, value in values.items()}}
        with store.transaction("v2.field.before_commit"):
            version = store.db.execute(
                "SELECT COALESCE(MAX(id),0)+1 FROM v2_fields").fetchone()[0]
            store.db.execute(
                "INSERT INTO v2_fields(id,grid,body) VALUES(?,?,?)",
                (version, grid, json_dump(body)))
            store.db.execute(
                "INSERT INTO v2_field_receipts(request,payload,version)"
                " VALUES(?,?,?)", (request, json_dump(body), version))
        return version

    def source_area(self, cell):
        if cell not in self._source_areas:
            pieces = self._archive.read_pieces(self.source, cell)
            self._source_areas[cell] = sum(
                (p.area() for p in pieces), Fraction(0))
        return self._source_areas[cell]

    def overlap_area(self, source_cell, target_cell):
        key = (source_cell, target_cell)
        if key not in self._overlap:
            source_pieces = self._archive.read_pieces(self.source, source_cell)
            target_pieces = self._archive.read_pieces(self.target, target_cell)
            self._overlap[key] = sum(
                (a.intersection_area(b)
                 for a in source_pieces for b in target_pieces),
                Fraction(0))
        return self._overlap[key]

    def run_job(self, job):
        store = self._archive._store
        # Accept either the job name or its integer id.
        row = store.db.execute(
            "SELECT id,name,field,result FROM v2_jobs WHERE name=? OR id=?",
            (job, job)).fetchone()
        if row is None:
            raise NotFound("job")
        job_id, job_name, field_id, prior_result = row
        field_grid = store.db.execute(
            "SELECT grid FROM v2_fields WHERE id=?", (field_id,)).fetchone()[0]
        if field_grid != self.source:
            raise ValidationError("job field does not bind the source")
        result = self._compute()
        body = json_dump(_result_body(result))
        with store.transaction("v2.result.after_insert"):
            # Idempotent republish: keep the original result row and pointer
            # when recomputation yields the identical body, so reruns neither
            # add result identities nor move the job's result pointer.
            existing = store.db.execute(
                "SELECT body FROM v2_results WHERE id=?",
                (prior_result,)).fetchone() if prior_result is not None else None
            if existing is not None and existing["body"] == body:
                result_id = prior_result
            else:
                result_id = store.db.execute(
                    "SELECT COALESCE(MAX(id),0)+1 FROM v2_results"
                ).fetchone()[0]
                store.db.execute(
                    "INSERT INTO v2_results(id,job,body) VALUES(?,?,?)",
                    (result_id, job_id, body))
                store.db.execute(
                    "UPDATE v2_jobs SET result=? WHERE id=?",
                    (result_id, job_id))
        return self.read_result(job_name)

    def _compute(self):
        source_cells = sorted(self._archive.piece_cells(self.source))
        target_cells = sorted(self._archive.piece_cells(self.target))
        result = {"allocated": {cell: Fraction(0) for cell in target_cells},
                  "source_area": {}, "overlap_area": {}, "transfers": {},
                  "uncovered": {}, "missing_area": {},
                  "uncovered_missing_area": {}}
        for cell in source_cells:
            area = self.source_area(cell)
            overlaps = {other: self.overlap_area(cell, other)
                        for other in target_cells}
            covered = sum(overlaps.values(), Fraction(0))
            if covered > area:
                raise ArithmeticError(
                    "target grid violates disjoint interior invariant")
            result["source_area"][cell] = area
            result["overlap_area"][cell] = overlaps
            value = self._values.get(cell)
            if value is None:
                result["missing_area"][cell] = {
                    other: overlap for other, overlap in overlaps.items()
                    if overlap}
                result["uncovered_missing_area"][cell] = area - covered
            else:
                transfers = {other: value * overlap / area
                             for other, overlap in overlaps.items()}
                result["transfers"][cell] = transfers
                result["uncovered"][cell] = value * (area - covered) / area
                for other, amount in transfers.items():
                    result["allocated"][other] += amount
        return result

    def read_result(self, job):
        store = self._archive._store
        row = store.db.execute(
            "SELECT r.body FROM v2_results r JOIN v2_jobs j ON r.job=j.id"
            " WHERE j.name=?", (job,)).fetchone()
        if row is None:
            raise NotFound("job")
        return _result_unpack(row[0])


def json_dump(value):
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _result_body(result):
    def fr_map(mapping):
        return {c: pack(v) for c, v in mapping.items()}

    return {
        "allocated": fr_map(result["allocated"]),
        "source_area": fr_map(result["source_area"]),
        "overlap_area": {c: fr_map(ov)
                         for c, ov in result["overlap_area"].items()},
        "transfers": {c: fr_map(tr)
                      for c, tr in result["transfers"].items()},
        "uncovered": fr_map(result["uncovered"]),
        "missing_area": {c: fr_map(ov)
                         for c, ov in result["missing_area"].items()},
        "uncovered_missing_area": fr_map(result["uncovered_missing_area"]),
    }


def _result_unpack(body):
    import json
    from .codec import unpack
    raw = json.loads(body)

    def fr(packed):
        return unpack(packed)

    def fr_map(mapping):
        return {c: fr(v) for c, v in mapping.items()}

    return {
        "allocated": fr_map(raw["allocated"]),
        "source_area": fr_map(raw["source_area"]),
        "overlap_area": {c: fr_map(ov)
                         for c, ov in raw["overlap_area"].items()},
        "transfers": {c: fr_map(tr)
                      for c, tr in raw["transfers"].items()},
        "uncovered": fr_map(raw["uncovered"]),
        "missing_area": {c: fr_map(ov)
                         for c, ov in raw["missing_area"].items()},
        "uncovered_missing_area": fr_map(raw["uncovered_missing_area"]),
    }
