"""Public migration exercise and smoke checks; no migration implementation."""
import argparse
import json
import sqlite3
import tempfile
from fractions import Fraction as F
from pathlib import Path

from gridarchive import Archive, ValidationError
from gridarchive.codec import pack
from gridarchive.samples import build_legacy, populate, rectangle
from gridarchive.vendor.sdk2 import Polygon

AUDIT = ("grids", "heads", "grid_receipts", "fields", "field_receipts", "jobs", "results")


def audit(path):
    with sqlite3.connect(path) as db:
        return {table: db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
                for table in AUDIT}


def observe(archive, ids):
    return {
        "grids": {key: archive.read_grid(ids[key])
                  for key in ("source", "target", "new_source", "new_target", "bow")},
        "fields": {key: archive.read_field(ids[key])
                   for key in ("field", "new_field", "bow_field")},
        "results": {key: archive.read_result(ids[key])
                    for key in ("job", "new_job", "bow_job")},
        "heads": {name: archive.head(name) for name in ("source", "target", "bow")},
        "grid_receipts": {name: archive.grid_receipt(name)
                          for name in ("source-1", "source-2", "target-1", "target-2", "bow-1")},
        "field_receipts": {name: archive.field_receipt(name)
                           for name in ("field-1", "field-2", "bow-field")},
    }


def baseline():
    with Archive() as archive:
        ids = populate(archive)
        before = observe(archive, ids)
        result = archive.run_job(ids["job"])["result"]
        assert result["allocated"]["small"] == F(3)
        assert result["allocated"]["large"] == F(9)
        assert result["source_area"]["mass"] == F(4)
        assert before["heads"]["source"] == ids["new_source"]
        assert before["results"]["new_job"]["result"]["source_area"]["lake"] == F(12)
        assert before["results"]["bow_job"]["result"]["source_area"]["bow"] == F(2)
        small = Polygon(rectangle(0, 0, 1, 1))
        large = Polygon(rectangle(0, 1, 3, 2))
        assert small.area() == F(1) and large.area() == F(3)
        assert small.intersection_area(large) == F(0)
        try:
            Polygon([(0, 0), (1, 0), (1, 1), (3, 1), (3, 2), (0, 2)])
        except ValidationError:
            pass
        else:
            raise AssertionError("SDK2 unexpectedly accepted concave input")
        return {"mode": "baseline", "bindings": ids, "historical": result,
                "native_piece_areas": [small.area(), large.area()]}


class InjectedFailure(RuntimeError):
    pass


def verify():
    from gridarchive.migration import upgrade
    with tempfile.TemporaryDirectory(prefix="grid-migration-") as directory:
        path = str(Path(directory) / "archive.sqlite")
        ids = build_legacy(path)
        old_rows = audit(path)
        with Archive(path) as existing:
            before = observe(existing, ids)
            events = []

            def fail(event):
                events.append(event)
                if event == "migration.before_commit":
                    raise InjectedFailure(event)

            try:
                upgrade(path, fault=fail)
            except InjectedFailure:
                pass
            else:
                raise AssertionError("migration commit hook did not propagate")
            assert "migration.before_commit" in events
            assert audit(path) == old_rows
            with sqlite3.connect(path) as db:
                assert db.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0] == "1"
            assert observe(existing, ids) == before
            with Archive(path) as retry_reader:
                assert observe(retry_reader, ids) == before

            upgrade(path)
            assert audit(path) == old_rows
            assert observe(existing, ids) == before
            with sqlite3.connect(path) as db:
                assert db.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0] == "2"
                assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []

            for key in ("job", "new_job", "bow_job"):
                assert existing.run_job(ids[key])["result"] == before["results"][key]["result"]
            assert audit(path) == old_rows
            for key in ("source", "new_source", "bow"):
                pieces = existing.read_pieces(ids[key])
                assert set(pieces) == set(before["grids"][key]["cells"])
                for cell, group in pieces.items():
                    assert isinstance(group, tuple) and group
                    assert all(isinstance(p, Polygon) for p in group)
                    for i, a in enumerate(group):
                        for b in group[i + 1:]:
                            assert a.intersection_area(b) == 0

            with Archive(path) as native:
                parts = {
                    "mass": [Polygon(rectangle(0, 0, 1, 1)),
                             Polygon(rectangle(0, 1, 1, 2)),
                             Polygon(rectangle(1, 1, 3, 2))],
                    "missing": [Polygon(rectangle(4, 0, 6, 2))],
                    "zero": [Polygon(rectangle(7, 0, 8, 1))],
                }
                source = native.publish_pieces("source", "native-source", parts)
                field = native.put_field("native-field", source,
                                         {"mass": 12, "missing": None, "zero": 0})
                job = native.create_job("native-job", source, ids["target"], field)
                result = existing.run_job(job)
                assert result["result"] == before["results"]["job"]["result"]
                assert existing.head("source") == source
                assert existing.publish_pieces("source", "native-source", parts) == source
                assert existing.publish_grid("source", "source-1",
                                             before["grids"]["source"]["raw"]) == ids["source"]
                assert existing.head("source") == source
                assert existing.read_result(ids["job"])["job"] == before["results"]["job"]["job"]
                # Exercise compatible old writes against the new representation too.
                hole = existing.publish_grid("hole-after", "hole-after", {
                    "lake": [rectangle(0, 0, 4, 4), rectangle(1, 1, 3, 3)]})
                hole_field = existing.put_field("hole-after-field", hole, {"lake": 24})
                hole_job = existing.create_job("hole-after-job", hole, ids["new_target"], hole_field)
                assert native.run_job(hole_job)["result"]["allocated"]["whole"] == F(24)
                assert existing.read_result(job) == result
            state = observe(existing, ids)
            native_grid = existing.read_grid(source)
            native_pieces = existing.read_pieces(source)
            assert sum((p.area() for p in native_pieces["mass"]), F(0)) == F(4)
            assert audit(path) == old_rows

        with Archive(path) as reopened:
            assert observe(reopened, ids) == state
            assert reopened.read_grid(source) == native_grid
            assert reopened.read_result(job) == result
            assert reopened.run_job(job)["result"] == result["result"]
            result = reopened.read_result(job)
            saved = observe(reopened, ids)
        with sqlite3.connect(path) as db:
            before_repeat = "\n".join(db.iterdump())
        upgrade(path)
        with sqlite3.connect(path) as db:
            assert "\n".join(db.iterdump()) == before_repeat
        with Archive(path) as reopened:
            assert observe(reopened, ids) == saved
            assert reopened.read_result(job) == result
        assert audit(path) == old_rows
        return {"mode": "migration", "legacy_bindings": ids,
                "native_revision": source, "native_grid": native_grid,
                "native_result": result, "historical": saved["results"],
                "rollback_event": "migration.before_commit"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--baseline", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    value = baseline() if args.baseline else verify()
    print(json.dumps(pack(value), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
