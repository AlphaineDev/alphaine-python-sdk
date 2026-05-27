import json
from pathlib import Path

import httpx

from alphaine import cli
from alphaine.client import AlphaineError


class FakeClient:
    instances = []
    error = None

    def __init__(self, api_key=None, base_url=None, trust_env=True, **_):
        self.api_key = api_key
        self.base_url = base_url
        self.trust_env = trust_env
        self.calls = []
        FakeClient.instances.append(self)

    def __enter__(self):
        if FakeClient.error:
            raise FakeClient.error
        return self

    def __exit__(self, *_):
        return None

    def me(self):
        self.calls.append(("me",))
        return {"user": {"email": "user@example.com"}, "token": {"scope": "data:read"}}

    def list(self, prefix=""):
        self.calls.append(("list", prefix))
        return {
            "prefix": prefix,
            "folders": [{"name": "exchange=binance", "prefix": "exchange=binance/", "fileCount": 2, "size": 6}],
            "files": [],
            "truncated": False,
            "cursor": None,
        }

    def list_streams(self, root_prefix=""):
        self.calls.append(("list_streams", root_prefix))
        return [{"name": "trades", "prefix": "exchange=binance/stream=trades/", "fileCount": 2, "size": 6}]

    def list_stream_dates(self, stream):
        self.calls.append(("list_stream_dates", stream))
        return [{"date": "2026-05-14", "fileCount": 1, "size": 3}]

    def list_stream_files(self, stream, dates=None):
        self.calls.append(("list_stream_files", stream, dates))
        return [{"key": "exchange=binance/stream=trades/date=20260514/a.txt", "size": 3, "objectRef": "oref_a"}]

    def iter_files(self, prefix=""):
        self.calls.append(("iter_files", prefix))
        yield {"key": f"{prefix}remote/new.txt", "size": 3, "objectRef": "oref_new"}
        yield {"key": f"{prefix}remote/existing.txt", "size": 3, "objectRef": "oref_existing"}

    def download_prefix(self, prefix, destination, *, workers, retries, show_progress):
        self.calls.append(("download_prefix", prefix, destination, workers, retries, show_progress))
        return [Path(destination) / "folder" / "file.txt"]

    def download_stream(self, stream, dates, destination, *, workers, retries, show_progress):
        self.calls.append(("download_stream", stream, dates, destination, workers, retries, show_progress))
        return [Path(destination) / "exchange=binance" / "stream=trades" / "date=20260514" / "a.txt"]

    def resolve_stream_prefix(self, stream):
        self.calls.append(("resolve_stream_prefix", stream))
        return f"exchange=binance/stream={stream}/"


def install_fake_client(monkeypatch):
    FakeClient.instances = []
    FakeClient.error = None
    monkeypatch.setattr(cli, "AlphaineClient", FakeClient)
    monkeypatch.setattr(cli, "_proxy_env_present", lambda: False)


def isolate_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ALPHAINE_API_KEY", raising=False)
    monkeypatch.delenv("ALPHAINE_BASE_URL", raising=False)
    return tmp_path / "config" / "alphaine" / "config.json"


def test_me_outputs_human_readable_status(monkeypatch, capsys):
    install_fake_client(monkeypatch)

    code = cli.main(["--api-key", "alphaine_live_test", "me"])

    assert code == 0
    assert FakeClient.instances[0].api_key == "alphaine_live_test"
    assert FakeClient.instances[0].calls == [("me",)]
    assert "user.email: user@example.com" in capsys.readouterr().out


def test_list_json_outputs_full_listing(monkeypatch, capsys):
    install_fake_client(monkeypatch)

    code = cli.main(["list", "exchange=binance/", "--json"])

    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["prefix"] == "exchange=binance/"
    assert output["folders"][0]["prefix"] == "exchange=binance/"


def test_stream_commands_call_matching_client_methods(monkeypatch, capsys):
    install_fake_client(monkeypatch)

    assert cli.main(["streams", "--root-prefix", "exchange=binance/"]) == 0
    assert FakeClient.instances[-1].calls == [("list_streams", "exchange=binance/")]

    assert cli.main(["dates", "trades"]) == 0
    assert FakeClient.instances[-1].calls == [("list_stream_dates", "trades")]

    assert cli.main(["files", "trades", "--date", "2026-05-14"]) == 0
    assert FakeClient.instances[-1].calls == [("list_stream_files", "trades", ["2026-05-14"])]

    output = capsys.readouterr().out
    assert "trades" in output
    assert "2026-05-14" in output


