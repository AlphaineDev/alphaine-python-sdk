from __future__ import annotations

import argparse
from datetime import datetime, timezone
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import httpx

from .client import AlphaineClient, AlphaineError


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _apply_arg_defaults(args)

    try:
        if args.requires_client:
            credentials = _resolve_credentials(args)
            with AlphaineClient(
                api_key=credentials["api_key"],
                base_url=credentials["base_url"],
                trust_env=bool(credentials["trust_env"]),
            ) as client:
                result = args.handler(client, args)
        else:
            result = args.handler(args)
        _print_result(result, as_json=args.as_json)
        return 0
    except AlphaineError as exc:
        print(f"alphaine: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"alphaine: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    globals_parser = argparse.ArgumentParser(add_help=False)
    globals_parser.add_argument("--api-key", default=argparse.SUPPRESS, help="Alphaine API key. Defaults to ALPHAINE_API_KEY.")
    globals_parser.add_argument(
        "--base-url",
        default=argparse.SUPPRESS,
        help="Alphaine API base URL. Defaults to ALPHAINE_BASE_URL or production.",
    )
    globals_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        dest="as_json",
        help="Print machine-readable JSON.",
    )
    globals_parser.add_argument(
        "--no-progress",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable download progress bars.",
    )
    globals_parser.add_argument(
        "--network-mode",
        choices=["auto", "env", "direct"],
        default=argparse.SUPPRESS,
        help="Network mode: auto probes proxy vs direct, env uses proxy environment, direct ignores proxy environment.",
    )

    parser = argparse.ArgumentParser(
        prog="alphaine",
        description="Command line access to Alphaine datasets.",
        parents=[globals_parser],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_local_command(subparsers, "login", _cmd_login, globals_parser, help="Save an Alphaine API key.")

    _add_local_command(subparsers, "logout", _cmd_logout, globals_parser, help="Remove the saved Alphaine API key.")

    auth_parser = subparsers.add_parser("auth", help="Manage CLI authentication.")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)
    _add_local_command(auth_subparsers, "status", _cmd_auth_status, globals_parser, help="Show CLI authentication status.")

    _add_command(subparsers, "me", _cmd_me, globals_parser, help="Inspect the authenticated SDK token.")

    list_parser = _add_command(subparsers, "list", _cmd_list, globals_parser, help="List data folders and files.")
    list_parser.add_argument("prefix", nargs="?", default="", help="Data prefix to list.")

    streams_parser = _add_command(subparsers, "streams", _cmd_streams, globals_parser, help="List discoverable streams.")
    streams_parser.add_argument("--root-prefix", default="", help="Root prefix to scan for stream folders.")

    dates_parser = _add_command(subparsers, "dates", _cmd_dates, globals_parser, help="List available dates for a stream.")
    dates_parser.add_argument("stream", help="Stream name or stream=... prefix.")

    files_parser = _add_command(subparsers, "files", _cmd_files, globals_parser, help="List stream files.")
    files_parser.add_argument("stream", help="Stream name or stream=... prefix.")
    files_parser.add_argument("--date", action="append", dest="dates", help="Date to include, YYYY-MM-DD or YYYYMMDD.")

    download_parser = _add_command(
        subparsers,
        "download",
        _cmd_download,
        globals_parser,
        help="Download by stream/date selection or all available data.",
    )
    download_parser.add_argument("destination", help="Destination directory.")
    download_parser.add_argument("--stream", help="Stream name or stream=... prefix.")
    download_parser.add_argument("--date", action="append", dest="dates", help="Date to download.")
    download_parser.add_argument("--all-stream", "--all-streams", action="store_true", help="Download all streams.")
    download_parser.add_argument("--all-dates", action="store_true", help="Download all dates.")
    _add_download_options(download_parser)

    prefix_parser = _add_command(
        subparsers,
        "download-prefix",
        _cmd_download_prefix,
        globals_parser,
        help="Download every file under a prefix.",
    )
    prefix_parser.add_argument("prefix", help="Prefix to download.")
    prefix_parser.add_argument("destination", help="Destination directory.")
    _add_download_options(prefix_parser)

    stream_parser = _add_command(
        subparsers,
        "download-stream",
        _cmd_download_stream,
        globals_parser,
        help="Download a stream for one or more dates.",
    )
    stream_parser.add_argument("stream", help="Stream name or stream=... prefix.")
    stream_parser.add_argument("--date", action="append", dest="dates", required=True, help="Date to download.")
    stream_parser.add_argument("destination", help="Destination directory.")
    _add_download_options(stream_parser)

    return parser


