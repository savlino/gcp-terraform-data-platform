"""Synthetic bootstrap plans for the destroy guard; no real plan data."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_bootstrap_plan.py"
STATE = "google_storage_bucket.terraform_state"
SA = 'google_service_account.ci_plan["dev"]'


def rc(address, actions, kind="google_service_account"):
    after = None if actions == ["delete"] else {}
    before = None if actions == ["create"] else {}
    return {
        "mode": "managed",
        "type": kind,
        "address": address,
        "change": {"actions": actions, "before": before, "after": after, "after_unknown": {}},
    }


def run(tmp_path, *changes, allow=None, bom=False):
    path = tmp_path / "bootstrap.plan.json"
    document = {"resource_changes": list(changes)}
    path.write_text(json.dumps(document), encoding="utf-8-sig" if bom else "utf-8")
    args = [sys.executable, str(SCRIPT), str(path)]
    if allow is not None:
        allow_path = tmp_path / "allow-destroy"
        allow_path.write_text("\n".join(allow) + "\n", encoding="utf-8")
        args += ["--allow-destroy-file", str(allow_path)]
    return subprocess.run(args, capture_output=True, text=True, check=False)


def test_summary_counts_like_terraform_and_lists_destroys(tmp_path):
    result = run(
        tmp_path,
        rc("a.new", ["create"]),
        rc("b.changed", ["update"]),
        rc("c.same", ["no-op"]),
        rc("d.gone", ["delete"]),
        rc("e.replaced", ["delete", "create"]),
        rc("f.forgotten", ["forget"]),
        allow=["d.gone", "e.replaced"],
    )
    assert result.returncode == 0, result.stdout
    assert "Bootstrap plan: 2 to add, 1 to change, 2 to destroy, 1 to forget." in result.stdout
    assert "  - d.gone (delete)" in result.stdout
    assert "  - e.replaced (replace)" in result.stdout


def test_no_deletes_passes_without_file(tmp_path):
    result = run(tmp_path, rc(SA, ["create"]), rc("x", ["update"]))
    assert result.returncode == 0, result.stdout
    assert "Bootstrap plan: 1 to add, 1 to change, 0 to destroy." in result.stdout
    assert "To destroy:" not in result.stdout


def test_unlisted_delete_fails(tmp_path):
    result = run(tmp_path, rc(STATE, ["delete"], "google_storage_bucket"))
    assert result.returncode == 1
    assert f"FAIL [P10] {STATE}: bootstrap delete/replacement" in result.stdout


def test_unlisted_replace_fails_for_any_type(tmp_path):
    result = run(tmp_path, rc(SA, ["delete", "create"]))
    assert result.returncode == 1
    assert f"FAIL [P10] {SA}:" in result.stdout


def test_listed_delete_passes(tmp_path):
    result = run(tmp_path, rc(SA, ["delete"]), allow=["# reviewed", "", SA])
    assert result.returncode == 0, result.stdout
    assert "FAIL" not in result.stdout


def test_listed_address_does_not_allow_other_deletes(tmp_path):
    result = run(tmp_path, rc(SA, ["delete"]), rc(STATE, ["delete"]), allow=[SA])
    assert result.returncode == 1
    assert f"FAIL [P10] {STATE}:" in result.stdout
    assert f"FAIL [P10] {SA}:" not in result.stdout


def test_stale_entry_fails(tmp_path):
    result = run(tmp_path, rc(SA, ["update"]), allow=[SA])
    assert result.returncode == 1
    assert f"FAIL [P10] {SA}: stale allow-destroy entry; remove it from" in result.stdout


def test_duplicate_entry_fails(tmp_path):
    result = run(tmp_path, rc(SA, ["delete"]), allow=[SA, SA])
    assert result.returncode == 1
    assert f"FAIL [P10] {SA}: duplicate allow-destroy entry (2 times" in result.stdout


def test_bom_plan_is_read(tmp_path):
    result = run(tmp_path, rc(SA, ["create"]), bom=True)
    assert result.returncode == 0, result.stderr


def test_invalid_json_and_missing_files_exit_2(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"resource_changes": []}), encoding="utf-8")
    for args in (
        [str(bad)],
        [str(tmp_path / "absent.json")],
        [str(good), "--allow-destroy-file", str(tmp_path / "absent")],
    ):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False
        )
        assert result.returncode == 2
        assert "ERROR:" in result.stderr
