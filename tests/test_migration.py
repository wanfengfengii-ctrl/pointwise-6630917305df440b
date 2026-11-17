import sqlite3
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from gridarchive import Archive
from gridarchive.decompose import decompose
from gridarchive.decompose_check import cross_check
from gridarchive.migration import upgrade
from gridarchive.samples import build_legacy, l_source, rectangle
from gridarchive.storage import Store
from gridarchive.v2_schema import AUDIT_TABLES, V2_VERSION
from gridarchive.vendor.sdk1 import Region
from gridarchive.vendor.sdk2 import Polygon


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = str(Path(self.dir.name) / "m.sqlite")
        self.ids = build_legacy(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_upgrade_sets_version_and_keeps_audit_tables(self):
        snapshot = self._audit_rows()
        upgrade(self.path)
        con = sqlite3.connect(self.path)
        self.assertEqual(
            con.execute("SELECT value FROM metadata WHERE key='version'")
            .fetchone()[0], V2_VERSION)
        self.assertEqual(snapshot, self._audit_rows())
        con.close()

    def _audit_rows(self):
        con = sqlite3.connect(self.path)
        rows = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                for t in AUDIT_TABLES}
        con.close()
        return rows

    def test_every_legacy_cell_gets_verified_convex_pieces(self):
        upgrade(self.path)
        archive = Archive(self.path)
        for revision in archive._v2.db.execute(
                "SELECT DISTINCT grid_id FROM v2_pieces").fetchall():
            revision = revision[0]
            raw = archive.read_grid(revision)
            for cell, rings in raw["cells"].items():
                pieces = archive.read_pieces(revision, cell)
                self.assertTrue(pieces)
                self.assertTrue(all(isinstance(p, Polygon) for p in pieces))
                # Independent exact SDK1 cross-check.
                cross_check(rings, pieces)

    def test_v2_remap_matches_legacy_scalar_results(self):
        upgrade(self.path)
        store = Store(self.path)
        from gridarchive.jobs import read_result
        legacy = read_result(store, self.ids["job"])["result"]
        archive = Archive(self.path)
        remap = archive.v2_remap(self.ids["source"], self.ids["target"])
        remap.put_field(
            "field-v2-check", self.ids["source"],
            {"mass": 12, "missing": None, "zero": 0})
        convex = remap.run_job("historical")
        for key in ("allocated", "source_area", "overlap_area", "transfers",
                    "uncovered", "missing_area", "uncovered_missing_area"):
            self.assertEqual(legacy[key], convex[key])

    def test_fault_before_commit_rolls_back_everything(self):
        class Boom(RuntimeError):
            pass

        with self.assertRaises(Boom):
            upgrade(self.path, fault=lambda event: (_ for _ in ()).throw(
                Boom(event)))
        con = sqlite3.connect(self.path)
        self.assertEqual(
            con.execute("SELECT value FROM metadata WHERE key='version'")
            .fetchone()[0], "1")
        for table in ("v2_grids", "v2_pieces", "v2_jobs", "v2_results"):
            if con.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?",
                    (table,)).fetchone():
                self.assertEqual(
                    con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    0)
        # Original database still fully usable through the SDK1 interface.
        con.close()
        archive = Archive(self.path)
        self.assertEqual(archive.head("source"),
                         self.ids["new_source"])
        self.assertEqual(archive.read_grid(self.ids["source"])["id"],
                         self.ids["source"])

    def test_repeated_upgrade_is_idempotent(self):
        upgrade(self.path)
        con = sqlite3.connect(self.path)
        first = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                 for t in AUDIT_TABLES}
        con.close()
        upgrade(self.path)
        upgrade(self.path)
        con = sqlite3.connect(self.path)
        second = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                  for t in AUDIT_TABLES}
        self.assertEqual(first, second)
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM v2_pieces").fetchone()[0], 17)
        con.close()

    def test_native_publish_and_read_round_trip(self):
        upgrade(self.path)
        archive = Archive(self.path)
        p1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        p2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])
        revision = archive.publish_pieces(
            "native", "native-request", {"c": [p1, p2]},
            {"c": [p1, p2]})
        loaded = archive.read_pieces(revision, "c")
        self.assertEqual([p.vertices for p in loaded],
                         [p1.vertices, p2.vertices])
        # Replay returns the same revision without new rows.
        again = archive.publish_pieces(
            "native", "native-request", {"c": [p1, p2]},
            {"c": [p1, p2]})
        self.assertEqual(revision, again)

    def test_native_publish_rejects_non_sdk2_or_overlapping_input(self):
        upgrade(self.path)
        archive = Archive(self.path)
        good = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        with self.assertRaises(Exception):
            archive.publish_pieces(
                "bad", "bad-request", {"c": [object()]})
        overlapping = Polygon([(0, 0), (2, 0), (2, 1), (0, 1)])
        with self.assertRaises(Exception):
            archive.publish_pieces(
                "bad2", "bad2-request",
                {"c": [good, overlapping]})


if __name__ == "__main__":
    unittest.main()