def _apply_arg_defaults(args: argparse.Namespace) -> None:
    for name, default in {
        "api_key": None,
        "base_url": None,
        "as_json": False,
        "no_progress": False,
        "network_mode": None,
    }.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    if not hasattr(args, "requires_client"):
        setattr(args, "requires_client", True)


def _add_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    handler: Callable[[AlphaineClient, argparse.Namespace], Any],
    globals_parser: argparse.ArgumentParser,
    *,
    help: str,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, parents=[globals_parser], help=help)
    parser.set_defaults(handler=handler)
    parser.set_defaults(requires_client=True)
    return parser


def _add_local_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    handler: Callable[[argparse.Namespace], Any],
    globals_parser: argparse.ArgumentParser,
    *,
    help: str,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, parents=[globals_parser], help=help)
    parser.set_defaults(handler=handler)
    parser.set_defaults(requires_client=False)
    return parser


def _add_download_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workers", type=int, default=8, help="Concurrent download workers.")
    parser.add_argument("--retries", type=int, default=3, help="Download retries per file.")
    parser.add_argument("--dry-run", action="store_true", help="Preview files and skip/download decisions without downloading.")


def _cmd_login(args: argparse.Namespace) -> dict[str, Any]:
    api_key = args.api_key or getpass.getpass("Alphaine API key: ").strip()
    if not api_key:
        raise AlphaineError("API key is required.")

    config = _read_config()
    config["api_key"] = api_key
    if args.base_url:
        config["base_url"] = args.base_url.rstrip("/")
    _write_config(config)
    return {
        "kind": "message",
        "value": f"Saved Alphaine API key to {_config_path()}.",
    }


def _cmd_logout(_: argparse.Namespace) -> dict[str, Any]:
    path = _config_path()
    if path.exists():
        path.unlink()
        message = f"Removed Alphaine CLI credentials from {path}."
    else:
        message = "No saved Alphaine CLI credentials found."
    return {"kind": "message", "value": message}


def _cmd_auth_status(args: argparse.Namespace) -> dict[str, Any]:
    status = _auth_status(args)
    network = _network_status(args, _read_config())
    status.update(network)
    return {"kind": "auth_status", "value": status}


def _cmd_me(client: AlphaineClient, _: argparse.Namespace) -> dict[str, Any]:
    return {"kind": "me", "value": client.me()}


def _cmd_list(client: AlphaineClient, args: argparse.Namespace) -> dict[str, Any]:
    return {"kind": "list", "value": client.list(args.prefix)}


def _cmd_streams(client: AlphaineClient, args: argparse.Namespace) -> dict[str, Any]:
    return {"kind": "streams", "value": client.list_streams(args.root_prefix)}


def _cmd_dates(client: AlphaineClient, args: argparse.Namespace) -> dict[str, Any]:
    return {"kind": "dates", "value": client.list_stream_dates(args.stream)}


def _cmd_files(client: AlphaineClient, args: argparse.Namespace) -> dict[str, Any]:
    return {"kind": "files", "value": client.list_stream_files(args.stream, args.dates)}


def _cmd_download(client: AlphaineClient, args: argparse.Namespace) -> dict[str, Any]:
    if args.all_stream:
        if args.stream:
            raise AlphaineError("Use either --stream or --all-stream, not both.")
        if args.dates and not args.all_dates:
            raise AlphaineError("--date requires --stream. Use --all-dates with --all-stream.")
        if not args.all_dates:
            raise AlphaineError("--all-stream requires --all-dates.")
        if not args.no_progress:
            print(
                "Alphaine full download: listing all streams and dates in small batches. "
                "This may take a while.",
                file=sys.stderr,
            )
        if args.dry_run:
            return _dry_run_prefix(client, "", args)
        return _download_prefix(client, "", args)

    if not args.stream:
        raise AlphaineError("Pass --stream, or use --all-stream --all-dates.")

    if args.all_dates:
        if args.dates:
            raise AlphaineError("Use either --date or --all-dates, not both.")
        prefix = client.resolve_stream_prefix(args.stream)
        if args.dry_run:
            return _dry_run_prefix(client, prefix, args)
        return _download_prefix(client, prefix, args)

    if not args.dates:
        raise AlphaineError("Pass at least one --date, or use --all-dates.")

    if args.dry_run:
        files = client.list_stream_files(args.stream, args.dates)
        return _dry_run_files(files, args.destination)

    paths = client.download_stream(
        args.stream,
        args.dates,
        args.destination,
        workers=args.workers,
        retries=args.retries,
        show_progress=not args.no_progress,
    )
    return {"kind": "paths", "value": paths}


def _cmd_download_prefix(client: AlphaineClient, args: argparse.Namespace) -> dict[str, Any]:
    if args.dry_run:
        return _dry_run_prefix(client, args.prefix, args)
    return _download_prefix(client, args.prefix, args)


