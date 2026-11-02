"""Synthetic whole-environment plans; no cloud credentials or real plan data."""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check_plan.py"
ENV_PROJECT = "dep-2610-dev"
PROJECT_ARGS = ["--project-id", ENV_PROJECT]
LABELS = {"project": "gcp-tf-portfolio", "env": "dev", "managed_by": "terraform"}


def rc(
    kind,
    address="module.example.resource.test",
    actions=None,
    after=None,
    after_unknown=None,
    before=None,
    project=ENV_PROJECT,
):
    """Build a change; `project` is added to before/after unless given or None."""
    actions = ["create"] if actions is None else actions
    after_unknown = {} if after_unknown is None else after_unknown
    if project is not None and isinstance(after_unknown, dict) and "project" not in after_unknown:
        if before is None and "delete" in actions:
            before = {}
        before = before if before is None or "project" in before else {**before, "project": project}
        after = after if after is None or "project" in after else {**after, "project": project}
    return {
        "mode": "managed",
        "type": kind,
        "address": address,
        "change": {
            "actions": actions,
            "before": before,
            "after": after,
            "after_unknown": after_unknown,
        },
    }


def dataset():
    return rc("google_bigquery_dataset", "module.bigquery.google_bigquery_dataset.main", after={})


def plan(*changes):
    return {"resource_changes": [dataset(), *changes]}


def cli(tmp_path, document, *args, bom=False, allow_destroy=None):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(document), encoding="utf-8-sig" if bom else "utf-8")
    env = os.environ.copy()
    env.pop("ALLOW_DESTROY", None)
    if allow_destroy is not None:
        env["ALLOW_DESTROY"] = allow_destroy
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--env", "dev", *PROJECT_ARGS, *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


CASES = [
    ("P1", "google_secret_manager_secret_version", {}, "google_secret_manager_secret", {}, []),
    ("P2", "google_service_account_key", {}, "google_service_account", {}, []),
    ("P3", "google_project_iam_binding", {}, "google_project_iam_member", {}, []),
    (
        "P4",
        "google_project_iam_member",
        {"role": "roles/editor"},
        "google_project_iam_member",
        {"role": "roles/bigquery.jobUser"},
        [],
    ),
    (
        "P5",
        "google_project_iam_member",
        {"member": "allUsers"},
        "google_project_iam_member",
        {"member": "serviceAccount:runtime@example.com"},
        [],
    ),
    (
        "P6",
        "google_storage_bucket",
        {"public_access_prevention": "inherited", "uniform_bucket_level_access": False},
        "google_storage_bucket",
        {"public_access_prevention": "enforced", "uniform_bucket_level_access": True},
        [],
    ),
    (
        "P7",
        "google_compute_firewall",
        {"allow": [{"protocol": "tcp"}], "source_ranges": ["0.0.0.0/0"]},
        "google_compute_firewall",
        {"allow": [], "source_ranges": ["0.0.0.0/0"]},
        [],
    ),
    (
        "P8",
        "google_compute_instance",
        {"network_interface": [{"access_config": [{}]}]},
        "google_compute_instance",
        {"network_interface": [{"access_config": []}]},
        [],
    ),
    (
        "P9",
        "google_service_account",
        {"labels": {"env": "test"}},
        "google_service_account",
        {"labels": LABELS},
        [],
    ),
    ("P10", "google_bigquery_table", None, "google_service_account", None, []),
    (
        "P11",
        "google_bigquery_table",
        {"deletion_protection": False},
        "google_bigquery_table",
        {"deletion_protection": True},
        ["--env", "prod"],
    ),
]


