#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from module_proposals import ProposalError, reapply_proposals_for_modules


def parse_modules(raw_modules: str) -> list[str]:
    return [module.strip() for module in raw_modules.replace("\n", ",").split(",") if module.strip()]


def append_markdown_summary(summary_path: Path, summary: dict) -> None:
    if not summary.get("proposals_found"):
        return

    lines = [
        "",
        "## Reapplied Local Module Proposals",
        "",
        f"- Modules with local proposals: {summary.get('modules_with_proposals', 0)}",
        f"- Proposals found: {summary.get('proposals_found', 0)}",
        f"- Reapplied to downloaded archives: {summary.get('proposals_applied', 0)}",
        f"- Already present in downloaded archives: {summary.get('proposals_already_applied', 0)}",
        f"- Registry comment blocks restored: {summary.get('comments_added', 0)}",
    ]

    conflicts = summary.get("conflicts") or []
    if conflicts:
        lines.extend(["", "### Proposal Conflicts", ""])
        lines.append("These proposals need manual review because upstream changed the same verse differently:")
        lines.append("")
        for conflict in conflicts:
            lines.append(f"- `{conflict['reference']}` from `{conflict['proposal_path']}`")

    changed_archives = summary.get("module_zip_paths") or []
    if changed_archives:
        lines.extend(["", "### Updated Archives", ""])
        for archive_path in changed_archives:
            lines.append(f"- `{archive_path}`")

    with summary_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
        handle.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reapply local module proposals to updated module archives")
    parser.add_argument("--modules", required=True, help="Comma-separated or newline-separated module abbreviations")
    parser.add_argument("--summary-path", help="Optional markdown file to append a PR summary to")
    args = parser.parse_args()

    modules = parse_modules(args.modules)
    if not modules:
        print(json.dumps({"modules_requested": [], "proposals_found": 0}, ensure_ascii=False))
        return

    try:
        summary = reapply_proposals_for_modules(Path.cwd(), modules)
        if args.summary_path:
            append_markdown_summary(Path(args.summary_path), summary)
    except ProposalError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - defensive fallback for workflow logs
        print(f"Unexpected error while reapplying proposals: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary.get("conflicts"):
        print("Local proposal conflicts require manual review.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