def _cmd_download_stream(client: AlphaineClient, args: argparse.Namespace) -> dict[str, Any]:
    if args.dry_run:
        files = client.list_stream_files(args.stream, args.dates)
        return _dry_run_files(files, args.destination)

    paths = client.download_stream(
        args.stream,
        args.dates,
        args.destination,
        workers=args.workers,
        retries=args.retries,
        show_progress=not args.no_progress,
    )
    return {"kind": "paths", "value": paths}


def _dry_run_prefix(client: AlphaineClient, prefix: str, args: argparse.Namespace) -> dict[str, Any]:
    return _dry_run_files(client.iter_files(prefix), args.destination)


def _dry_run_files(files: Iterable[dict[str, Any]], destination: str | os.PathLike[str]) -> dict[str, Any]:
    items = [_planned_download(file, destination) for file in files]
    total_bytes = sum(item["size"] for item in items)
    skip_bytes = sum(item["size"] for item in items if item["action"] == "skip")
    download_bytes = total_bytes - skip_bytes
    return {
        "kind": "dry_run",
        "value": {
            "totalFiles": len(items),
            "downloadFiles": sum(1 for item in items if item["action"] == "download"),
            "skipFiles": sum(1 for item in items if item["action"] == "skip"),
            "totalBytes": total_bytes,
            "downloadBytes": download_bytes,
            "skipBytes": skip_bytes,
            "files": items,
        },
    }


def _planned_download(file: dict[str, Any], destination: str | os.PathLike[str]) -> dict[str, Any]:
    key = str(file.get("key") or "")
    size = int(file.get("size") or 0)
    target = Path(destination) / key
    action = "skip" if target.exists() and target.stat().st_size == size else "download"
    return {
        "action": action,
        "key": key,
        "target": str(target),
        "size": size,
        "objectRef": str(file.get("objectRef") or ""),
    }


def _download_prefix(client: AlphaineClient, prefix: str, args: argparse.Namespace) -> dict[str, Any]:
    paths = client.download_prefix(
        prefix,
        args.destination,
        workers=args.workers,
        retries=args.retries,
        show_progress=not args.no_progress,
    )
    return {"kind": "paths", "value": paths}


def _config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "alphaine" / "config.json"


