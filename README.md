# Alphaine Python SDK

Programmatic access to Alphaine datasets.

## Overview

`alphaine` is the official Python SDK and command-line tool for downloading
Alphaine market datasets from scripts, notebooks, and automated data pipelines.
It authenticates with an Alphaine API token, lists available dataset folders and
files, and downloads data through short-lived direct download URLs provided by
the Alphaine backend.

Use this repository when you want machine-friendly access to Alphaine data
without clicking through the web app. The SDK keeps the public interface small:
inspect your token, browse available data, preview download work, and download
individual files, prefixes, streams, or date ranges.

## Project Status

Current version: `0.1.0`.

This SDK is in early beta. The core download flow is usable, but command names,
helper methods, and response shapes may still evolve before a stable `1.0.0`
release. Pin the version or Git commit in production workflows.

## Install

The SDK is currently distributed from GitHub. It is not published to PyPI yet.

Install it into a project directly from GitHub:

```bash
uv add "alphaine @ git+https://github.com/AlphaineDev/alphaine-python-sdk.git"
```

For private repository access over SSH:

```bash
uv add "alphaine @ git+ssh://git@github.com/AlphaineDev/alphaine-python-sdk.git"
```

For local development from this repository:

```bash
uv sync --extra test
```

To install the CLI for the current user so `alphaine` works from any directory:

```bash
./install.sh
```

For a source checkout without installing into another project:

```bash
PYTHONPATH=src python -c "from alphaine import AlphaineClient; print(AlphaineClient)"
```

## API Key

The SDK requires an Alphaine API key. Sign in to your Alphaine account at
https://alphaine.com and create an API token from the account or data access
area before using the Python client or CLI.

Keep your API key private. Pass it through `ALPHAINE_API_KEY`, `alphaine login`,
or the `api_key` argument in local scripts; do not commit real keys to source
control.

## Usage

```python
from alphaine import AlphaineClient

client = AlphaineClient(api_key="alphaine_live_xxx")
print(client.me())
print(client.list("binance/usdm/"))

print(client.list_streams())
print(client.list_stream_dates("trades"))
print(client.list_stream_files("trades", ["2026-05-14"]))

client.download("path/file.parquet", "./data")
client.download(next(client.iter_files("binance/usdm/trade/")), "./data")
client.download_prefix("binance/usdm/trade/", "./data", workers=8)
client.download_stream("trades", ["2026-05-13", "2026-05-14"], "./data", workers=8)
```

The SDK reads `ALPHAINE_API_KEY` when `api_key` is not passed. Use
`ALPHAINE_BASE_URL` to point at a non-production Alphaine deployment.

Downloads use the Alphaine API token only to request short-lived direct download
URLs. The object bytes are then fetched from the returned URL with retry support,
temporary `.part` files, and atomic rename on success.

Files returned by `list()` and `iter_files()` include an opaque `objectRef`.
Pass those file dictionaries directly to `download()` or `download_many()` when
possible; raw `key` strings remain supported for legacy single-source data.

## CLI

Installing the package also installs the `alphaine` command:

```bash
alphaine login --api-key "alphaine_live_xxx"
alphaine auth status

alphaine me
alphaine list
alphaine list "exchange=binance/"
alphaine streams
alphaine dates trades
alphaine files trades --date 2026-05-14
alphaine download --stream trades --date 2026-05-14 ./data
alphaine download --stream trades --all-dates ./data
alphaine download --all-streams --all-dates ./data
alphaine download --all-streams --all-dates ./data --dry-run
alphaine download-prefix "exchange=binance/stream=trades/" ./data --workers 8
alphaine download-stream trades --date 2026-05-13 --date 2026-05-14 ./data --workers 8
```

Use `--json` on any command to print the underlying SDK response:

```bash
alphaine streams --json
```

The CLI reads `ALPHAINE_API_KEY` and `ALPHAINE_BASE_URL` by default. You can
override them per command with `--api-key` and `--base-url`; otherwise it uses
the API key saved by `alphaine login`. Add `--no-progress` to download commands
when running in logs or scripts.

By default, the CLI uses `--network-mode auto`. If proxy environment variables
are present, it probes Alphaine with the proxy environment and direct mode,
records the working mode in the CLI config, and reuses that mode on later
commands. You can override it per command:

```bash
alphaine me --network-mode auto
alphaine me --network-mode direct
alphaine me --network-mode env
```

Use `alphaine logout` to remove saved CLI credentials.

`alphaine download --all-streams --all-dates` is a large job. The SDK lists and
downloads in small batches to stay below Worker request limits, but for first
checks prefer a single stream/date command. Use `--dry-run` on any download
command to preview what would be downloaded and what would be skipped because
the target file already exists with the expected size. `--all-stream` remains
accepted as a compatibility alias for `--all-streams`.

## API Contract

The SDK repository carries the API contract at `openapi/sdk-v1.yaml`. Keep it in
sync with the Alphaine web backend before publishing SDK releases.

## Tests

```bash
uv run pytest -q
```