def test_download_stream_passes_cli_options(monkeypatch, capsys):
    install_fake_client(monkeypatch)

    code = cli.main([
        "download-stream",
        "trades",
        "--date",
        "2026-05-14",
        "--date",
        "2026-05-15",
        "/tmp/data",
        "--workers",
        "2",
        "--retries",
        "5",
        "--no-progress",
    ])

    assert code == 0
    assert FakeClient.instances[0].calls == [
        ("download_stream", "trades", ["2026-05-14", "2026-05-15"], "/tmp/data", 2, 5, False)
    ]
    assert "/tmp/data/exchange=binance/stream=trades/date=20260514/a.txt" in capsys.readouterr().out


def test_download_command_with_stream_and_date_uses_download_stream(monkeypatch):
    install_fake_client(monkeypatch)

    code = cli.main([
        "download",
        "--stream",
        "trade",
        "--date",
        "2026-05-14",
        "/tmp/data",
        "--workers",
        "3",
        "--retries",
        "6",
        "--no-progress",
    ])

    assert code == 0
    assert FakeClient.instances[0].calls == [
        ("download_stream", "trade", ["2026-05-14"], "/tmp/data", 3, 6, False)
    ]


def test_download_command_with_all_streams_all_dates_uses_root_prefix(monkeypatch):
    install_fake_client(monkeypatch)

    code = cli.main(["download", "--all-stream", "--all-dates", "/tmp/data"])

    assert code == 0
    assert FakeClient.instances[0].calls == [("download_prefix", "", "/tmp/data", 8, 3, True)]


def test_download_command_accepts_all_streams_alias(monkeypatch):
    install_fake_client(monkeypatch)

    code = cli.main(["download", "--all-streams", "--all-dates", "/tmp/data"])

    assert code == 0
    assert FakeClient.instances[0].calls == [("download_prefix", "", "/tmp/data", 8, 3, True)]


def test_download_dry_run_reports_download_and_skip(monkeypatch, tmp_path, capsys):
    install_fake_client(monkeypatch)
    existing = tmp_path / "remote" / "existing.txt"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"abc")

    code = cli.main(["download", "--all-streams", "--all-dates", str(tmp_path), "--dry-run"])

    assert code == 0
    assert FakeClient.instances[0].calls == [("iter_files", "")]
    output = capsys.readouterr().out
    assert "dry-run: 1 download, 1 skip, 2 total" in output
    assert "download" in output
    assert "skip" in output
    assert "remote/new.txt" in output
    assert "remote/existing.txt" in output


def test_download_dry_run_json(monkeypatch, tmp_path, capsys):
    install_fake_client(monkeypatch)

    code = cli.main(["download", "--stream", "trades", "--date", "2026-05-14", str(tmp_path), "--dry-run", "--json"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totalFiles"] == 1
    assert payload["downloadFiles"] == 1
    assert payload["files"][0]["key"] == "exchange=binance/stream=trades/date=20260514/a.txt"
    assert FakeClient.instances[0].calls == [("list_stream_files", "trades", ["2026-05-14"])]


def test_download_command_with_stream_all_dates_downloads_stream_prefix(monkeypatch):
    install_fake_client(monkeypatch)

    code = cli.main(["download", "--stream", "trades", "--all-dates", "/tmp/data"])

    assert code == 0
    assert FakeClient.instances[0].calls == [
        ("resolve_stream_prefix", "trades"),
        ("download_prefix", "exchange=binance/stream=trades/", "/tmp/data", 8, 3, True),
    ]


def test_download_command_requires_stream_or_all_stream(monkeypatch, capsys):
    install_fake_client(monkeypatch)

    code = cli.main(["download", "--date", "2026-05-14", "/tmp/data"])

    assert code == 1
    assert "Pass --stream, or use --all-stream --all-dates." in capsys.readouterr().err


def test_sdk_error_returns_nonzero_and_stderr(monkeypatch, capsys):
    install_fake_client(monkeypatch)
    FakeClient.error = AlphaineError("Pass api_key or set ALPHAINE_API_KEY.")

    code = cli.main(["me"])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Pass api_key or set ALPHAINE_API_KEY." in captured.err


def test_transport_error_returns_nonzero_and_stderr(monkeypatch, capsys):
    install_fake_client(monkeypatch)
    FakeClient.error = httpx.ConnectError("connection failed")

    code = cli.main(["me"])

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "connection failed" in captured.err


def test_login_with_api_key_writes_config_without_printing_secret(monkeypatch, tmp_path, capsys):
    config_path = isolate_config(monkeypatch, tmp_path)

    code = cli.main(["login", "--api-key", "alphaine_live_secret_123456", "--base-url", "https://alphaine.test"])

    assert code == 0
    config = json.loads(config_path.read_text())
    assert config == {
        "api_key": "alphaine_live_secret_123456",
        "base_url": "https://alphaine.test",
    }
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert "alphaine_live_secret_123456" not in capsys.readouterr().out


def test_login_prompts_for_api_key(monkeypatch, tmp_path):
    config_path = isolate_config(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "alphaine_live_prompted")

    code = cli.main(["login"])

    assert code == 0
    assert json.loads(config_path.read_text())["api_key"] == "alphaine_live_prompted"


def test_logout_removes_saved_config(monkeypatch, tmp_path, capsys):
    config_path = isolate_config(monkeypatch, tmp_path)
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"api_key": "alphaine_live_secret"}')

    code = cli.main(["logout"])

    assert code == 0
    assert not config_path.exists()
    assert "Removed Alphaine CLI credentials" in capsys.readouterr().out


def test_auth_status_reports_missing_env_config_and_flag_sources(monkeypatch, tmp_path, capsys):
    config_path = isolate_config(monkeypatch, tmp_path)

    assert cli.main(["auth", "status"]) == 0
    assert "source: missing" in capsys.readouterr().out

    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"api_key": "alphaine_live_config_secret", "base_url": "https://config.test"}))
    assert cli.main(["auth", "status"]) == 0
    output = capsys.readouterr().out
    assert "source: config" in output
    assert "alphaine...cret" in output
    assert "alphaine_live_config_secret" not in output

    monkeypatch.setenv("ALPHAINE_API_KEY", "alphaine_live_env_secret")
    monkeypatch.setenv("ALPHAINE_BASE_URL", "https://env.test")
    assert cli.main(["auth", "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "env"
    assert payload["base_url"] == "https://env.test"
    assert "api_key" not in payload
    assert "alphaine_live_env_secret" not in json.dumps(payload)

    assert cli.main(["auth", "status", "--api-key", "alphaine_live_flag_secret"]) == 0
    assert "source: flag" in capsys.readouterr().out


def test_data_command_uses_saved_config_when_flag_and_env_are_missing(monkeypatch, tmp_path):
    config_path = isolate_config(monkeypatch, tmp_path)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"api_key": "alphaine_live_config", "base_url": "https://config.test"}))
    install_fake_client(monkeypatch)

    code = cli.main(["me"])

    assert code == 0
    assert FakeClient.instances[0].api_key == "alphaine_live_config"
    assert FakeClient.instances[0].base_url == "https://config.test"


