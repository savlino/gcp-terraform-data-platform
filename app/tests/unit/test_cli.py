import json
import logging
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from ingest import __main__ as cli
from ingest.config import Config, parse_quarter
from ingest.state import Decision


@pytest.fixture
def environment(monkeypatch, schema_path):
    monkeypatch.setenv("PROJECT_ID", "dep-2610-dev")
    monkeypatch.setenv("RAW_BUCKET", "dep-2610-dev-raw")
    monkeypatch.setenv("SOURCE_SCHEMA_PATH", str(schema_path))
    monkeypatch.delenv("IMPERSONATE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("MIN_QUARTER", raising=False)
    monkeypatch.delenv("S3_REGION", raising=False)


@pytest.mark.parametrize("value", ["2023-Q2", "2022-Q4", "2024-Q5", "bad"])
def test_invalid_quarters(value):
    with pytest.raises(ValueError):
        parse_quarter(value)


def test_adc_and_optional_impersonation(environment, monkeypatch):
    adc, client, impersonate = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(cli.google.auth, "default", lambda **kwargs: (adc, "quota-project"))
    monkeypatch.setattr(cli.storage, "Client", client)
    monkeypatch.setattr(cli.impersonated_credentials, "Credentials", impersonate)
    cli.storage_client(Config.from_env())
    assert client.call_args.kwargs["credentials"] is adc
    impersonate.assert_not_called()
    monkeypatch.setenv(
        "IMPERSONATE_SERVICE_ACCOUNT", "ingest-runtime@example.iam.gserviceaccount.com"
    )
    cli.storage_client(Config.from_env())
    assert impersonate.call_args.kwargs["source_credentials"] is adc
    assert client.call_args.kwargs["credentials"] is impersonate.return_value


def test_empty_impersonation_is_rejected(environment, monkeypatch):
    monkeypatch.setenv("IMPERSONATE_SERVICE_ACCOUNT", "")
    with pytest.raises(ValueError, match="refusing ADC fallback"):
        Config.from_env()


@pytest.mark.parametrize("working_directory", ["root", "app", "other"])
def test_default_schema_independent_of_working_directory(
    environment, monkeypatch, schema_path, tmp_path, working_directory
):
    root = schema_path.parent.parent
    directory = {"root": root, "app": root / "app", "other": tmp_path}[working_directory]
    monkeypatch.chdir(directory)
    monkeypatch.delenv("SOURCE_SCHEMA_PATH")
    config = Config.from_env()
    assert config.schema_path == schema_path
    assert len(json.loads(config.schema_path.read_text(encoding="utf-8"))) == 11


def test_explicit_relative_schema_path_preserved(environment, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOURCE_SCHEMA_PATH", "custom.json")
    (tmp_path / "custom.json").write_text("[]", encoding="utf-8")
    assert Config.from_env().schema_path.read_text(encoding="utf-8") == "[]"


def test_missing_explicit_schema_fails_before_cloud_access(environment, monkeypatch, tmp_path):
    monkeypatch.setenv("SOURCE_SCHEMA_PATH", str(tmp_path / "missing.json"))
    monkeypatch.setattr("sys.argv", ["ingest", "sync", "--dry-run"])
    client = MagicMock()
    monkeypatch.setattr(cli, "storage_client", client)
    assert cli.main() == 1
    client.assert_not_called()


def setup_cli(monkeypatch, source, argv):
    monkeypatch.setattr("sys.argv", ["ingest", "sync", *argv])
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(cli, "storage_client", lambda _: client)
    monkeypatch.setattr(cli, "http_session", MagicMock())
    monkeypatch.setattr(cli, "discover", lambda *args: [source])
    monkeypatch.setattr(cli, "pending", lambda bucket, obj: Decision(obj, 0, "missing"))
    footer, transfer, s3 = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(cli, "check_source", footer)
    monkeypatch.setattr(cli, "transfer", transfer)
    monkeypatch.setattr(cli.fs, "S3FileSystem", s3)
    return footer, transfer, s3


def test_dry_run_no_writes_or_source_filesystem(environment, monkeypatch, source):
    footer, transfer, s3 = setup_cli(monkeypatch, source, ["--type", "mobile", "--dry-run"])
    assert cli.main() == 0
    footer.assert_not_called()
    transfer.assert_not_called()
    s3.assert_not_called()


def test_footer_before_transfer_and_limit(environment, monkeypatch, source):
    footer, transfer, s3 = setup_cli(monkeypatch, source, ["--type", "all", "--limit", "1"])
    order = MagicMock()
    order.attach_mock(footer, "footer")
    order.attach_mock(transfer, "transfer")
    assert cli.main() == 0
    assert [call[0] for call in order.mock_calls] == ["footer", "transfer"]
    s3.assert_called_once()
    assert s3.call_args.kwargs["anonymous"] is True
    assert s3.call_args.kwargs["region"] == "us-west-2"
    assert s3.call_args.kwargs["connect_timeout"] == 10
    assert s3.call_args.kwargs["request_timeout"] == 120
    assert transfer.call_count == 1


def test_bad_schema_fails_before_transfer(environment, monkeypatch, source):
    footer, transfer, _ = setup_cli(monkeypatch, source, ["--type", "mobile"])
    footer.side_effect = ValueError("Missing devices")
    assert cli.main() == 1
    transfer.assert_not_called()


def test_unchanged_run_transfers_nothing(environment, monkeypatch, source):
    footer, transfer, s3 = setup_cli(monkeypatch, source, ["--type", "mobile"])
    monkeypatch.setattr(cli, "pending", lambda *args: None)
    assert cli.main() == 0
    transfer.assert_not_called()
    s3.assert_not_called()


def test_second_run_skips_and_changed_etag_transfers_exactly_one(environment, monkeypatch, source):
    from ingest.state import pending

    footer, transfer, _ = setup_cli(monkeypatch, source, ["--type", "mobile"])
    second = replace(source, key=source.key.replace("2023", "2024"), year=2024)
    objects = {}

    def lookup(bucket, obj):
        fake_bucket = MagicMock()
        fake_bucket.get_blob.return_value = objects.get(obj.destination)
        return pending(fake_bucket, obj)

    def upload(session, bucket, decision):
        obj = decision.source
        objects[obj.destination] = MagicMock(
            metadata={"source_etag": obj.etag, "source_size": str(obj.size)},
            size=obj.size,
            generation=1,
        )

    monkeypatch.setattr(cli, "discover", lambda *args: [source, second])
    monkeypatch.setattr(cli, "pending", lookup)
    transfer.side_effect = upload
    assert cli.main() == 0
    assert transfer.call_count == 2
    transfer.reset_mock()
    assert cli.main() == 0
    transfer.assert_not_called()
    changed = replace(source, etag="new-opaque-etag-3")
    monkeypatch.setattr(cli, "discover", lambda *args: [changed, second])
    assert cli.main() == 0
    transfer.assert_called_once()
    assert transfer.call_args.args[2].source == changed


@pytest.mark.parametrize("value", ["0", "-1"])
def test_nonpositive_limit_rejected(monkeypatch, value):
    monkeypatch.setattr("sys.argv", ["ingest", "sync", "--limit", value])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code != 0


def test_json_logging_shape():
    record = logging.LogRecord("ingest", logging.INFO, "", 0, "Transferred %d", (1,), None)
    result = json.loads(cli.JsonFormatter().format(record))
    assert result["severity"] == "INFO"
    assert result["message"] == "Transferred 1"
    assert result["timestamp"]


def test_s3_region_is_configurable(environment, monkeypatch, source):
    monkeypatch.setenv("S3_REGION", "us-east-1")
    _, _, s3 = setup_cli(monkeypatch, source, ["--type", "mobile"])
    assert cli.main() == 0
    assert s3.call_args.kwargs["region"] == "us-east-1"


def test_missing_environment_fails_with_nonzero_exit(environment, monkeypatch):
    monkeypatch.delenv("RAW_BUCKET")
    monkeypatch.setattr("sys.argv", ["ingest", "sync"])
    assert cli.main() == 1


@pytest.mark.parametrize("dry_run", [False, True])
def test_quarter_filters_before_destination_checks(environment, monkeypatch, source, dry_run):
    arguments = ["--type", "mobile", "--quarter", "2023-Q3"]
    if dry_run:
        arguments.append("--dry-run")
    _, transfer, _ = setup_cli(monkeypatch, source, arguments)
    other = replace(source, key=source.key.replace("quarter=3", "quarter=4"), quarter=4)
    monkeypatch.setattr(cli, "discover", lambda *args: [other, source])
    decisions = MagicMock(side_effect=lambda bucket, obj: Decision(obj, 0, "missing"))
    monkeypatch.setattr(cli, "pending", decisions)
    assert cli.main() == 0
    assert decisions.call_count == 1
    assert decisions.call_args.args[1] == source
    assert transfer.call_count == (0 if dry_run else 1)


def test_quarter_unchanged_then_etag_changed(environment, monkeypatch, source):
    from ingest.state import pending

    _, transfer, _ = setup_cli(monkeypatch, source, ["--type", "mobile", "--quarter", "2023-Q3"])
    other = replace(source, key=source.key.replace("quarter=3", "quarter=4"), quarter=4)
    monkeypatch.setattr(cli, "discover", lambda *args: [source, other])
    blob = MagicMock(
        metadata={"source_etag": source.etag, "source_size": str(source.size)},
        size=source.size,
        generation=12,
    )
    bucket = MagicMock()
    bucket.get_blob.return_value = blob
    monkeypatch.setattr(cli, "pending", lambda _, obj: pending(bucket, obj))
    assert cli.main() == 0
    transfer.assert_not_called()
    blob.metadata["source_etag"] = "synthetic-mismatch"
    assert cli.main() == 0
    transfer.assert_called_once()
    assert transfer.call_args.args[2] == Decision(source, 12, "etag_changed")


def test_limit_counts_pending_files_not_discovered_files(environment, monkeypatch, source):
    from ingest.state import pending

    _, transfer, _ = setup_cli(monkeypatch, source, ["--type", "mobile", "--limit", "1"])
    second = replace(source, key=source.key.replace("quarter=3", "quarter=4"), quarter=4)
    third = replace(source, key=source.key.replace("2023", "2024"), year=2024)
    monkeypatch.setattr(cli, "discover", lambda *args: [third, second, source])
    bucket = MagicMock()
    current = MagicMock(
        metadata={"source_etag": source.etag, "source_size": str(source.size)},
        size=source.size,
        generation=12,
    )
    bucket.get_blob.side_effect = lambda name, **kwargs: (
        current if name == source.destination else None
    )
    monkeypatch.setattr(cli, "pending", lambda _, obj: pending(bucket, obj))
    assert cli.main() == 0
    transfer.assert_called_once()
    assert transfer.call_args.args[2].source == second


@pytest.mark.parametrize("quarter", ["2023-Q2", "2024-Q5", "bad"])
def test_invalid_quarter_argument_rejected(monkeypatch, quarter):
    monkeypatch.setattr("sys.argv", ["ingest", "sync", "--quarter", quarter])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_quarter_before_configured_minimum_fails_before_cloud(environment, monkeypatch):
    monkeypatch.setenv("MIN_QUARTER", "2024-Q1")
    monkeypatch.setattr("sys.argv", ["ingest", "sync", "--quarter", "2023-Q3"])
    client = MagicMock()
    monkeypatch.setattr(cli, "storage_client", client)
    assert cli.main() == 1
    client.assert_not_called()


def test_unavailable_quarter_fails(environment, monkeypatch, source):
    _, transfer, _ = setup_cli(monkeypatch, source, ["--type", "mobile", "--quarter", "2025-Q1"])
    assert cli.main() == 1
    transfer.assert_not_called()
