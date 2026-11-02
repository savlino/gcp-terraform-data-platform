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
     google_artifact_registry_repository unless --allow-destroy,
     ALLOW_DESTROY=true or the address is in --allow-destroy-file.
     Every --allow-destroy-file entry must allow at least one
     P10 or P13 finding; stale or duplicate entries fail (P10).
P11  google_bigquery_table.deletion_protection == true;        PROD, non-null after
     google_storage_bucket.force_destroy == false;
     no member or members item starting with "user:".
P12  The project of every change equals --project-id; a   Before and after
     project change (including replacements) fails.       (deletes too)
     Resolution order: (1) the project attribute;
     (2) DERIVED from an identifying attribute;
     (3) naming CONVENTION (bucket name starts with
     <project_id>-); (4) EXEMPT type -> SKIP with reason;
     otherwise FAIL "cannot determine project".
P13  On the P10 types, deletion_protection true -> false  Before and after both
     or force_destroy false -> true fails unless the      non-null
     address is in --allow-destroy-file (--allow-destroy
     and ALLOW_DESTROY do not disable it).

--allow-destroy-file lists one resource address per line; blank lines and
lines starting with # are ignored; each address may appear only once.

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

RULES = tuple(f"P{number}" for number in range(14))
BASIC_ROLES = {"roles/owner", "roles/editor", "roles/viewer"}
PUBLIC_MEMBERS = {"allUsers", "allAuthenticatedUsers"}
STATEFUL = {
    "google_storage_bucket",
    "google_bigquery_dataset",
    "google_bigquery_table",
    "google_secret_manager_secret",
    "google_artifact_registry_repository",
}


# P12 project resolution for managed types without a `project` attribute. Only
# types present in our plans are listed (checked 2026-11-01):
#   infra (dev/test/prod): google_storage_bucket_iam_member
#   bootstrap: google_service_account_iam_member, google_storage_bucket_iam_member,
#              google_billing_budget (bootstrap plans are not policy-checked today)
# Any other project-less type fails P12 until it is added to one of these tables.


def project_from_service_account_id(value: str) -> str | None:
    """Return <p> from projects/<p>/serviceAccounts/<email>."""
    parts = value.split("/")
    if len(parts) == 4 and parts[0] == "projects" and parts[2] == "serviceAccounts":
        return parts[1] if parts[1] not in ("", "-") else None
    return None


