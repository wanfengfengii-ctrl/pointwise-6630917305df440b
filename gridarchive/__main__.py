import argparse
import json
import tempfile
from fractions import Fraction
from pathlib import Path

from . import Archive
from .codec import pack
from .migration import upgrade
from .samples import build_legacy, rectangle
from .vendor.sdk2 import Polygon


def main():
    parser = argparse.ArgumentParser(description="Exact conservative grid archive")
    parser.add_argument("command", choices=["demo"])
    parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="gridarchive-demo-") as directory:
        path = str(Path(directory) / "archive.sqlite")
        ids = build_legacy(path)

        # --- SDK1 baseline, before the storage upgrade -------------------
        with Archive(path) as archive:
            before = archive.read_result(ids["job"])
            assert before["result"]["allocated"]["small"] == 3
            assert before["result"]["allocated"]["large"] == 9

        # --- atomic SDK1 -> SDK2 migration --------------------------------
        upgrade(path)

        with Archive(path) as archive:
            assert archive.storage_version == "2"
            # The same convex pieces are readable as real SDK2 polygons.
            pieces = archive.read_pieces(ids["source"], "mass")
            assert pieces and all(isinstance(p, Polygon) for p in pieces)
            mass_area = sum((p.area() for p in pieces), Fraction(0))
            assert mass_area == Fraction(4)

            # Rerun the fixed task on the convex representation; the conserved
            # result is identical to the pre-migration SDK1 scalar result.
            rerun = archive.run_job(ids["job"])
            assert rerun["result"] == before["result"]
            assert archive.head("source") == ids["new_source"]

            # Native convex-piece publication and a fresh v2 remap.
            native_parts = {
                "mass": [Polygon(rectangle(0, 0, 1, 1)),
                         Polygon(rectangle(1, 0, 2, 1))],
            }
            native_revision = archive.publish_pieces(
                "native", "native-demo-request", native_parts)
            native_pieces = archive.read_pieces(native_revision, "mass")
            assert [p.vertices for p in native_pieces] == \
                [p.vertices for p in native_parts["mass"]]

            print(json.dumps(pack({
                "storage_version": archive.storage_version,
                "bindings": ids,
                "mass_piece_count": len(pieces),
                "mass_convex_area": mass_area,
                "historical": rerun,
                "current": archive.read_result(ids["new_job"]),
                "self_crossing": archive.read_result(ids["bow_job"]),
                "source_head": archive.head("source"),
                "native_revision": native_revision,
            }), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
