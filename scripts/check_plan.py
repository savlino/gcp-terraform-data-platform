"""Check a whole-environment Terraform JSON plan using standard-library code.

ID   Check                                                   Applies to
P0   At least one managed change and google_bigquery_dataset; Whole plan
     otherwise "check would pass vacuously".
P1   No google_secret_manager_secret_version.                 All managed changes
P2   No google_service_account_key.                           All managed changes
P3   No type ending in _iam_binding or _iam_policy.            All managed changes
P4   role not in roles/owner, roles/editor, roles/viewer.      Non-null after
P5   Neither member nor any members item is allUsers          Non-null after
     or allAuthenticatedUsers.
P6   public_access_prevention == "enforced" and                Buckets, non-null after
     uniform_bucket_level_access == true.
P7   Non-empty allow with INGRESS direction (default)         Firewalls, non-null after
     must not include source_ranges "0.0.0.0/0"; deny is OK.
P8   Every network_interface has empty access_config.         Instances, non-null after
P9   If labels exists, require project, env, managed_by       Non-null after with labels
     keys and labels.env == --env; ignore effective_labels
     and terraform_labels.
P10  No delete/replace of google_storage_bucket,               Delete actions
     google_bigquery_dataset, google_bigquery_table,
     google_secret_manager_secret or
     google_artifact_registry_repository unless allowed.
P11  google_bigquery_table.deletion_protection == true;        PROD, non-null after
     google_storage_bucket.force_destroy == false;
     no member or members item starting with "user:".

Unknown required fields produce explicit SKIP findings, not violations.
This checks proposed configuration, not effective cloud permissions.
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass

type JSON = None | bool | int | float | str | list[JSON] | dict[str, JSON]

RULES = tuple(f"P{number}" for number in range(12))
BASIC_ROLES = {"roles/owner", "roles/editor", "roles/viewer"}
PUBLIC_MEMBERS = {"allUsers", "allAuthenticatedUsers"}
STATEFUL = {
    "google_storage_bucket",
    "google_bigquery_dataset",
    "google_bigquery_table",
    "google_secret_manager_secret",
    "google_artifact_registry_repository",
}


@dataclass(frozen=True)
class Finding:
    rule: str
    address: str
    message: str
    skipped: bool = False


@dataclass(frozen=True)
class Resource:
    kind: str
    address: str
    actions: list[str]
    after: dict[str, JSON] | None
    unknown: JSON


def managed_resources(document: JSON) -> list[Resource]:
    if not isinstance(document, dict) or "resource_changes" not in document:
        raise ValueError("plan must contain resource_changes")
    changes = document["resource_changes"]
    if not isinstance(changes, list):
        raise ValueError("resource_changes must be an array")
    resources = []
    for entry in changes:
        if not isinstance(entry, dict):
            raise ValueError("each resource change must be an object")
        if entry.get("mode") != "managed":
            continue
        kind, address, change = entry.get("type"), entry.get("address"), entry.get("change")
        if not isinstance(kind, str) or not isinstance(address, str):
            raise ValueError("managed resource type and address must be strings")
        if not isinstance(change, dict) or "after" not in change:
            raise ValueError(f"{address}: change must contain after")
        actions, after = change.get("actions"), change["after"]
        if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
            raise ValueError(f"{address}: actions must be an array of strings")
        if after is not None and not isinstance(after, dict):
            raise ValueError(f"{address}: after must be an object or null")
        resources.append(
            Resource(kind, address, [str(a) for a in actions], after, change.get("after_unknown"))
        )
    return resources


def has_unknown(value: JSON) -> bool:
    if isinstance(value, dict):
        return any(has_unknown(item) for item in value.values())
    if isinstance(value, list):
        return any(has_unknown(item) for item in value)
    return value is True


class Check:
    def __init__(self, resource: Resource, findings: list[Finding]) -> None:
        self.resource = resource
        self.findings = findings

    def fail(self, rule: str, message: str) -> None:
        self.findings.append(Finding(rule, self.resource.address, message))

    def field(
        self, rule: str, *path: str | int, optional: bool = False, deep: bool = True
    ) -> tuple[bool, JSON]:
        value: JSON = self.resource.after
        unknown = self.resource.unknown
        missing = False
        for key in path:
            if unknown is True:
                break
            if isinstance(key, int):
                unknown = unknown[key] if isinstance(unknown, list) and key < len(unknown) else None
                if isinstance(value, list) and key < len(value):
                    value = value[key]
                else:
                    missing, value = True, None
            else:
                unknown = unknown.get(key) if isinstance(unknown, dict) else None
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    missing, value = True, None
        if (has_unknown(unknown) if deep else unknown is True) or (missing and not optional):
            name = ".".join(str(key) for key in path)
            self.findings.append(
                Finding(rule, self.resource.address, f"{name} unknown until apply", skipped=True)
            )
            return False, None
        return True, value

    def require(self, rule: str, field: str, expected: JSON) -> None:
        known, value = self.field(rule, field)
        matches = value is expected if isinstance(expected, bool) else value == expected
        if known and not matches:
            self.fail(rule, f"{field} must be {json.dumps(expected)}")

    def principals(self, rule: str, prod: bool = False) -> None:
        for field in ("member", "members"):
            # A partially unknown collection must not hide a known public/user member.
            after = self.resource.after or {}
            value = after.get(field)
            required = field == "member" and self.resource.kind.endswith("_iam_member")
            self.field(rule, field, optional=not required)
            members = value if isinstance(value, list) else [value]
            for member in members:
                if isinstance(member, str):
                    if prod and member.startswith("user:"):
                        self.fail(rule, f"human principal {member} is forbidden in prod")
                    elif not prod and member in PUBLIC_MEMBERS:
                        self.fail(rule, f"public principal {member}")


def evaluate(resources: list[Resource], env: str, allow_destroy: bool) -> list[Finding]:
    findings: list[Finding] = []
    if not resources or not any(r.kind == "google_bigquery_dataset" for r in resources):
        findings.append(Finding("P0", "<plan>", "check would pass vacuously"))
    for resource in resources:
        check = Check(resource, findings)
        if resource.kind == "google_secret_manager_secret_version":
            check.fail("P1", "secret versions must never be in Terraform state")
        if resource.kind == "google_service_account_key":
            check.fail("P2", "service-account keys are forbidden")
        if resource.kind.endswith(("_iam_binding", "_iam_policy")):
            check.fail("P3", "authoritative IAM is forbidden; use additive IAM members")
        if not allow_destroy and resource.kind in STATEFUL and "delete" in resource.actions:
            check.fail(
                "P10",
                "stateful delete/replacement; allow intentionally with --allow-destroy "
                "or ALLOW_DESTROY=true",
            )
        if resource.after is None:
            continue
        iam = resource.kind.endswith(("_iam_member", "_iam_binding"))
        known, role = check.field("P4", "role", optional=not iam)
        if known and isinstance(role, str) and role in BASIC_ROLES:
            check.fail("P4", f"basic role {role}")
        check.principals("P5")
        if resource.kind == "google_storage_bucket":
            check.require("P6", "public_access_prevention", "enforced")
            check.require("P6", "uniform_bucket_level_access", True)
        if resource.kind == "google_compute_firewall":
            check_firewall(check)
        if resource.kind == "google_compute_instance":
            check_interfaces(check)
        if (
            "labels" in resource.after
            or resource.unknown is True
            or (isinstance(resource.unknown, dict) and "labels" in resource.unknown)
        ):
            check_labels(check, env)
        if env == "prod":
            if resource.kind == "google_bigquery_table":
                check.require("P11", "deletion_protection", True)
            if resource.kind == "google_storage_bucket":
                check.require("P11", "force_destroy", False)
            check.principals("P11", prod=True)
    return findings


def check_firewall(check: Check) -> None:
    allow_known, allow = check.field("P7", "allow", deep=False)
    if allow_known and not allow:
        return
    known, direction = check.field("P7", "direction", optional=True)
    if known and direction not in (None, "INGRESS"):
        return
    if not known or not allow_known:
        return
    check.field("P7", "source_ranges")
    ranges = (check.resource.after or {}).get("source_ranges")
    if isinstance(ranges, list) and "0.0.0.0/0" in ranges:
        check.fail("P7", "INGRESS allow includes 0.0.0.0/0")


def check_interfaces(check: Check) -> None:
    # Inspect known interfaces even when a different interface is unknown.
    after = check.resource.after or {}
    interfaces = after.get("network_interface")
    if not isinstance(interfaces, list):
        check.field("P8", "network_interface")
        return
    if check.resource.unknown is True:
        check.field("P8", "network_interface")
        return
    unknown = check.resource.unknown
    if isinstance(unknown, dict) and unknown.get("network_interface") is True:
        check.field("P8", "network_interface")
        return
    if not interfaces:
        check.field("P8", "network_interface")
    for index in range(len(interfaces)):
        known, configs = check.field("P8", "network_interface", index, "access_config", deep=False)
        if known and configs:
            check.fail("P8", f"network_interface.{index}.access_config must be empty")


def check_labels(check: Check, env: str) -> None:
    after = check.resource.after or {}
    labels = after.get("labels")
    if labels is None:
        known, _ = check.field("P9", "labels")
        if known:
            check.fail("P9", "labels must contain project, env and managed_by")
        return
    for key in ("project", "env", "managed_by"):
        # Missing label keys are violations, unlike missing computed resource attributes.
        known, value = check.field("P9", "labels", key, optional=True)
        if known and (not isinstance(labels, dict) or key not in labels):
            check.fail("P9", f"labels missing required key {key}")
        elif known and key == "env" and value != env:
            check.fail("P9", f"labels.env must equal {env}")


def write_junit(path: str, findings: list[Finding]) -> None:
    failed = {finding.rule for finding in findings if not finding.skipped}
    skipped = {finding.rule for finding in findings if finding.skipped} - failed
    suite = ET.Element(
        "testsuite",
        name="terraform-plan-policy",
        tests=str(len(RULES)),
        failures=str(len(failed)),
        skipped=str(len(skipped)),
    )
    for rule in RULES:
        case = ET.SubElement(suite, "testcase", classname="plan-policy", name=rule)
        relevant = [f for f in findings if f.rule == rule]
        failures = [f"{f.address}: {f.message}" for f in relevant if not f.skipped]
        skips = [f"{f.address}: {f.message}" for f in relevant if f.skipped]
        if failures:
            ET.SubElement(case, "failure", message="\n".join(failures)).text = "\n".join(failures)
        elif skips:
            ET.SubElement(case, "skipped", message="\n".join(skips))
        if skips:
            ET.SubElement(case, "system-out").text = "\n".join(skips)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_json")
    parser.add_argument("--env", required=True, choices=("dev", "test", "prod"))
    parser.add_argument("--junit")
    parser.add_argument("--allow-destroy", action="store_true", help="Disable P10 only")
    args = parser.parse_args(argv)
    try:
        with open(args.plan_json, encoding="utf-8-sig") as source:
            document = json.load(source)
        resources = managed_resources(document)
        allow_destroy = args.allow_destroy or os.environ.get("ALLOW_DESTROY", "").lower() == "true"
        findings = evaluate(resources, args.env, allow_destroy)
        if args.junit:
            write_junit(args.junit, findings)
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    for finding in findings:
        status = "SKIP" if finding.skipped else "FAIL"
        print(f"{status} [{finding.rule}] {finding.address}: {finding.message}")
    violations = sum(not f.skipped for f in findings)
    skipped = sum(f.skipped for f in findings)
    status = "FAIL" if violations else "PASS"
    print(
        f"{status}: {len(resources)} resource changes checked, "
        f"{violations} violations, {skipped} skipped"
    )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