@pytest.mark.parametrize("rule,bad_type,bad,good_type,good,args", CASES)
def test_every_rule_violation_and_compliance(tmp_path, rule, bad_type, bad, good_type, good, args):
    address = f"module.example.{bad_type}.bad"
    actions = ["delete"] if rule == "P10" else ["create"]
    result = cli(tmp_path, plan(rc(bad_type, address, actions, bad)), *args)
    assert result.returncode == 1, result.stderr
    assert f"FAIL [{rule}] {address}:" in result.stdout
    result = cli(tmp_path, plan(rc(good_type, actions=actions, after=good)), *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"[{rule}]" not in result.stdout


@pytest.mark.parametrize(
    "document",
    [
        {"resource_changes": []},
        {
            "resource_changes": [
                rc("google_service_account", after={}),
            ]
        },
    ],
)
def test_positive_control(tmp_path, document):
    result = cli(tmp_path, document)
    assert result.returncode == 1
    assert "FAIL [P0]" in result.stdout
    assert "check would pass vacuously" in result.stdout


def test_only_managed_resources_count(tmp_path):
    data = dataset()
    data["mode"] = "data"
    result = cli(tmp_path, {"resource_changes": [data]})
    assert result.returncode == 1
    assert "0 resource changes checked" in result.stdout
    forbidden_data = rc("google_service_account_key", after={})
    forbidden_data["mode"] = "data"
    result = cli(tmp_path, plan(forbidden_data))
    assert result.returncode == 0
    assert "1 resource changes checked" in result.stdout


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"]])
@pytest.mark.parametrize(
    "kind",
    [
        "google_storage_bucket",
        "google_bigquery_dataset",
        "google_bigquery_table",
        "google_secret_manager_secret",
        "google_artifact_registry_repository",
    ],
)
def test_all_stateful_delete_and_replace_actions(tmp_path, kind, actions):
    result = cli(tmp_path, plan(rc(kind, actions=actions)))
    assert result.returncode == 1
    assert "[P10]" in result.stdout
    assert "--allow-destroy" in result.stdout
    assert "ALLOW_DESTROY=true" in result.stdout


@pytest.mark.parametrize(
    "args,value",
    [
        (["--allow-destroy"], None),
        ([], "true"),
        ([], "TRUE"),
    ],
)
def test_destroy_overrides(tmp_path, args, value):
    result = cli(
        tmp_path,
        plan(rc("google_bigquery_table", actions=["delete", "create"])),
        *args,
        allow_destroy=value,
    )
    assert result.returncode == 0
    assert "[P10]" not in result.stdout


def test_allow_destroy_disables_only_p10(tmp_path):
    result = cli(
        tmp_path,
        plan(
            rc("google_bigquery_table", actions=["delete"]),
            rc("google_service_account_key", "key.bad", after={}),
        ),
        "--allow-destroy",
    )
    assert result.returncode == 1
    assert "[P2] key.bad" in result.stdout
    assert "[P10]" not in result.stdout


@pytest.mark.parametrize("value", ["false", "1", "yes"])
def test_other_env_values_do_not_allow_destroy(tmp_path, value):
    assert (
        cli(
            tmp_path, plan(rc("google_bigquery_table", actions=["delete"])), allow_destroy=value
        ).returncode
        == 1
    )


@pytest.mark.parametrize(
    "kind,after",
    [
        ("google_bigquery_table", {"deletion_protection": False}),
        (
            "google_storage_bucket",
            {
                "force_destroy": True,
                "public_access_prevention": "enforced",
                "uniform_bucket_level_access": True,
            },
        ),
        ("google_project_iam_member", {"member": "user:developer@example.com"}),
        ("google_project_iam_member", {"members": ["user:developer@example.com"]}),
    ],
)
def test_prod_rules_and_dev_exemption(tmp_path, kind, after):
    result = cli(tmp_path, plan(rc(kind, "prod.bad", after=after)), "--env", "prod")
    assert result.returncode == 1
    assert "FAIL [P11] prod.bad:" in result.stdout
    result = cli(tmp_path, plan(rc(kind, after=after)))
    assert result.returncode == 0
    assert "[P11]" not in result.stdout


@pytest.mark.parametrize("field", ["member", "members"])
@pytest.mark.parametrize("member", ["allUsers", "allAuthenticatedUsers"])
def test_public_principals(tmp_path, field, member):
    value = [member] if field == "members" else member
    result = cli(tmp_path, plan(rc("google_project_iam_member", after={field: value})))
    assert result.returncode == 1
    assert "[P5]" in result.stdout


@pytest.mark.parametrize("suffix", ["_iam_binding", "_iam_policy"])
def test_authoritative_iam_suffixes(tmp_path, suffix):
    result = cli(tmp_path, plan(rc("custom_resource" + suffix, after=None, actions=["delete"])))
    assert result.returncode == 1
    assert "[P3]" in result.stdout


@pytest.mark.parametrize(
    "kind",
    [
        "google_secret_manager_secret_version",
        "google_service_account_key",
    ],
)
def test_forbidden_types_even_on_delete(tmp_path, kind):
    assert cli(tmp_path, plan(rc(kind, actions=["delete"]))).returncode == 1


def test_after_rules_do_not_check_deleted_values(tmp_path):
    result = cli(tmp_path, plan(rc("google_compute_instance", actions=["delete"])))
    assert result.returncode == 0
    assert "SKIP" not in result.stdout


@pytest.mark.parametrize(
    "kind,after,unknown,rule,args",
    [
        ("google_project_iam_member", {}, {"member": True}, "P5", []),
        ("google_project_iam_member", {}, {"role": True}, "P4", []),
        ("google_storage_bucket", {}, {}, "P6", []),
        ("google_storage_bucket", {}, True, "P6", []),
        ("google_compute_firewall", {"allow": [{}]}, {"source_ranges": True}, "P7", []),
        (
            "google_compute_instance",
            {"network_interface": [{}]},
            {"network_interface": [{"access_config": True}]},
            "P8",
            [],
        ),
        ("google_compute_instance", {}, {"network_interface": True}, "P8", []),
        ("google_service_account", {}, {"labels": True}, "P9", []),
        (
            "google_service_account",
            {"labels": {"project": "p", "managed_by": "terraform"}},
            {"labels": {"env": True}},
            "P9",
            [],
        ),
        ("google_bigquery_table", {}, {"deletion_protection": True}, "P11", ["--env", "prod"]),
    ],
)
def test_unknown_required_fields_skip(tmp_path, kind, after, unknown, rule, args):
    result = cli(
        tmp_path, plan(rc(kind, "unknown.test", after=after, after_unknown=unknown)), *args
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"SKIP [{rule}] unknown.test:" in result.stdout
    assert "unknown until apply" in result.stdout


def test_partial_unknown_members_preserve_known_violation(tmp_path):
    result = cli(
        tmp_path,
        plan(
            rc(
                "google_project_iam_binding",
                "partial.test",
                after={"members": ["allUsers", None]},
                after_unknown={"members": [False, True]},
            )
        ),
    )
    assert result.returncode == 1
    assert "SKIP [P5] partial.test:" in result.stdout
    assert "FAIL [P5] partial.test:" in result.stdout


@pytest.mark.parametrize(
    "after",
    [
        {"allow": [], "source_ranges": ["0.0.0.0/0"]},
        {"allow": [{}], "direction": "EGRESS", "source_ranges": ["0.0.0.0/0"]},
        {"allow": [{}], "direction": "INGRESS", "source_ranges": ["35.235.240.0/20"]},
    ],
)
def test_compliant_firewall_variants(tmp_path, after):
    result = cli(tmp_path, plan(rc("google_compute_firewall", after=after)))
    assert result.returncode == 0
    assert "[P7]" not in result.stdout


def test_every_network_interface_is_checked(tmp_path):
    result = cli(
        tmp_path,
        plan(
            rc(
                "google_compute_instance",
                after={
                    "network_interface": [{"access_config": []}, {"access_config": [{}]}],
                },
            )
        ),
    )
    assert result.returncode == 1
    assert "[P8]" in result.stdout


@pytest.mark.parametrize("labels", [None, {}, {"env": "dev"}, {**LABELS, "env": "test"}])
def test_labels_present_but_invalid(tmp_path, labels):
    result = cli(tmp_path, plan(rc("google_service_account", after={"labels": labels})))
    assert result.returncode == 1
    assert "[P9]" in result.stdout


def test_effective_and_terraform_labels_do_not_replace_labels(tmp_path):
    result = cli(
        tmp_path,
        plan(
            rc(
                "google_service_account",
                after={
                    "effective_labels": LABELS,
                    "terraform_labels": LABELS,
                    "labels": {},
                },
            )
        ),
    )
    assert result.returncode == 1
    assert "[P9]" in result.stdout


def test_absent_labels_are_not_required(tmp_path):
    assert cli(tmp_path, plan(rc("google_service_account", after={}))).returncode == 0


def test_bom(tmp_path):
    result = cli(tmp_path, plan(), bom=True)
    assert result.returncode == 0
    assert "PASS: 1 resource changes checked, 0 violations, 0 skipped" in result.stdout


@pytest.mark.parametrize(
    "content", ["{", "{}", "[]", '{"resource_changes": null}', '{"resource_changes": [null]}']
)
def test_invalid_input(tmp_path, content):
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--env", "dev", *PROJECT_ARGS],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "ERROR:" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_file(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "absent.json"), "--env", "dev", *PROJECT_ARGS],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "ERROR:" in result.stderr


def test_env_is_required(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "plan.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


def test_junit_failure_and_skip(tmp_path):
    report = tmp_path / "policy.xml"
    result = cli(
        tmp_path,
        plan(
            rc("google_project_iam_member", "bad.role", after={"role": "roles/editor"}),
            rc(
                "google_project_iam_member",
                "unknown.member",
                after={},
                after_unknown={"member": True},
            ),
        ),
        "--junit",
        str(report),
    )
    assert result.returncode == 1
    suite = ET.parse(report).getroot()
    assert suite.attrib["tests"] == "14"
    cases = {case.attrib["name"]: case for case in suite.findall("testcase")}
    assert set(cases) == {f"P{i}" for i in range(14)}
    failure = cases["P4"].find("failure")
    assert failure is not None
    assert "bad.role" in failure.attrib["message"]
    assert cases["P5"].find("skipped") is not None


def test_junit_failure_wins_over_skip_in_same_rule(tmp_path):
    report = tmp_path / "policy.xml"
    result = cli(
        tmp_path,
        plan(
            rc("google_project_iam_member", "bad.member", after={"member": "allUsers"}),
            rc(
                "google_project_iam_member",
                "unknown.member",
                after={},
                after_unknown={"member": True},
            ),
        ),
        "--junit",
        str(report),
    )
    assert result.returncode == 1
    case = ET.parse(report).find(".//testcase[@name='P5']")
    assert case is not None
    assert case.find("failure") is not None
    assert case.find("skipped") is None
    assert "unknown.member" in case.findtext("system-out", "")


def test_unwritable_junit_is_input_error(tmp_path):
    result = cli(tmp_path, plan(), "--junit", str(tmp_path / "absent" / "policy.xml"))
    assert result.returncode == 2
    assert "ERROR:" in result.stderr


@pytest.mark.parametrize(
    "kind,after,unknown,rule",
    [
        (
            "google_compute_firewall",
            {"allow": [{"protocol": "tcp"}], "source_ranges": ["0.0.0.0/0"]},
            {"allow": [{"ports": True}]},
            "P7",
        ),
        (
            "google_compute_instance",
            {"network_interface": [{"access_config": [{}]}]},
            {"network_interface": [{"access_config": [{"nat_ip": True}]}]},
            "P8",
        ),
    ],
)
def test_nested_computed_values_do_not_hide_known_violation(tmp_path, kind, after, unknown, rule):
    result = cli(tmp_path, plan(rc(kind, after=after, after_unknown=unknown)))
    assert result.returncode == 1
    assert f"FAIL [{rule}]" in result.stdout
    assert f"SKIP [{rule}]" not in result.stdout


@pytest.mark.parametrize("field,unknown", [("member", {}), ("role", {})])
def test_missing_computed_iam_field_skips(tmp_path, field, unknown):
    after = {"member": "serviceAccount:runtime@example.com", "role": "roles/bigquery.jobUser"}
    after.pop(field)
    result = cli(
        tmp_path, plan(rc("google_project_iam_member", after=after, after_unknown=unknown))
    )
    assert result.returncode == 0
    rule = "P5" if field == "member" else "P4"
    assert f"SKIP [{rule}]" in result.stdout


@pytest.mark.parametrize(
    "field,value",
    [("public_access_prevention", "inherited"), ("uniform_bucket_level_access", False)],
)
def test_each_bucket_public_access_control(tmp_path, field, value):
    after = {"public_access_prevention": "enforced", "uniform_bucket_level_access": True}
    after[field] = value
    result = cli(tmp_path, plan(rc("google_storage_bucket", after=after)))
    assert result.returncode == 1
    assert "[P6]" in result.stdout
    assert field in result.stdout


def test_prod_compliant_bucket_and_nonhuman_principals(tmp_path):
    result = cli(
        tmp_path,
        plan(
            rc(
                "google_storage_bucket",
                after={
                    "force_destroy": False,
                    "uniform_bucket_level_access": True,
                    "public_access_prevention": "enforced",
                    "labels": {**LABELS, "env": "prod"},
                },
            ),
            rc(
                "google_project_iam_member",
                after={
                    "role": "roles/run.invoker",
                    "member": "serviceAccount:scheduler@example.com",
                    "members": ["group:operators@example.com"],
                },
            ),
        ),
        "--env",
        "prod",
    )
    assert result.returncode == 0
    assert "[P11]" not in result.stdout


def test_junit_failure_contains_all_violating_addresses(tmp_path):
    report = tmp_path / "policy.xml"
    result = cli(
        tmp_path,
        plan(
            rc("google_service_account_key", "key.first", after={}),
            rc("google_service_account_key", "key.second", after={}),
        ),
        "--junit",
        str(report),
    )
    assert result.returncode == 1
    failure = ET.parse(report).find(".//testcase[@name='P2']/failure")
    assert failure is not None
    assert all(address in failure.attrib["message"] for address in ("key.first", "key.second"))


def project_plan(**kwargs):
    return plan(rc("google_service_account", "module.iam.google_service_account.runtime", **kwargs))


def test_p12_same_project_passes(tmp_path):
    same = {"project": "dep-2610-dev"}
    result = cli(
        tmp_path, project_plan(actions=["update"], before=same, after=same), "--env", "dev"
    )
    assert result.returncode == 0, result.stdout
    assert "[P12]" not in result.stdout


def test_p12_replace_from_dev_to_test_fails(tmp_path):
    # The 2026-10-29 incident: TEST configuration planned against DEV state.
    document = project_plan(
        actions=["delete", "create"],
        before={"project": "dep-2610-dev"},
        after={"project": "dep-2610-test"},
    )
    for project in ("dep-2610-dev", "dep-2610-test"):
        result = cli(tmp_path, document, "--project-id", project)
        assert result.returncode == 1
        assert "FAIL [P12] module.iam.google_service_account.runtime:" in result.stdout
        assert 'changes project from "dep-2610-dev" to "dep-2610-test"' in result.stdout


def test_p12_is_not_disabled_by_allow_destroy(tmp_path):
    document = project_plan(
        actions=["delete", "create"],
        before={"project": "dep-2610-dev"},
        after={"project": "dep-2610-test"},
    )
    result = cli(tmp_path, document, "--project-id", "dep-2610-test", "--allow-destroy")
    assert result.returncode == 1
    assert "[P12]" in result.stdout


def test_p12_create_in_another_project_fails(tmp_path):
    result = cli(tmp_path, project_plan(after={"project": "dep-2610-test"}))
    assert result.returncode == 1
    assert "FAIL [P12] module.iam.google_service_account.runtime:" in result.stdout
    assert "differs from --project-id dep-2610-dev" in result.stdout


def test_p12_delete_of_foreign_resource_fails(tmp_path):
    document = project_plan(actions=["delete"], before={"project": "dep-2610-test"})
    result = cli(tmp_path, document)
    assert result.returncode == 1
    assert 'before.project "dep-2610-test" differs from --project-id dep-2610-dev' in result.stdout


def test_p12_unknown_project_skips(tmp_path):
    result = cli(tmp_path, project_plan(after={}, after_unknown={"project": True}))
    assert result.returncode == 0
    assert "SKIP [P12] module.iam.google_service_account.runtime:" in result.stdout


SA_MEMBER = "google_service_account_iam_member.plan_federation"
BUCKET_MEMBER = "module.iam.google_storage_bucket_iam_member.raw_objects"


def sa_member(service_account_id):
    after = {
        "service_account_id": service_account_id,
        "role": "roles/iam.workloadIdentityUser",
        "member": "principalSet://iam.googleapis.com/example",
    }
    return rc("google_service_account_iam_member", SA_MEMBER, after=after, project=None)


def bucket_member(bucket, actions=None, before=None, after_unknown=None):
    after = {"role": "roles/storage.objectUser", "member": "serviceAccount:runtime@example.com"}
    if bucket is not None:
        after["bucket"] = bucket
    return rc(
        "google_storage_bucket_iam_member",
        BUCKET_MEMBER,
        actions,
        after,
        after_unknown,
        before=before,
        project=None,
    )


def sa_id(project):
    return f"projects/{project}/serviceAccounts/ci-plan-dev@{project}.iam.gserviceaccount.com"


def test_p12_derived_project_matching_passes(tmp_path):
    result = cli(tmp_path, plan(sa_member(sa_id("dep-2610-dev"))))
    assert result.returncode == 0, result.stdout
    assert "[P12]" not in result.stdout


def test_p12_derived_project_mismatch_fails(tmp_path):
    result = cli(tmp_path, plan(sa_member(sa_id("dep-2610-test"))))
    assert result.returncode == 1
    assert (
        f'FAIL [P12] {SA_MEMBER}: after.service_account_id is in project "dep-2610-test", '
        "not --project-id dep-2610-dev"
    ) in result.stdout


def test_p12_unparseable_derivation_source_fails(tmp_path):
    result = cli(tmp_path, plan(sa_member("ci-plan-dev@dep-2610-dev.iam.gserviceaccount.com")))
    assert result.returncode == 1
    assert f"FAIL [P12] {SA_MEMBER}: cannot determine project" in result.stdout


def test_p12_convention_project_mismatch_fails(tmp_path):
    result = cli(tmp_path, plan(bucket_member("dep-2610-test-raw")))
    assert result.returncode == 1
    assert (
        f'FAIL [P12] {BUCKET_MEMBER}: after.bucket "dep-2610-test-raw" does not start with '
        "dep-2610-dev-"
    ) in result.stdout


def test_p12_convention_accepts_state_bucket_form(tmp_path):
    # State stores bucket IAM members as "b/<name>"; config passes the plain name.
    change = bucket_member("dep-2610-dev-raw", ["update"], before={"bucket": "b/dep-2610-dev-raw"})
    result = cli(tmp_path, plan(change))
    assert result.returncode == 0, result.stdout
    assert "[P12]" not in result.stdout


def test_p12_convention_value_unknown_skips(tmp_path):
    result = cli(tmp_path, plan(bucket_member(None, after_unknown={"bucket": True})))
    assert result.returncode == 0, result.stdout
    assert f"SKIP [P12] {BUCKET_MEMBER}: bucket unknown until apply" in result.stdout


def test_p12_exempt_type_skips_with_reason(tmp_path):
    budget = rc(
        "google_billing_budget",
        "google_billing_budget.portfolio",
        after={"display_name": "portfolio"},
        project=None,
    )
    result = cli(tmp_path, plan(budget))
    assert result.returncode == 0, result.stdout
    assert (
        "SKIP [P12] google_billing_budget.portfolio: "
        "exempt (scoped to a billing account, not to a project)"
    ) in result.stdout
    assert "FAIL" not in result.stdout


def test_p12_unknown_type_without_project_fails(tmp_path):
    widget = rc("google_example_widget", "google_example_widget.x", after={}, project=None)
    result = cli(tmp_path, plan(widget))
    assert result.returncode == 1
    assert (
        "FAIL [P12] google_example_widget.x: cannot determine project for "
        "google_example_widget; add a derivation or an explicit exemption"
    ) in result.stdout


def test_project_id_is_required(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan()), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--env", "dev"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--project-id" in result.stderr


TABLE = 'module.bigquery.google_bigquery_table.native["ookla_tiles"]'
BUCKET = "module.storage.google_storage_bucket.raw"
PROTECTED_BUCKET = {
    "public_access_prevention": "enforced",
    "uniform_bucket_level_access": True,
    "force_destroy": False,
}


def allow_file(tmp_path, *lines):
    path = tmp_path / "dev.allow-destroy"
    path.write_text("\n".join(["# reviewed in MR !1", "", *lines]) + "\n", encoding="utf-8")
    return ["--allow-destroy-file", str(path)]


def unprotect_table():
    return rc(
        "google_bigquery_table",
        TABLE,
        ["update"],
        before={"deletion_protection": True},
        after={"deletion_protection": False},
    )


def unprotect_bucket():
    return rc(
        "google_storage_bucket",
        BUCKET,
        ["update"],
        before=PROTECTED_BUCKET,
        after={**PROTECTED_BUCKET, "force_destroy": True},
    )


@pytest.mark.parametrize("change,address", [(unprotect_table, TABLE), (unprotect_bucket, BUCKET)])
def test_p13_relaxing_protection_without_file_fails(tmp_path, change, address):
    result = cli(tmp_path, plan(change()))
    assert result.returncode == 1
    assert f"FAIL [P13] {address}:" in result.stdout


@pytest.mark.parametrize("change,address", [(unprotect_table, TABLE), (unprotect_bucket, BUCKET)])
def test_p13_listed_address_passes(tmp_path, change, address):
    result = cli(tmp_path, plan(change()), *allow_file(tmp_path, address))
    assert result.returncode == 0, result.stdout
    assert "[P13]" not in result.stdout


def test_p13_other_address_in_file_does_not_allow(tmp_path):
    result = cli(tmp_path, plan(unprotect_table()), *allow_file(tmp_path, BUCKET))
    assert result.returncode == 1
    assert f"FAIL [P13] {TABLE}:" in result.stdout


@pytest.mark.parametrize("args,env_value", [(["--allow-destroy"], None), ([], "true")])
def test_p13_is_not_disabled_by_global_override(tmp_path, args, env_value):
    result = cli(tmp_path, plan(unprotect_table()), *args, allow_destroy=env_value)
    assert result.returncode == 1
    assert f"FAIL [P13] {TABLE}:" in result.stdout


def test_p13_unrelated_attribute_changes_do_not_trigger(tmp_path):
    table = rc(
        "google_bigquery_table",
        TABLE,
        ["update"],
        before={"deletion_protection": True, "description": "old"},
        after={"deletion_protection": True, "description": "new"},
    )
    bucket = rc(
        "google_storage_bucket",
        BUCKET,
        ["update"],
        before={**PROTECTED_BUCKET, "labels": None},
        after={**PROTECTED_BUCKET, "storage_class": "NEARLINE"},
    )
    tightening = rc(
        "google_bigquery_table",
        'module.bigquery.google_bigquery_table.native["load_log"]',
        ["update"],
        before={"deletion_protection": False},
        after={"deletion_protection": True},
    )
    result = cli(tmp_path, plan(table, bucket, tightening))
    assert result.returncode == 0, result.stdout
    assert "[P13]" not in result.stdout


def test_p13_ignores_non_stateful_types(tmp_path):
    job = rc(
        "google_cloud_run_v2_job",
        "module.ingest_job.google_cloud_run_v2_job.ingest[0]",
        ["update"],
        before={"deletion_protection": True},
        after={"deletion_protection": False},
    )
    result = cli(tmp_path, plan(job))
    assert "[P13]" not in result.stdout


def test_p13_unknown_value_skips(tmp_path):
    change = rc(
        "google_bigquery_table",
        TABLE,
        ["update"],
        before={"deletion_protection": True},
        after={},
        after_unknown={"deletion_protection": True},
    )
    result = cli(tmp_path, plan(change))
    assert result.returncode == 0
    assert f"SKIP [P13] {TABLE}:" in result.stdout


def test_allow_file_makes_p10_address_specific(tmp_path):
    other = 'module.bigquery.google_bigquery_table.native["mart_quarterly_performance"]'
    document = plan(
        rc("google_bigquery_table", TABLE, ["delete", "create"], after={}),
        rc("google_bigquery_table", other, ["delete"], after=None),
    )
    result = cli(tmp_path, document, *allow_file(tmp_path, TABLE))
    assert result.returncode == 1
    assert f"[P10] {TABLE}:" not in result.stdout
    assert f"FAIL [P10] {other}:" in result.stdout


def test_missing_allow_file_is_input_error(tmp_path):
    result = cli(tmp_path, plan(), "--allow-destroy-file", str(tmp_path / "absent"))
    assert result.returncode == 2
    assert "ERROR:" in result.stderr


def delete_table():
    return rc("google_bigquery_table", TABLE, ["delete"], before={"deletion_protection": False})


def test_stale_allow_entry_fails(tmp_path):
    args = allow_file(tmp_path, TABLE)
    result = cli(tmp_path, plan(), *args)
    assert result.returncode == 1
    assert f"FAIL [P10] {TABLE}: stale allow-destroy entry; remove it from {args[1]}" in (
        result.stdout
    )


def test_allow_entry_used_only_by_p13_is_not_stale(tmp_path):
    result = cli(tmp_path, plan(unprotect_table()), *allow_file(tmp_path, TABLE))
    assert result.returncode == 0, result.stdout
    assert "stale" not in result.stdout


def test_allow_entry_used_by_p10_is_not_stale(tmp_path):
    result = cli(tmp_path, plan(delete_table()), *allow_file(tmp_path, TABLE))
    assert result.returncode == 0, result.stdout
    assert "stale" not in result.stdout


def test_allow_file_comments_and_blank_lines_are_ignored(tmp_path):
    args = allow_file(tmp_path, "   ", "  # indented comment", TABLE, "", "# trailing")
    result = cli(tmp_path, plan(delete_table()), *args)
    assert result.returncode == 0, result.stdout
    assert "[P10]" not in result.stdout


def test_duplicate_allow_entries_fail(tmp_path):
    args = allow_file(tmp_path, TABLE, TABLE)
    result = cli(tmp_path, plan(delete_table()), *args)
    assert result.returncode == 1
    assert f"FAIL [P10] {TABLE}: duplicate allow-destroy entry (2 times in {args[1]})" in (
        result.stdout
    )
    assert "stale" not in result.stdout


def test_without_allow_file_there_is_no_allowance_and_no_stale_check(tmp_path):
    result = cli(tmp_path, plan(delete_table()))
    assert result.returncode == 1
    assert f"FAIL [P10] {TABLE}: stateful delete/replacement" in result.stdout
    assert "stale" not in result.stdout