def _read_config() -> dict[str, str]:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AlphaineError(f"Unable to read Alphaine CLI config at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AlphaineError(f"Invalid Alphaine CLI config at {path}.")
    return {str(key): str(value) for key, value in data.items() if value is not None}


def _write_config(config: dict[str, str]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _resolve_credentials(args: argparse.Namespace) -> dict[str, str | None]:
    status = _auth_status(args)
    network_mode = _resolve_network_mode(args, status)
    return {
        "api_key": status.get("api_key"),
        "base_url": status.get("base_url"),
        "trust_env": network_mode == "env",
    }


def _auth_status(args: argparse.Namespace) -> dict[str, Any]:
    config = _read_config()
    env_api_key = os.getenv("ALPHAINE_API_KEY")
    env_base_url = os.getenv("ALPHAINE_BASE_URL")

    api_key = args.api_key or env_api_key or config.get("api_key")
    base_url = args.base_url or env_base_url or config.get("base_url")

    if args.api_key:
        source = "flag"
    elif env_api_key:
        source = "env"
    elif config.get("api_key"):
        source = "config"
    else:
        source = "missing"

    if args.base_url:
        base_url_source = "flag"
    elif env_base_url:
        base_url_source = "env"
    elif config.get("base_url"):
        base_url_source = "config"
    else:
        base_url_source = "default"

    return {
        "source": source,
        "api_key": api_key,
        "token": _mask_token(api_key),
        "base_url": base_url or "https://alphaine.com",
        "base_url_source": base_url_source,
        "config_path": str(_config_path()),
    }


def _resolve_network_mode(args: argparse.Namespace, auth_status: dict[str, Any]) -> str:
    config = _read_config()
    requested = args.network_mode or config.get("network_mode") or "auto"
    if requested == "env":
        return "env"
    if requested == "direct":
        return "direct"
    if not _proxy_env_present():
        return "direct"

    selected = _probe_network_mode(
        api_key=auth_status.get("api_key"),
        base_url=auth_status.get("base_url"),
    )
    config["network_mode"] = selected
    config["network_mode_source"] = "auto"
    config["network_mode_updated_at"] = datetime.now(timezone.utc).isoformat()
    config["proxy_env_detected"] = "true" if _proxy_env_present() else "false"
    _write_config(config)
    return selected


def _probe_network_mode(api_key: str | None, base_url: str | None) -> str:
    modes = ["env", "direct"] if _proxy_env_present() else ["direct", "env"]
    failures: dict[str, str] = {}
    for mode in modes:
        try:
            with AlphaineClient(
                api_key=api_key,
                base_url=base_url,
                timeout=10.0,
                api_retries=1,
                trust_env=mode == "env",
            ) as client:
                client.me()
            return mode
        except AlphaineError:
            return mode
        except httpx.HTTPError as exc:
            failures[mode] = str(exc)

    detail = "; ".join(f"{mode}: {message}" for mode, message in failures.items())
    raise AlphaineError(f"Unable to reach Alphaine using proxy environment or direct mode. {detail}")


def _network_status(args: argparse.Namespace, config: dict[str, str]) -> dict[str, Any]:
    mode = args.network_mode or config.get("network_mode") or "auto"
    source = "flag" if args.network_mode else ("config" if config.get("network_mode") else "auto")
    return {
        "network_mode": mode,
        "network_mode_source": source,
        "proxy_env_detected": _proxy_env_present(),
        "network_mode_updated_at": config.get("network_mode_updated_at"),
    }


def _proxy_env_present() -> bool:
    names = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
    return any(os.getenv(name) for name in names)


def _mask_token(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return f"{value[:2]}...{value[-2:]}"
    return f"{value[:8]}...{value[-4:]}"


def _print_result(result: dict[str, Any], *, as_json: bool) -> None:
    kind = result["kind"]
    value = result["value"]
    if as_json:
        if kind == "paths":
            payload = {"paths": [str(path) for path in value]}
        elif kind == "auth_status":
            payload = {key: item for key, item in value.items() if key != "api_key"}
        else:
            payload = value
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return

    if kind == "me":
        _print_mapping(value)
    elif kind == "list":
        _print_listing(value)
    elif kind == "streams":
        _print_table(value, ["name", "prefix", "fileCount", "size"])
    elif kind == "dates":
        _print_table(value, ["date", "fileCount", "size"])
    elif kind == "files":
        _print_table(value, ["key", "size", "objectRef"])
    elif kind == "paths":
        for path in value:
            print(Path(path))
    elif kind == "message":
        print(value)
    elif kind == "auth_status":
        _print_auth_status(value)
    elif kind == "dry_run":
        _print_dry_run(value)


def _print_mapping(value: dict[str, Any], *, prefix: str = "") -> None:
    for key in sorted(value):
        item = value[key]
        name = f"{prefix}{key}"
        if isinstance(item, dict):
            _print_mapping(item, prefix=f"{name}.")
        elif isinstance(item, list):
            print(f"{name}: {len(item)} item(s)")
        else:
            print(f"{name}: {item}")


def _print_listing(listing: dict[str, Any]) -> None:
    prefix = listing.get("prefix", "")
    print(f"prefix: {prefix}")
    folders = listing.get("folders", [])
    files = listing.get("files", [])
    if folders:
        print("\nfolders")
        _print_table(folders, ["name", "prefix", "fileCount", "size"])
    if files:
        print("\nfiles")
        _print_table(files, ["name", "key", "size", "objectRef"])
    if not folders and not files:
        print("empty")
    cursor = listing.get("cursor")
    if cursor:
        print(f"\ncursor: {cursor}")


def _print_auth_status(status: dict[str, Any]) -> None:
    print(f"status: {'authenticated' if status['source'] != 'missing' else 'missing'}")
    print(f"source: {status['source']}")
    if status["token"]:
        print(f"token: {status['token']}")
    print(f"base_url: {status['base_url']}")
    print(f"base_url_source: {status['base_url_source']}")
    print(f"network_mode: {status['network_mode']}")
    print(f"network_mode_source: {status['network_mode_source']}")
    print(f"proxy_env_detected: {status['proxy_env_detected']}")
    if status.get("network_mode_updated_at"):
        print(f"network_mode_updated_at: {status['network_mode_updated_at']}")
    print(f"config_path: {status['config_path']}")


def _print_dry_run(plan: dict[str, Any]) -> None:
    print(
        "dry-run: "
        f"{plan['downloadFiles']} download, "
        f"{plan['skipFiles']} skip, "
        f"{plan['totalFiles']} total, "
        f"{plan['downloadBytes']:,} B to download"
    )
    _print_table(plan["files"], ["action", "size", "key", "target"])


def _print_table(rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> None:
    if not rows:
        print("empty")
        return
    widths = {
        column: max(len(column), *(len(_format_cell(row.get(column))) for row in rows))
        for column in columns
    }
    print("  ".join(column.ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(_format_cell(row.get(column)).ljust(widths[column]) for column in columns))


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
