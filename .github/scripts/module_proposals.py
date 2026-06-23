#!/usr/bin/env python3
from __future__ import annotations

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


class ProposalError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise ProposalError(message)


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


def load_proposal(proposal_path: Path) -> dict:
    with proposal_path.open("r", encoding="utf-8") as handle:
        return validate_proposal(yaml.safe_load(handle))


def load_registry(repo_root: Path) -> dict:
    registry_path = repo_root / "registry.json"
    if not registry_path.exists():
        fail(f"registry.json not found at {registry_path}")

    with registry_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_registry(repo_root: Path, registry: dict) -> None:
    registry_path = repo_root / "registry.json"
    with registry_path.open("w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_registry_zip(repo_root: Path) -> None:
    registry_zip_path = repo_root / "registry.zip"
    with zipfile.ZipFile(
        registry_zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zf:
        zf.write(repo_root / "registry.json", "registry.json")


def bump_registry_version(registry: dict) -> None:
    current_version = registry.get("version", 0)
    if not isinstance(current_version, int):
        fail("registry.json has non-integer 'version'")
    registry["version"] = current_version + 1


def registry_downloads(registry: dict) -> list:
    downloads = registry.get("downloads")
    if not isinstance(downloads, list):
        fail("registry.json has invalid 'downloads' section")
    return downloads


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

    fail("Could not resolve module in registry using short_name/requested_file_name/module_name")


def resolve_module_by_abr(downloads: list, module_abr: str) -> tuple[int, dict]:
    matches = [
        (idx, item)
        for idx, item in enumerate(downloads)
        if isinstance(item, dict) and item.get("abr") == module_abr
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        fail(f"Registry has multiple modules with abr='{module_abr}'")
    fail(f"Could not resolve module in registry using abr='{module_abr}'")


def module_file_from_entry(module_entry: dict) -> str:
    module_file = module_entry.get("fil")
    if not isinstance(module_file, str) or not module_file.strip():
        fail("Resolved registry module does not have valid 'fil'")
    return module_file.strip()


def format_comment_block(
    module_file: str,
    proposal: dict,
    book_title: str,
    old_text: str,
    applied_date: str | None = None,
) -> str:
    block_date = applied_date or date.today().isoformat()

    def escape_backticks(text: str) -> str:
        return text.replace("`", "\\`")

    lines = [
        f"({block_date}) User proposed: Module: `{module_file}`",
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


def extract_module(module_zip_path: Path, tmp_path: Path) -> Path:
    with zipfile.ZipFile(module_zip_path, "r") as zf:
        names = zf.namelist()
        if ".SQLite3" not in names:
            fail(f"Archive {module_zip_path.name} does not contain .SQLite3")
        zf.extractall(tmp_path)

    sqlite_path = tmp_path / ".SQLite3"
    if not sqlite_path.exists():
        fail("Failed to extract .SQLite3 from module archive")
    return sqlite_path


def repack_module(module_zip_path: Path, tmp_path: Path) -> None:
    with zipfile.ZipFile(
        module_zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as zf:
        for file_path in sorted(tmp_path.rglob("*")):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(tmp_path).as_posix())


def read_verse(cur: sqlite3.Cursor, proposal: dict) -> tuple[str, str]:
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
            f"Verse not found in module: {proposal['book_number']}:"
            f"{proposal['chapter_number']}:{proposal['verse_number']}"
        )

    text = rows[0][0] if rows[0][0] is not None else ""
    book_title = rows[0][1] if rows[0][1] is not None else f"Book {proposal['book_number']}"
    return text, book_title


def update_verse(cur: sqlite3.Cursor, proposal: dict) -> int:
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
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 1


def apply_proposal(repo_root: Path, proposal_path: Path) -> dict:
    modules_dir = repo_root / "modules"
    if not modules_dir.exists():
        fail(f"modules directory not found at {modules_dir}")

    proposal = load_proposal(proposal_path)
    registry = load_registry(repo_root)
    downloads = registry_downloads(registry)

    module_index, module_entry = resolve_module_entry(downloads, proposal)
    module_file = module_file_from_entry(module_entry)
    module_zip_path = modules_dir / f"{module_file}.zip"
    if not module_zip_path.exists():
        fail(f"Module ZIP not found: {module_zip_path}")

    with tempfile.TemporaryDirectory(prefix="proposal-apply-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        sqlite_path = extract_module(module_zip_path, tmp_path)

        conn = sqlite3.connect(str(sqlite_path))
        try:
            cur = conn.cursor()
            original_text, book_title = read_verse(cur, proposal)
            updated_rows = update_verse(cur, proposal)
            conn.commit()
        finally:
            conn.close()

        repack_module(module_zip_path, tmp_path)

    comment_block = format_comment_block(module_file, proposal, book_title, original_text)
    current_cmt = module_entry.get("cmt")
    module_entry["cmt"] = f"{comment_block}\n{current_cmt}" if current_cmt else comment_block
    module_entry["upd"] = date.today().isoformat()
    bump_registry_version(registry)

    downloads[module_index] = module_entry
    save_registry(repo_root, registry)
    write_registry_zip(repo_root)

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


def proposal_targets_module(proposal: dict, module_abr: str, module_entry: dict) -> bool:
    module_file = module_file_from_entry(module_entry)
    identifiers = {module_abr, module_file}
    short_name = proposal.get("short_name")
    requested_file_name = proposal.get("requested_file_name")

    return short_name in identifiers or requested_file_name in identifiers


def find_module_proposals(repo_root: Path, module_abr: str, module_entry: dict) -> list[tuple[Path, dict]]:
    proposals_dir = repo_root / "proposals" / "module-updates"
    if not proposals_dir.exists():
        return []

    proposals: list[tuple[Path, dict]] = []
    for proposal_path in sorted(proposals_dir.glob("*.y*ml")):
        proposal = load_proposal(proposal_path)
        if proposal_targets_module(proposal, module_abr, module_entry):
            proposals.append((proposal_path, proposal))
    return proposals


def ensure_comment_block(module_entry: dict, module_file: str, proposal: dict, book_title: str) -> bool:
    current_cmt = module_entry.get("cmt") or ""
    if proposal["new_text"] in current_cmt:
        return False

    submitted_at = proposal.get("submitted_at")
    applied_date = submitted_at[:10] if isinstance(submitted_at, str) and len(submitted_at) >= 10 else None
    comment_block = format_comment_block(
        module_file,
        proposal,
        book_title,
        proposal["old_text"],
        applied_date=applied_date,
    )
    module_entry["cmt"] = f"{comment_block}\n{current_cmt}" if current_cmt else comment_block
    return True


def reapply_proposals_for_modules(repo_root: Path, module_abrs: list[str]) -> dict:
    modules_dir = repo_root / "modules"
    if not modules_dir.exists():
        fail(f"modules directory not found at {modules_dir}")

    registry = load_registry(repo_root)
    downloads = registry_downloads(registry)

    summary = {
        "modules_requested": module_abrs,
        "modules_with_proposals": 0,
        "proposals_found": 0,
        "proposals_applied": 0,
        "proposals_already_applied": 0,
        "comments_added": 0,
        "module_zip_paths": [],
        "conflicts": [],
        "modules": [],
    }

    registry_changed = False
    module_archives_changed = False
    module_plans = []

    for module_abr in module_abrs:
        module_abr = module_abr.strip()
        if not module_abr:
            continue

        module_index, module_entry = resolve_module_by_abr(downloads, module_abr)
        module_file = module_file_from_entry(module_entry)
        proposals = find_module_proposals(repo_root, module_abr, module_entry)
        module_summary = {
            "module_abr": module_abr,
            "module_file": module_file,
            "proposals_found": len(proposals),
            "applied": 0,
            "already_applied": 0,
            "comments_added": 0,
            "conflicts": [],
        }
        summary["modules"].append(module_summary)

        if not proposals:
            continue

        summary["modules_with_proposals"] += 1
        summary["proposals_found"] += len(proposals)

        module_zip_path = modules_dir / f"{module_file}.zip"
        if not module_zip_path.exists():
            fail(f"Module ZIP not found: {module_zip_path}")

        with tempfile.TemporaryDirectory(prefix=f"proposal-reapply-{module_abr}-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            sqlite_path = extract_module(module_zip_path, tmp_path)

            conn = sqlite3.connect(str(sqlite_path))
            try:
                cur = conn.cursor()
                planned_actions = []
                simulated_texts = {}
                for proposal_path, proposal in proposals:
                    verse_key = (
                        proposal["book_number"],
                        proposal["chapter_number"],
                        proposal["verse_number"],
                    )
                    stored_text, book_title = read_verse(cur, proposal)
                    current_text = simulated_texts.get(verse_key, stored_text)
                    reference = (
                        f"{module_abr} {proposal['book_number']}:"
                        f"{proposal['chapter_number']}:{proposal['verse_number']}"
                    )

                    if current_text == proposal["new_text"]:
                        module_summary["already_applied"] += 1
                        summary["proposals_already_applied"] += 1
                        planned_actions.append(
                            {
                                "proposal_path": proposal_path,
                                "proposal": proposal,
                                "book_title": book_title,
                                "status": "already_applied",
                            }
                        )
                    elif current_text == proposal["old_text"]:
                        simulated_texts[verse_key] = proposal["new_text"]
                        module_summary["applied"] += 1
                        summary["proposals_applied"] += 1
                        planned_actions.append(
                            {
                                "proposal_path": proposal_path,
                                "proposal": proposal,
                                "book_title": book_title,
                                "status": "apply",
                            }
                        )
                    else:
                        conflict = {
                            "proposal_path": str(proposal_path.relative_to(repo_root)),
                            "reference": reference,
                            "reason": "Current verse text matches neither old_text nor new_text",
                        }
                        module_summary["conflicts"].append(conflict)
                        summary["conflicts"].append(conflict)
                        continue

            finally:
                conn.close()

        module_plans.append(
            {
                "module_index": module_index,
                "module_entry": module_entry,
                "module_file": module_file,
                "module_zip_path": module_zip_path,
                "module_summary": module_summary,
                "actions": planned_actions,
            }
        )

    if summary["conflicts"]:
        summary["registry_changed"] = False
        summary["module_archives_changed"] = False
        return summary

    for plan in module_plans:
        module_entry = plan["module_entry"]
        module_file = plan["module_file"]
        module_zip_path = plan["module_zip_path"]
        module_summary = plan["module_summary"]
        actions = plan["actions"]
        actions_to_apply = [action for action in actions if action["status"] == "apply"]

        if actions_to_apply:
            with tempfile.TemporaryDirectory(prefix=f"proposal-reapply-{module_summary['module_abr']}-") as tmp_dir:
                tmp_path = Path(tmp_dir)
                sqlite_path = extract_module(module_zip_path, tmp_path)

                conn = sqlite3.connect(str(sqlite_path))
                try:
                    cur = conn.cursor()
                    for action in actions_to_apply:
                        update_verse(cur, action["proposal"])
                    conn.commit()
                finally:
                    conn.close()

                repack_module(module_zip_path, tmp_path)
                module_archives_changed = True
                summary["module_zip_paths"].append(str(module_zip_path.relative_to(repo_root)))

        for action in actions:
            if ensure_comment_block(
                module_entry,
                module_file,
                action["proposal"],
                action["book_title"],
            ):
                module_summary["comments_added"] += 1
                summary["comments_added"] += 1
                registry_changed = True

        if module_summary["applied"] or module_summary["comments_added"]:
            module_entry["upd"] = date.today().isoformat()
            downloads[plan["module_index"]] = module_entry
            registry_changed = True

    if registry_changed:
        bump_registry_version(registry)
        save_registry(repo_root, registry)

    summary["registry_changed"] = registry_changed
    summary["module_archives_changed"] = module_archives_changed
    return summary
