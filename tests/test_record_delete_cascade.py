import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("DATABASE_URL", "postgres://user:password@localhost:5432/db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")


class RecordDeleteCascadeTests(unittest.TestCase):
    def test_foreign_keys_cascade_in_the_database(self):
        from models import Analysis, AnalysisImage, Ibis

        expected = {
            (Analysis.__table__, "recorder_id"),
            (AnalysisImage.__table__, "analysis_id"),
            (AnalysisImage.__table__, "record_id"),
            (Ibis.__table__, "analysis_id"),
            (Ibis.__table__, "analysis_image_id"),
        }
        for table, column in expected:
            fks = list(table.c[column].foreign_keys)
            self.assertEqual(len(fks), 1, f"{table.name}.{column}")
            self.assertEqual(fks[0].ondelete, "CASCADE", f"{table.name}.{column}")

    def test_relationships_leave_deletes_to_the_database(self):
        # Without passive_deletes the ORM nulls analyses.recorder_id (NOT NULL)
        # when a record is deleted, which made DELETE /records/{id} return 500.
        from sqlalchemy.orm import configure_mappers
        from models import Analysis, AnalysisImage, Record

        configure_mappers()
        for model, name in (
            (Record, "analysis"),
            (Analysis, "birds"),
            (Analysis, "image_analyses"),
            (AnalysisImage, "detections"),
        ):
            rel = model.__mapper__.relationships[name]
            self.assertTrue(rel.passive_deletes, f"{model.__name__}.{name}")
            self.assertTrue(rel.cascade.delete, f"{model.__name__}.{name}")


if __name__ == "__main__":
    unittest.main()
