import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/reapply_module_proposals.py"


def verse_text(archive: Path, book: int, chapter: int, verse: int) -> str:
    with tempfile.TemporaryDirectory() as temp_dir:
        with zipfile.ZipFile(archive) as module_zip:
            module_zip.extract(".SQLite3", temp_dir)
        with sqlite3.connect(str(Path(temp_dir) / ".SQLite3")) as conn:
            return conn.execute(
                "SELECT text FROM verses WHERE book_number = ? AND chapter = ? AND verse = ?",
                (book, chapter, verse),
            ).fetchone()[0]


class ReapplyModuleProposalsTest(unittest.TestCase):
    def test_upstream_text_wins_while_unrelated_proposal_is_applied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "modules").mkdir()
            proposal_dir = root / "proposals/module-updates"
            proposal_dir.mkdir(parents=True)
            archive = root / "modules/JBL.zip"

            sqlite_path = root / ".SQLite3"
            with sqlite3.connect(str(sqlite_path)) as conn:
                conn.execute("CREATE TABLE books (book_number INTEGER, long_name TEXT, short_name TEXT)")
                conn.execute("CREATE TABLE verses (book_number INTEGER, chapter INTEGER, verse INTEGER, text TEXT)")
                conn.execute("INSERT INTO books VALUES (290, 'Isaiah', 'Isa')")
                conn.executemany(
                    "INSERT INTO verses VALUES (290, 66, ?, ?)",
                    [(9, "upstream revision"), (10, "original text")],
                )
            with zipfile.ZipFile(archive, "w") as module_zip:
                module_zip.write(sqlite_path, ".SQLite3")
            sqlite_path.unlink()

            (root / "registry.json").write_text(
                json.dumps({"version": 1, "downloads": [{"abr": "JBL", "fil": "JBL"}]}),
                encoding="utf-8",
            )
            for filename, verse, old_text, new_text in [
                ("conflict.yaml", 9, "old local text", "local correction"),
                ("applicable.yaml", 10, "original text", "local update"),
            ]:
                (proposal_dir / filename).write_text(
                    yaml.safe_dump(
                        {
                            "proposal_type": "module_verse_update",
                            "request_id": filename,
                            "module_name": "JBL",
                            "short_name": "JBL",
                            "requested_file_name": "JBL",
                            "book_number": 290,
                            "chapter_number": 66,
                            "verse_number": verse,
                            "old_text": old_text,
                            "new_text": new_text,
                        },
                        allow_unicode=True,
                    ),
                    encoding="utf-8",
                )

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--modules", "JBL", "--summary-path", "summary.md"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary["proposals_applied"], 1)
            self.assertEqual(len(summary["conflicts"]), 1)
            self.assertEqual(verse_text(archive, 290, 66, 9), "upstream revision")
            self.assertEqual(verse_text(archive, 290, 66, 10), "local update")
            self.assertIn("Proposals Superseded by Upstream", (root / "summary.md").read_text())
            registry = json.loads((root / "registry.json").read_text())
            self.assertIn("local update", registry["downloads"][0]["cmt"])
            self.assertNotIn("local correction", registry["downloads"][0]["cmt"])


if __name__ == "__main__":
    unittest.main()
