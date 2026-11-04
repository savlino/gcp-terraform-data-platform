"""Destroy guard for the bootstrap root module's JSON plan.

Bootstrap holds the Terraform state bucket, WIF, CI identities, the shared
Artifact Registry and the budget. A deletion there can lose state or lock CI
out, so every managed change whose actions contain "delete" (including
delete/create replacements) fails unless its address is listed in
--allow-destroy-file. The file has the same format and stale-entry rule as
check_plan.py: one address per line, blank lines and # comments ignored; each
entry must allow a delete in this plan, and duplicates fail.

Output: a Terraform-style summary (N to add, N to change, N to destroy), every
address to be destroyed, then FAIL lines and a PASS/FAIL line.
Exit codes: 0 no violations, 1 violations, 2 input/usage error.
"""

import argparse
import json
import sys

from check_plan import Finding, allow_list_findings, managed_resources, read_allow_list


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_json")
    parser.add_argument(
        "--allow-destroy-file", help="Addresses allowed to be deleted, one per line"
    )
    args = parser.parse_args(argv)
    try:
        with open(args.plan_json, encoding="utf-8-sig") as source:
            resources = managed_resources(json.load(source))
        entries = read_allow_list(args.allow_destroy_file) if args.allow_destroy_file else ()
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    add = sum("create" in r.actions for r in resources)
    change = sum(r.actions == ["update"] for r in resources)
    destroyed = [r for r in resources if "delete" in r.actions]
    forget = sum("forget" in r.actions for r in resources)
    summary = f"Bootstrap plan: {add} to add, {change} to change, {len(destroyed)} to destroy"
    print(summary + (f", {forget} to forget." if forget else "."))
    if destroyed:
        print("To destroy:")
        for resource in destroyed:
            kind = "replace" if "create" in resource.actions else "delete"
            print(f"  - {resource.address} ({kind})")

    allowed = frozenset(entries)
    allow_file = args.allow_destroy_file or "the allow-destroy file"
    findings = [
        Finding(
            "P10",
            r.address,
            f"bootstrap delete/replacement; list this address in {allow_file} to allow it",
        )
        for r in destroyed
        if r.address not in allowed
    ]
    used = {r.address for r in destroyed if r.address in allowed}
    findings.extend(allow_list_findings(entries, used, allow_file))
    for finding in findings:
        print(f"FAIL [{finding.rule}] {finding.address}: {finding.message}")
    status = "FAIL" if findings else "PASS"
    print(f"{status}: {len(resources)} resource changes checked, {len(findings)} violations")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
