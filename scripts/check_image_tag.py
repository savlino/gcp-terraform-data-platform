"""Check the release image reference in infra/env/*.tfvars using standard-library code.

Subcommands
  git        (default) Lint check, no cloud access. Fails unless:
             - the clone is not shallow;
             - some commit changed the image inputs (IMAGE_INPUTS below);
             - image_tag is set once in each of dev/test/prod tfvars and all three agree;
             - image_digest (optional) is identical in all three, or absent/null in all three;
             - image_tag resolves to a commit in this repository, and that commit is the
               last commit that changed the image inputs.
             Short SHAs are compared as full commit IDs, so a longer abbreviation of the
             same commit is accepted and an unknown tag (rewritten history) is reported.
  ref ENV    Print "<image_repository> <image_tag> <image_digest or ->" for one
             environment, for the pre-apply registry check. image_repository comes from
             the tfvars file, otherwise from the default in infra/variables.tf.

  --print-inputs
             Print IMAGE_INPUTS, one path per line, and exit. This is the only list of
             image inputs; the runbook computes the release tag from this output.

IMAGE_INPUTS are exactly the paths that reach the image build: the Dockerfile, the
files it copies (app/pyproject.toml, app/src, schemas) and .dockerignore, which selects
the build context. app/tests and docs are excluded, so changing them needs no new image.
scripts/tests/test_check_image_tag.py fails if a Dockerfile COPY source is not covered.

Exit codes: 0 pass, 1 check failed, 2 usage or input error.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ENVS = ("dev", "test", "prod")
IMAGE_INPUTS = ("app/Dockerfile", "app/pyproject.toml", "app/src", "schemas", ".dockerignore")
ROOT = Path(__file__).resolve().parents[1]

_ASSIGN = r'^[ \t]*{name}[ \t]*=[ \t]*(?:"(?P<str>[^"\n]*)"|(?P<null>null))[ \t]*(?:#.*)?$'
_ANY_ASSIGN = r"^[ \t]*{name}[ \t]*="


class InputError(Exception):
    """Configuration that cannot be read; exit code 2."""


def read_var(text, name, required, source):
    """Return a string value, or None for null/absent (when not required)."""
    assignments = re.findall(_ANY_ASSIGN.format(name=name), text, re.MULTILINE)
    if len(assignments) > 1:
        raise InputError(f"{source}: {name} is assigned {len(assignments)} times")
    if not assignments:
        if required:
            raise InputError(f"{source}: {name} is missing")
        return None
    match = re.search(_ASSIGN.format(name=name), text, re.MULTILINE)
    if match is None:
        raise InputError(f"{source}: {name} must be a quoted string or null on one line")
    if match.group("null"):
        if required:
            raise InputError(f"{source}: {name} must not be null")
        return None
    value = match.group("str")
    if required and not value:
        raise InputError(f"{source}: {name} must not be empty")
    return value


def variable_default(text, name, source):
    """Default of `variable "<name>"` in variables.tf (string defaults only)."""
    block = re.search(
        r'^variable[ \t]+"' + re.escape(name) + r'"[ \t]*\{(?P<body>.*?)^\}',
        text,
        re.MULTILINE | re.DOTALL,
    )
    if block is None:
        raise InputError(f"{source}: variable {name} is missing")
    return read_var(block.group("body"), "default", True, f"{source} (variable {name})")


def run_git(*args):
    """Run git in the repository; return stripped stdout, or None on a non-zero exit."""
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def tfvars_path(root, env):
    return root / "infra" / "env" / f"{env}.tfvars"


def read_text(path):
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise InputError(f"cannot read {path}: {error.strerror}") from error


def check_git(root, git):
    """Return a list of failure messages (empty means pass)."""
    if git("rev-parse", "--is-shallow-repository") != "false":
        return [
            "the clone is shallow or not a git repository; image inputs history is incomplete "
            "(set GIT_DEPTH: \"0\" for this job)"
        ]

    expected_full = git("log", "-1", "--format=%H", "--", *IMAGE_INPUTS)
    if not expected_full:
        inputs = ", ".join(IMAGE_INPUTS)
        return [f"no commit changed the image inputs ({inputs}); expected tag is empty"]
    expected = git("log", "-1", "--format=%h", "--", *IMAGE_INPUTS)

    tags = {}
    digests = {}
    for env in ENVS:
        path = tfvars_path(root, env)
        text = read_text(path)
        source = f"infra/env/{env}.tfvars"
        tags[env] = read_var(text, "image_tag", True, source)
        digests[env] = read_var(text, "image_digest", False, source)

    summary = f"expected={expected} " + " ".join(f"{env}={tags[env]}" for env in ENVS)
    failures = []
    if len(set(tags.values())) != 1:
        failures.append(f"image_tag differs between tfvars: {summary}")
    if len(set(digests.values())) != 1:
        values = " ".join(f"{env}={digests[env] or 'null'}" for env in ENVS)
        failures.append(f"image_digest differs between tfvars: {values}")
    if failures:
        return failures

    tag = tags["dev"]
    resolved = git("rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}")
    if not resolved:
        return [
            f"image_tag {tag} is not a commit in this repository "
            f"(rewritten or unpushed history?): {summary}"
        ]
    if resolved != expected_full:
        return [
            f"image_tag is not the last commit that changed the image inputs: {summary}. "
            "Build and push the image for the expected tag, then update all three tfvars."
        ]
    return []


def image_ref(root, env):
    source = f"infra/env/{env}.tfvars"
    text = read_text(tfvars_path(root, env))
    tag = read_var(text, "image_tag", True, source)
    digest = read_var(text, "image_digest", False, source)
    repository = read_var(text, "image_repository", False, source)
    if repository is None:
        variables = read_text(root / "infra" / "variables.tf")
        repository = variable_default(variables, "image_repository", "infra/variables.tf")
    values = {"image_repository": repository, "image_tag": tag, "image_digest": digest or "-"}
    for name, value in values.items():
        if any(char.isspace() for char in value):
            raise InputError(f"{source}: {name} must not contain whitespace")
    return repository, tag, digest


def main(argv=None, root=ROOT, git=run_git, out=sys.stdout, err=sys.stderr):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--print-inputs", action="store_true", help="print the image input paths and exit"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("git", help="lint check against git history (default)")
    ref = sub.add_parser("ref", help="print the image reference of one environment")
    ref.add_argument("env", choices=ENVS)
    args = parser.parse_args(argv)

    if args.print_inputs:
        print("\n".join(IMAGE_INPUTS), file=out)
        return 0
    try:
        if args.command == "ref":
            repository, tag, digest = image_ref(root, args.env)
            print(repository, tag, digest or "-", file=out)
            return 0
        failures = check_git(root, git)
    except InputError as error:
        print(f"ERROR: {error}", file=err)
        return 2

    for failure in failures:
        print(f"FAIL: {failure}", file=err)
    if failures:
        return 1
    print("PASS: image_tag in all tfvars is the last commit changing the image inputs", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())