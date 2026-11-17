"""面域观测归档：兼容 SDK1 旧接口，迁移后在版本 2 上使用凸片表示。"""
from .errors import Conflict, NotFound, ValidationError
from .storage import Store
from .v2_archive import V2Archive
from .v2_remap import V2Remap
from .v2_schema import V2_VERSION


def json_loads(text):
    import json
    return json.loads(text)


def _result_id_lookup(store, job):
    row = store.db.execute(
        "SELECT id FROM v2_jobs WHERE name=?", (job,)).fetchone()
    return row[0] if row else None


class Archive:
    def __init__(self, path=":memory:", fault=None):
        self._store = Store(path, fault)
        self._v2 = V2Archive(self._store)

    def close(self):
        self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ---- legacy SDK1 surface (delegates unchanged) ---------------------
    def publish_grid(self, name, request, cells):
        if self.storage_version == "2":
            return self._v2_publish_legacy_grid(name, request, cells)
        from .grids import publish_grid
        return publish_grid(self._store, name, request, cells)

    def _v2_publish_legacy_grid(self, name, request, cells):
        # Convert the legacy even-odd rings to SDK2 convex pieces and publish
        # through the v2 representation, keeping the old request semantics and
        # raw spelling available via read_grid's reconstructed view.
        from .decompose import decompose
        from .decompose_check import cross_check
        pieces_by_cell = {}
        for cell, rings in cells.items():
            polygons = decompose(rings)
            cross_check(rings, polygons)
            pieces_by_cell[cell] = polygons
        return self._v2.publish_pieces(
            name, request, cells, pieces_by_cell)

    def head(self, name):
        from .grids import head
        return head(self._store, name)

    def read_grid(self, revision):
        from .grids import read_grid
        return read_grid(self._store, revision)

    def grid_receipt(self, request):
        from .grids import grid_receipt
        return grid_receipt(self._store, request)

    def put_field(self, request, grid, values):
        if self.storage_version == "2":
            return self._v2_put_field(request, grid, values)
        from .fields import put_field
        return put_field(self._store, request, grid, values)

    def _v2_put_field(self, request, grid, values):
        from .codec import name as check_name, identity
        check_name(request)
        identity(grid)
        cells = set(self._v2.piece_cells(grid))
        if not isinstance(values, dict) or set(values) != cells:
            raise ValidationError(
                "field must cover exactly the cells of its grid revision")
        from .codec import dump, pack
        from fractions import Fraction
        body = dump({cell: None if value is None
                     else str(Fraction(value)) for cell, value in values.items()})
        payload = dump({"grid": grid, "values": json_loads(body)})
        prior = self._store.db.execute(
            "SELECT payload,version FROM v2_field_receipts WHERE request=?",
            (request,)).fetchone()
        if prior is not None:
            if prior["payload"] != payload:
                raise Conflict("field request already bound")
            return prior["version"]
        with self._store.transaction("v2.field.before_commit") as db:
            version = db.execute(
                "INSERT INTO v2_fields(grid,body) VALUES(?,?)",
                (grid, body)).lastrowid
            db.execute(
                "INSERT INTO v2_field_receipts(request,payload,version)"
                " VALUES(?,?,?)", (request, payload, version))
        return version

    def read_field(self, version):
        from .fields import read_field
        return read_field(self._store, version)

    def field_receipt(self, request):
        from .fields import field_receipt
        return field_receipt(self._store, request)

    def create_job(self, name, source, target, field):
        if self.storage_version == "2":
            return self._v2_create_job(name, source, target, field)
        from .jobs import create_job
        return create_job(self._store, name, source, target, field)

    def _v2_create_job(self, name, source, target, field):
        from .codec import name as check_name, identity
        check_name(name)
        identity(source)
        identity(target)
        identity(field)
        observation = self._store.db.execute(
            "SELECT grid FROM v2_fields WHERE id=?", (field,)).fetchone()
        if observation is None:
            raise NotFound("field version")
        if observation[0] != source:
            raise ValidationError("field belongs to a different grid revision")
        for grid_id in (source, target):
            if not self._store.db.execute(
                    "SELECT 1 FROM v2_grids WHERE id=?",
                    (grid_id,)).fetchone():
                raise NotFound("grid revision")
        with self._store.transaction("v2.job.before_commit") as db:
            prior = db.execute(
                "SELECT * FROM v2_jobs WHERE name=?", (name,)).fetchone()
            if prior is not None:
                if (prior["source"], prior["target"], prior["field"]) \
                        != (source, target, field):
                    raise Conflict("job name already bound")
                return prior["id"]
            job_id = db.execute(
                "INSERT INTO v2_jobs(name,source,target,field,result)"
                " VALUES(?,?,?,?,NULL)",
                (name, source, target, field)).lastrowid
        return job_id

    def read_result(self, job):
        from .jobs import read_result
        return read_result(self._store, job)

    def run_job(self, job):
        # On a version-2 archive run through the convex-piece remap so only v2
        # result tables are written and the SDK1 audit tables stay untouched.
        if self.storage_version == "2":
            return self._run_v2_job(job)
        from .remap import run_job
        return run_job(self._store, job)

    def _run_v2_job(self, job):
        from .fields import read_field
        row = self._store.db.execute(
            "SELECT id,name,source,target,field FROM v2_jobs"
            " WHERE name=? OR id=?", (job, job)).fetchone()
        if row is None:
            raise NotFound("job")
        job_id, job_name, source, target, field_id = row
        binding = {"id": job_id, "name": job_name, "source": source,
                   "target": target, "field": field_id}
        remap = V2Remap(self._v2, source, target)
        values = read_field(self._store, field_id)["values"]
        from fractions import Fraction
        remap._values = {
            k: (None if v is None else Fraction(v)) for k, v in values.items()}
        result = remap.run_job(job)
        return {"job": binding,
                "result_id": _result_id_lookup(self._store, job_name),
                "result": result}

    # ---- SDK2 convex-piece surface (version 2 only) --------------------
    def publish_pieces(self, name, request, pieces_by_cell, cells=None):
        return self._v2.publish_pieces(
            name, request, cells or pieces_by_cell, pieces_by_cell)

    def read_pieces(self, revision, cell=None):
        if cell is None:
            # Whole-revision view: cell key -> tuple of SDK2 polygons.
            return {key: tuple(self._v2.read_pieces(revision, key))
                    for key in self._v2.piece_cells(revision)}
        return self._v2.read_pieces(revision, cell)

    def piece_cells(self, revision):
        return self._v2.piece_cells(revision)

    def v2_remap(self, source, target):
        return V2Remap(self._v2, source, target)

    @property
    def storage_version(self):
        return self._store.db.execute(
            "SELECT value FROM metadata WHERE key='version'").fetchone()[0]