DERIVED = {
    "google_service_account_iam_member": ("service_account_id", project_from_service_account_id),
}
# Relies on the naming rule in .github/copilot-instructions.md: buckets are named
# <project_id>-<purpose>. State stores the bucket IAM `bucket` value as "b/<name>".
CONVENTION = {"google_storage_bucket_iam_member": "bucket"}
EXEMPT = {"google_billing_budget": "scoped to a billing account, not to a project"}


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
    before: dict[str, JSON] | None
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
        actions, after, before = change.get("actions"), change["after"], change.get("before")
        if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
            raise ValueError(f"{address}: actions must be an array of strings")
        if after is not None and not isinstance(after, dict):
            raise ValueError(f"{address}: after must be an object or null")
        if before is not None and not isinstance(before, dict):
            raise ValueError(f"{address}: before must be an object or null")
        resources.append(
            Resource(
                kind, address, [str(a) for a in actions], before, after, change.get("after_unknown")
            )
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


def evaluate(
    resources: list[Resource],
    env: str,
    project_id: str,
    allow_destroy: bool,
    allow_entries: tuple[str, ...] = (),
    allow_file: str = "the allow-destroy file",
) -> list[Finding]:
    findings: list[Finding] = []
    allowed = frozenset(allow_entries)
    used: set[str] = set()
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
        check_project(check, project_id)
        stateful = resource.kind in STATEFUL
        allowed_address = resource.address in allowed
        if stateful and "delete" in resource.actions:
            if allowed_address:
                used.add(resource.address)
            elif not allow_destroy:
                check.fail(
                    "P10",
                    "stateful delete/replacement; allow intentionally by listing this address "
                    "in the allow-destroy file (or --allow-destroy / ALLOW_DESTROY=true)",
                )
        if stateful and check_protection(check, allowed_address) and allowed_address:
            used.add(resource.address)
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
    findings.extend(allow_list_findings(allow_entries, used, allow_file))
    return findings


def allow_list_findings(entries: tuple[str, ...], used: set[str], allow_file: str) -> list[Finding]:
    findings = []
    for address in dict.fromkeys(entries):
        count = entries.count(address)
        if count > 1:
            findings.append(
                Finding(
                    "P10",
                    address,
                    f"duplicate allow-destroy entry ({count} times in {allow_file}); "
                    "list each address once",
                )
            )
        if address not in used:
            findings.append(
                Finding("P10", address, f"stale allow-destroy entry; remove it from {allow_file}")
            )
    return findings


def check_project(check: Check, project_id: str) -> None:
    resource = check.resource
    unknown = resource.unknown
    has_project = (
        any(
            isinstance(side, dict) and "project" in side
            for side in (resource.before, resource.after)
        )
        or isinstance(unknown, dict)
        and "project" in unknown
    )
    if has_project:
        check_project_attribute(check, project_id)
    elif resource.kind in DERIVED:
        attribute, derive = DERIVED[resource.kind]
        for side, value in identifying_values(check, attribute):
            project = derive(value) if isinstance(value, str) else None
            if project is None:
                check.fail(
                    "P12",
                    f"cannot determine project for {resource.kind} from "
                    f"{side}.{attribute} {json.dumps(value)}",
                )
            elif project != project_id:
                check.fail(
                    "P12",
                    f"{side}.{attribute} is in project {json.dumps(project)}, "
                    f"not --project-id {project_id}",
                )
    elif resource.kind in CONVENTION:
        attribute = CONVENTION[resource.kind]
        for side, value in identifying_values(check, attribute):
            name = value.removeprefix("b/") if isinstance(value, str) else ""
            if not name:
                check.fail(
                    "P12",
                    f"cannot determine project for {resource.kind} from "
                    f"{side}.{attribute} {json.dumps(value)}",
                )
            elif not name.startswith(f"{project_id}-"):
                check.fail(
                    "P12",
                    f"{side}.{attribute} {json.dumps(name)} does not start with "
                    f"{project_id}- (bucket naming convention <project_id>-<purpose>)",
                )
    elif resource.kind in EXEMPT:
        check.findings.append(
            Finding("P12", resource.address, f"exempt ({EXEMPT[resource.kind]})", skipped=True)
        )
    elif unknown is True:
        check_project_attribute(check, project_id)
    else:
        check.fail(
            "P12",
            f"cannot determine project for {resource.kind}; "
            "add a derivation or an explicit exemption",
        )


def identifying_values(check: Check, attribute: str) -> list[tuple[str, JSON]]:
    values: list[tuple[str, JSON]] = []
    if check.resource.before is not None:
        values.append(("before", check.resource.before.get(attribute)))
    if check.resource.after is not None:
        known, value = check.field("P12", attribute)
        if known:
            values.append(("after", value))
    return values


def check_project_attribute(check: Check, project_id: str) -> None:
    before, after = check.resource.before, check.resource.after
    before_has_project = isinstance(before, dict) and "project" in before
    after_has_project = isinstance(after, dict) and "project" in after
    old = before.get("project") if before_has_project else None
    new_known, new = (
        check.field("P12", "project", optional=True) if after is not None else (True, None)
    )
    if before_has_project and old != project_id:
        check.fail(
            "P12", f"before.project {json.dumps(old)} differs from --project-id {project_id}"
        )
    if after_has_project and new_known and new != project_id:
        check.fail("P12", f"after.project {json.dumps(new)} differs from --project-id {project_id}")
    if before_has_project and after_has_project and new_known and old != new:
        check.fail("P12", f"changes project from {json.dumps(old)} to {json.dumps(new)}")


def check_protection(check: Check, allowed: bool) -> bool:
    """Report P13 unless allowed; return whether a known relaxation was found."""
    before, after = check.resource.before, check.resource.after
    if before is None or after is None:
        return False
    relaxed = False
    for field, protected in (("deletion_protection", True), ("force_destroy", False)):
        if before.get(field) is not protected:
            continue
        known, value = check.field("P13", field, optional=True)
        if known and field in after and value is not protected:
            relaxed = True
            if not allowed:
                check.fail(
                    "P13",
                    f"{field} relaxed from {json.dumps(protected)} to {json.dumps(value)}; "
                    "list this address in the allow-destroy file to allow it",
                )
    return relaxed


def read_allow_list(path: str) -> tuple[str, ...]:
    with open(path, encoding="utf-8-sig") as source:
        lines = (line.strip() for line in source)
        return tuple(line for line in lines if line and not line.startswith("#"))


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
    parser.add_argument("--project-id", required=True, help="GCP project of the environment")
    parser.add_argument("--junit")
    parser.add_argument("--allow-destroy", action="store_true", help="Disable P10 only")
    parser.add_argument(
        "--allow-destroy-file", help="Addresses exempt from P10 and P13, one per line"
    )
    args = parser.parse_args(argv)
    try:
        with open(args.plan_json, encoding="utf-8-sig") as source:
            document = json.load(source)
        resources = managed_resources(document)
        allow_destroy = args.allow_destroy or os.environ.get("ALLOW_DESTROY", "").lower() == "true"
        entries = read_allow_list(args.allow_destroy_file) if args.allow_destroy_file else ()
        findings = evaluate(
            resources,
            args.env,
            args.project_id,
            allow_destroy,
            entries,
            args.allow_destroy_file or "the allow-destroy file",
        )
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
