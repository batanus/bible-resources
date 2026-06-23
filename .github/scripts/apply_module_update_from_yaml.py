#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

from module_proposals import ProposalError, apply_proposal


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply module verse update proposal from YAML")
    parser.add_argument("--proposal-path", required=True, help="Path to proposal YAML file")
    args = parser.parse_args()

    repo_root = Path.cwd()
    proposal_path = (repo_root / args.proposal_path).resolve()

    if not proposal_path.exists():
        print(f"Proposal file not found: {proposal_path}", file=sys.stderr)
        sys.exit(1)

    try:
        summary = apply_proposal(repo_root, proposal_path)
    except ProposalError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # pragma: no cover - defensive fallback for workflow logs
        print(f"Unexpected error while applying proposal: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