def test_api_key_precedence_is_flag_then_env_then_config(monkeypatch, tmp_path):
    config_path = isolate_config(monkeypatch, tmp_path)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"api_key": "alphaine_live_config", "base_url": "https://config.test"}))
    monkeypatch.setenv("ALPHAINE_API_KEY", "alphaine_live_env")
    monkeypatch.setenv("ALPHAINE_BASE_URL", "https://env.test")
    install_fake_client(monkeypatch)

    assert cli.main(["me"]) == 0
    assert FakeClient.instances[-1].api_key == "alphaine_live_env"
    assert FakeClient.instances[-1].base_url == "https://env.test"

    assert cli.main(["--api-key", "alphaine_live_flag", "--base-url", "https://flag.test", "me"]) == 0
    assert FakeClient.instances[-1].api_key == "alphaine_live_flag"
    assert FakeClient.instances[-1].base_url == "https://flag.test"


def test_saved_network_mode_controls_proxy_environment_use(monkeypatch, tmp_path):
    config_path = isolate_config(monkeypatch, tmp_path)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"api_key": "alphaine_live_config", "network_mode": "env"}))
    install_fake_client(monkeypatch)

    assert cli.main(["me"]) == 0
    assert FakeClient.instances[-1].trust_env is True

    config_path.write_text(json.dumps({"api_key": "alphaine_live_config", "network_mode": "direct"}))
    assert cli.main(["me"]) == 0
    assert FakeClient.instances[-1].trust_env is False


def test_auto_network_mode_records_direct_when_proxy_fails(monkeypatch, tmp_path):
    config_path = isolate_config(monkeypatch, tmp_path)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"api_key": "alphaine_live_config"}))
    monkeypatch.setattr(cli, "_proxy_env_present", lambda: True)

    class ProxyAwareClient(FakeClient):
        def me(self):
            self.calls.append(("me",))
            if self.trust_env:
                raise httpx.ConnectError("proxy failed")
            return {"user": {"email": "user@example.com"}}

    FakeClient.instances = []
    monkeypatch.setattr(cli, "AlphaineClient", ProxyAwareClient)

    assert cli.main(["me"]) == 0

    config = json.loads(config_path.read_text())
    assert config["network_mode"] == "direct"
    assert config["network_mode_source"] == "auto"
    assert config["proxy_env_detected"] == "true"
    assert [instance.trust_env for instance in FakeClient.instances] == [True, False, False]
