#!/usr/bin/env python3
import argparse
import json
import sqlite3
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError as exc:
    print(f"Failed to import yaml: {exc}", file=sys.stderr)
    print("Install dependency: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def validate_proposal(data: dict) -> dict:
    if not isinstance(data, dict):
        fail("Proposal YAML must be an object")

    required_fields = [
        "proposal_type",
        "request_id",
        "module_name",
        "requested_file_name",
        "book_number",
        "chapter_number",
        "verse_number",
        "old_text",
        "new_text",
    ]
    missing = [field for field in required_fields if field not in data]
    if missing:
        fail(f"Missing required proposal fields: {', '.join(missing)}")

    if data.get("proposal_type") != "module_verse_update":
        fail("Unsupported proposal_type (expected 'module_verse_update')")

    for field in ["request_id", "module_name", "requested_file_name"]:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            fail(f"Field '{field}' must be a non-empty string")

    short_name = data.get("short_name")
    if short_name is not None and (not isinstance(short_name, str) or not short_name.strip()):
        fail("Field 'short_name' must be null or non-empty string")

    old_text = data.get("old_text")
    if not isinstance(old_text, str):
        fail("Field 'old_text' must be a string")

    for field in ["book_number", "chapter_number", "verse_number"]:
        value = data.get(field)
        if not isinstance(value, int) or value <= 0:
            fail(f"Field '{field}' must be a positive integer")

    new_text = data.get("new_text")
    if not isinstance(new_text, str) or not new_text.strip():
        fail("Field 'new_text' must be a non-empty string")

    normalized = dict(data)
    normalized["module_name"] = data["module_name"].strip()
    normalized["requested_file_name"] = data["requested_file_name"].strip()
    normalized["short_name"] = short_name.strip() if isinstance(short_name, str) else None
    normalized["old_text"] = old_text
    normalized["new_text"] = new_text
    return normalized


def resolve_module_entry(downloads: list, proposal: dict) -> tuple[int, dict]:
    def find_matches(key: str, value: str) -> list[tuple[int, dict]]:
        return [
            (idx, item)
            for idx, item in enumerate(downloads)
            if isinstance(item, dict) and item.get(key) == value
        ]

    short_name = proposal.get("short_name")
    requested_file_name = proposal["requested_file_name"]
    module_name = proposal["module_name"]

    if short_name:
        matches = find_matches("abr", short_name)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            fail(f"Registry has multiple modules with abr='{short_name}'")

    matches = find_matches("fil", requested_file_name)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        fail(f"Registry has multiple modules with fil='{requested_file_name}'")

    matches = find_matches("des", module_name)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        fail(f"Registry has multiple modules with des='{module_name}'")

    fail(
        "Could not resolve module in registry using short_name/requested_file_name/module_name"
    )


def format_comment_block(module_file: str, proposal: dict, book_title: str, old_text: str) -> str:
    today = date.today().isoformat()

    def escape_backticks(text: str) -> str:
        return text.replace("`", "\\`")

    lines = [
        f"({today}) User proposed: Module: `{module_file}`",
        f"Book: `{book_title} ({proposal['book_number']})`",
        f"Chapter: `{proposal['chapter_number']}`",
        f"Verse: `{proposal['verse_number']}`",
        "",
        "```",
        escape_backticks(old_text),
        "```",
        "->",
        "```",
        escape_backticks(proposal["new_text"]),
        "```",
    ]
    return "\n".join(lines)


def apply_proposal(repo_root: Path, proposal_path: Path) -> dict:
    registry_path = repo_root / "registry.json"
    modules_dir = repo_root / "modules"

    if not registry_path.exists():
        fail(f"registry.json not found at {registry_path}")
    if not modules_dir.exists():
        fail(f"modules directory not found at {modules_dir}")

    with proposal_path.open("r", encoding="utf-8") as handle:
        proposal_raw = yaml.safe_load(handle)
    proposal = validate_proposal(proposal_raw)

    with registry_path.open("r", encoding="utf-8") as handle:
        registry = json.load(handle)

    downloads = registry.get("downloads")
    if not isinstance(downloads, list):
        fail("registry.json has invalid 'downloads' section")

    module_index, module_entry = resolve_module_entry(downloads, proposal)
    module_file = module_entry.get("fil")
    if not isinstance(module_file, str) or not module_file.strip():
        fail("Resolved registry module does not have valid 'fil'")
    module_file = module_file.strip()

    module_zip_path = modules_dir / f"{module_file}.zip"
    if not module_zip_path.exists():
        fail(f"Module ZIP not found: {module_zip_path}")

    with tempfile.TemporaryDirectory(prefix="proposal-apply-") as tmp_dir:
        tmp_path = Path(tmp_dir)

        with zipfile.ZipFile(module_zip_path, "r") as zf:
            names = zf.namelist()
            if ".SQLite3" not in names:
                fail(f"Archive {module_zip_path.name} does not contain .SQLite3")
            zf.extractall(tmp_path)

        sqlite_path = tmp_path / ".SQLite3"
        if not sqlite_path.exists():
            fail("Failed to extract .SQLite3 from module archive")

        conn = sqlite3.connect(str(sqlite_path))
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT v.text, COALESCE(b.long_name, b.short_name, ?) AS book_title
                FROM verses v
                LEFT JOIN books b ON v.book_number = b.book_number
                WHERE v.book_number = ? AND v.chapter = ? AND v.verse = ?
                """,
                (
                    f"Book {proposal['book_number']}",
                    proposal["book_number"],
                    proposal["chapter_number"],
                    proposal["verse_number"],
                ),
            )
            rows = cur.fetchall()
            if not rows:
                fail(
                    f"Verse not found in module: {proposal['book_number']}:{proposal['chapter_number']}:{proposal['verse_number']}"
                )

            original_text = rows[0][0] if rows[0][0] is not None else ""
            book_title = rows[0][1] if rows[0][1] is not None else f"Book {proposal['book_number']}"

            cur.execute(
                """
                UPDATE verses
                SET text = ?
                WHERE book_number = ? AND chapter = ? AND verse = ?
                """,
                (
                    proposal["new_text"],
                    proposal["book_number"],
                    proposal["chapter_number"],
                    proposal["verse_number"],
                ),
            )
            updated_rows = cur.rowcount if cur.rowcount and cur.rowcount > 0 else len(rows)
            conn.commit()
        finally:
            conn.close()

        with zipfile.ZipFile(
            module_zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as zf:
            for file_path in sorted(tmp_path.rglob("*")):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(tmp_path).as_posix())

    comment_block = format_comment_block(module_file, proposal, book_title, original_text)
    current_cmt = module_entry.get("cmt")
    module_entry["cmt"] = f"{comment_block}\n{current_cmt}" if current_cmt else comment_block
    module_entry["upd"] = date.today().isoformat()

    current_version = registry.get("version", 0)
    if not isinstance(current_version, int):
        fail("registry.json has non-integer 'version'")
    registry["version"] = current_version + 1

    downloads[module_index] = module_entry

    with registry_path.open("w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    registry_zip_path = repo_root / "registry.zip"
    with zipfile.ZipFile(
        registry_zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zf:
        zf.write(registry_path, "registry.json")

    return {
        "proposal_path": str(proposal_path.relative_to(repo_root)),
        "module_abr": module_entry.get("abr"),
        "module_file": module_file,
        "module_zip_path": str(module_zip_path.relative_to(repo_root)),
        "book_number": proposal["book_number"],
        "chapter_number": proposal["chapter_number"],
        "verse_number": proposal["verse_number"],
        "updated_rows": updated_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply module verse update proposal from YAML")
    parser.add_argument("--proposal-path", required=True, help="Path to proposal YAML file")
    args = parser.parse_args()

    repo_root = Path.cwd()
    proposal_path = (repo_root / args.proposal_path).resolve()

    if not proposal_path.exists():
        fail(f"Proposal file not found: {proposal_path}")

    try:
        summary = apply_proposal(repo_root, proposal_path)
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive fallback for workflow logs
        fail(f"Unexpected error while applying proposal: {exc}")

    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
