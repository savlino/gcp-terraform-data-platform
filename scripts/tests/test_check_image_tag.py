"""Synthetic tfvars and a fake git; no repository history or cloud access is used."""

import importlib.util
import io
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_image_tag.py"
_spec = importlib.util.spec_from_file_location("check_image_tag", SCRIPT)
check_image_tag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_image_tag)

FULL = "bda7f24c0ffee0000000000000000000000000aa"
OTHER_FULL = "88fc030c0ffee0000000000000000000000000bb"
DIGEST = "sha256:" + "9d" * 32
REPOSITORY = "europe-southwest1-docker.pkg.dev/dep-2610-ops/ops-ingest/ingest"


def fake_git(last_full=FULL, last_short="bda7f24", commits=None, shallow="false"):
    """Answer only the git calls the script makes; any other call fails the test."""
    commits = {"bda7f24": FULL, "88fc030": OTHER_FULL} if commits is None else commits
    calls = []

    def git(*args):
        calls.append(args)
        if args == ("rev-parse", "--is-shallow-repository"):
            return shallow
        if args[:3] == ("log", "-1", "--format=%H"):
            return last_full
        if args[:3] == ("log", "-1", "--format=%h"):
            return last_short
        if args[:3] == ("rev-parse", "--verify", "--quiet"):
            return commits.get(args[3].removesuffix("^{commit}"))
        raise AssertionError(f"unexpected git call {args}")

    git.calls = calls
    return git


def write_tfvars(root, tags, digests=None, extra=""):
    digests = digests or {}
    env_dir = root / "infra" / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    for env in ("dev", "test", "prod"):
        lines = [f'image_tag    = "{tags[env]}"']
        if env in digests:
            lines.append(f"image_digest = {digests[env]}")
        (env_dir / f"{env}.tfvars").write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")
    variables = root / "infra" / "variables.tf"
    variables.write_text(
        'variable "image_tag" {\n  type = string\n}\n\n'
        'variable "image_repository" {\n'
        "  type        = string\n"
        f'  default     = "{REPOSITORY}"\n'
        "}\n",
        encoding="utf-8",
    )


def run(root, git, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = check_image_tag.main(list(argv), root=root, git=git, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def same(value):
    return {"dev": value, "test": value, "prod": value}


def test_match_passes(tmp_path):
    write_tfvars(tmp_path, same("bda7f24"), same(f'"{DIGEST}"'))
    code, out, err = run(tmp_path, fake_git())
    assert (code, err) == (0, "")
    assert out.startswith("PASS:")


def test_longer_abbreviation_of_the_same_commit_passes(tmp_path):
    write_tfvars(tmp_path, same("bda7f24c"))
    git = fake_git(commits={"bda7f24c": FULL})
    code, _, err = run(tmp_path, git)
    assert (code, err) == (0, "")


def test_mismatch_fails_and_prints_all_four_values(tmp_path):
    write_tfvars(tmp_path, same("88fc030"))
    code, _, err = run(tmp_path, fake_git())
    assert code == 1
    assert "not the last commit that changed the image inputs" in err
    assert "expected=bda7f24 dev=88fc030 test=88fc030 prod=88fc030" in err


def test_tag_unknown_to_git_fails(tmp_path):
    write_tfvars(tmp_path, same("986c22e"))
    code, _, err = run(tmp_path, fake_git())
    assert code == 1
    assert "image_tag 986c22e is not a commit in this repository" in err


def test_tfvars_disagree_fails_and_prints_all_four_values(tmp_path):
    write_tfvars(tmp_path, {"dev": "bda7f24", "test": "bda7f24", "prod": "88fc030"})
    git = fake_git()
    code, _, err = run(tmp_path, git)
    assert code == 1
    assert "image_tag differs between tfvars: " in err
    assert "expected=bda7f24 dev=bda7f24 test=bda7f24 prod=88fc030" in err
    assert not [call for call in git.calls if call[:2] == ("rev-parse", "--verify")]


def test_digest_disagree_fails(tmp_path):
    digests = {"dev": f'"{DIGEST}"', "test": f'"{DIGEST}"', "prod": "null"}
    write_tfvars(tmp_path, same("bda7f24"), digests)
    code, _, err = run(tmp_path, fake_git())
    assert code == 1
    assert f"image_digest differs between tfvars: dev={DIGEST} test={DIGEST} prod=null" in err


def test_empty_git_result_fails(tmp_path):
    write_tfvars(tmp_path, same("bda7f24"))
    code, _, err = run(tmp_path, fake_git(last_full=""))
    assert code == 1
    assert "no commit changed the image inputs (app, schemas, .dockerignore)" in err
    assert "expected tag is empty" in err


def test_failed_git_log_fails(tmp_path):
    write_tfvars(tmp_path, same("bda7f24"))
    code, _, err = run(tmp_path, fake_git(last_full=None))
    assert code == 1
    assert "expected tag is empty" in err


def test_shallow_clone_fails_before_reading_history(tmp_path):
    write_tfvars(tmp_path, same("bda7f24"))
    git = fake_git(shallow="true")
    code, _, err = run(tmp_path, git)
    assert code == 1
    assert 'set GIT_DEPTH: "0"' in err
    assert git.calls == [("rev-parse", "--is-shallow-repository")]


def test_duplicate_assignment_is_an_input_error(tmp_path):
    write_tfvars(tmp_path, same("bda7f24"), extra='image_tag = "bda7f24"\n')
    code, _, err = run(tmp_path, fake_git())
    assert code == 2
    assert "image_tag is assigned 2 times" in err


def test_missing_tag_is_an_input_error(tmp_path):
    write_tfvars(tmp_path, same("bda7f24"))
    test_tfvars = tmp_path / "infra" / "env" / "test.tfvars"
    test_tfvars.write_text("enable_ingest_job = true\n", encoding="utf-8")
    code, _, err = run(tmp_path, fake_git())
    assert code == 2
    assert "infra/env/test.tfvars: image_tag is missing" in err


def test_unquoted_tag_is_an_input_error(tmp_path):
    write_tfvars(tmp_path, same("bda7f24"))
    dev_tfvars = tmp_path / "infra" / "env" / "dev.tfvars"
    dev_tfvars.write_text("image_tag = var.tag\n", encoding="utf-8")
    code, _, err = run(tmp_path, fake_git())
    assert code == 2
    assert "image_tag must be a quoted string or null on one line" in err


def test_ref_prints_repository_default_tag_and_digest(tmp_path):
    write_tfvars(tmp_path, same("bda7f24"), same(f'"{DIGEST}"  # release digest'))
    code, out, err = run(tmp_path, fake_git(), "ref", "test")
    assert (code, err) == (0, "")
    assert out == f"{REPOSITORY} bda7f24 {DIGEST}\n"


def test_ref_without_digest_prints_dash_and_tfvars_repository_wins(tmp_path):
    other = "europe-southwest1-docker.pkg.dev/dep-2610-ops/other/ingest"
    write_tfvars(tmp_path, same("bda7f24"), same("null"), extra=f'image_repository = "{other}"\n')
    code, out, _ = run(tmp_path, fake_git(), "ref", "prod")
    assert code == 0
    assert out == f"{other} bda7f24 -\n"
