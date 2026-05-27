from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx
from tqdm.auto import tqdm

SDK_DOWNLOAD_BATCH_LIMIT = 20


class AlphaineError(RuntimeError):
    pass


class AlphaineRateLimitError(AlphaineError):
    pass


class AlphaineClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 30.0,
        api_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        trust_env: bool = True,
    ) -> None:
        self.api_key = api_key or os.getenv("ALPHAINE_API_KEY")
        if not self.api_key:
            raise AlphaineError("Pass api_key or set ALPHAINE_API_KEY.")
        self.base_url = (base_url or os.getenv("ALPHAINE_BASE_URL") or "https://alphaine.com").rstrip("/")
        self.api_retries = max(1, api_retries)
        self._http = httpx.Client(timeout=timeout, follow_redirects=True, transport=transport, trust_env=trust_env)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AlphaineClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = f"Bearer {self.api_key}"
        last_response: httpx.Response | None = None
        for attempt in range(self.api_retries):
            response = self._http.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            last_response = response
            if response.status_code != 429:
                return self._parse_response(response, path)

            retry_after = self._retry_after_seconds(response)
            if attempt + 1 >= self.api_retries:
                break
            tqdm.write(f"Alphaine API rate limited on {path}; retrying in {retry_after:.0f}s.")
            time.sleep(retry_after)

        assert last_response is not None
        message = self._response_message(last_response)
        retry_after = self._retry_after_seconds(last_response)
        raise AlphaineRateLimitError(
            f"Alphaine rate limited {path}; retryAfter={retry_after:.0f}s; message={message}"
        )

    def _parse_response(self, response: httpx.Response, path: str) -> Any:
        if response.status_code >= 400:
            message = self._response_message(response)
            if "Too many subrequests" in message:
                message = (
                    "Alphaine hit a Cloudflare Worker subrequest limit while preparing this request. "
                    "The SDK now downloads in smaller batches; retry, or narrow the request to a stream/date "
                    "if the service is still processing a very large listing."
                )
            raise AlphaineError(message or f"Alphaine request failed ({response.status_code}) on {path}.")
        if not response.content:
            return None
        return response.json()

    @staticmethod
    def _response_message(response: httpx.Response) -> str:
        try:
            body = response.json()
            return str(body.get("message") or body.get("error") or response.text)
        except ValueError:
            return response.text

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float:
        value = response.headers.get("Retry-After")
        if not value:
            return 1.0
        try:
            return max(0.0, float(value))
        except ValueError:
            return 1.0

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/api/sdk/v1/me")

    def list(self, prefix: str = "", cursor: str | None = None) -> dict[str, Any]:
        params = {"prefix": prefix}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/api/sdk/v1/data/list", params=params)

    def iter_files(self, prefix: str = "") -> Iterable[dict[str, Any]]:
        queue = [prefix]
        while queue:
            current = queue.pop(0)
            cursor: str | None = None
            while True:
                listing = self.list(current, cursor=cursor)
                yield from listing.get("files", [])
                queue.extend(folder["prefix"] for folder in listing.get("folders", []))
                cursor = listing.get("cursor")
                if not cursor:
                    break

    @staticmethod
    def _download_selector(item: str | dict[str, Any]) -> dict[str, str]:
        if isinstance(item, dict):
            object_ref = item.get("objectRef")
            if object_ref:
                return {"objectRef": str(object_ref)}
            key = item.get("key")
            if key:
                return {"key": str(key)}
            raise AlphaineError("Download item must include objectRef or key.")
        return {"key": str(item)}

    def download_url(self, key: str | dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/sdk/v1/data/download-url", json=self._download_selector(key))

    def download_urls(self, keys: Iterable[str | dict[str, Any]]) -> list[dict[str, Any]]:
        key_list = list(keys)
        links: list[dict[str, Any]] = []
        for index in range(0, len(key_list), SDK_DOWNLOAD_BATCH_LIMIT):
            batch = key_list[index:index + SDK_DOWNLOAD_BATCH_LIMIT]
            object_refs: list[str] = []
            legacy_keys: list[str] = []
            for item in batch:
                selector = self._download_selector(item)
                if "objectRef" in selector:
                    object_refs.append(selector["objectRef"])
                else:
                    legacy_keys.append(selector["key"])
            payload: dict[str, list[str]] = {}
            if object_refs:
                payload["objectRefs"] = object_refs
            if legacy_keys:
                payload["keys"] = legacy_keys
            response = self._request("POST", "/api/sdk/v1/data/download-batch", json=payload)
            links.extend(response.get("links", []))
        return links

    def download(
        self,
        key: str | dict[str, Any],
        destination: str | os.PathLike[str],
        *,
        retries: int = 3,
        show_progress: bool = True,
    ) -> Path:
        link = self.download_url(key)
        return self._download_link(link, destination, preserve_key=False, retries=retries, show_progress=show_progress)

    def download_many(
        self,
        keys: Iterable[str | dict[str, Any]],
        destination_dir: str | os.PathLike[str],
        *,
        workers: int = 8,
        retries: int = 3,
        show_progress: bool = True,
    ) -> list[Path]:
        links = self.download_urls(keys)
        return self._download_links(links, destination_dir, workers=workers, retries=retries, show_progress=show_progress)

    def _download_items_in_batches(
        self,
        keys: Iterable[str | dict[str, Any]],
        destination_dir: str | os.PathLike[str],
        *,
        workers: int,
        retries: int,
        show_progress: bool,
    ) -> list[Path]:
        paths: list[Path] = []
        batch: list[str | dict[str, Any]] = []
        for item in keys:
            batch.append(item)
            if len(batch) >= SDK_DOWNLOAD_BATCH_LIMIT:
                paths.extend(self.download_many(batch, destination_dir, workers=workers, retries=retries, show_progress=show_progress))
                batch = []
        if batch:
            paths.extend(self.download_many(batch, destination_dir, workers=workers, retries=retries, show_progress=show_progress))
        return paths

    def download_prefix(
        self,
        prefix: str,
        destination_dir: str | os.PathLike[str],
        *,
        workers: int = 8,
        retries: int = 3,
        show_progress: bool = True,
    ) -> list[Path]:
        return self._download_items_in_batches(
            self.iter_files(prefix),
            destination_dir,
            workers=workers,
            retries=retries,
            show_progress=show_progress,
        )

    def list_streams(self, root_prefix: str = "", *, max_depth: int = 6) -> list[dict[str, Any]]:
        streams: list[dict[str, Any]] = []
        queue: list[tuple[str, int]] = [(root_prefix, 0)]
        seen: set[str] = set()
        while queue:
            prefix, depth = queue.pop(0)
            if prefix in seen or depth > max_depth:
                continue
            seen.add(prefix)
            listing = self.list(prefix)
            for folder in listing.get("folders", []):
                stream_name = self._stream_name_from_prefix(folder["prefix"])
                if stream_name:
                    streams.append({
                        "name": stream_name,
                        "prefix": folder["prefix"],
                        "size": folder.get("size", 0),
                        "fileCount": folder.get("fileCount", 0),
                    })
                else:
                    queue.append((folder["prefix"], depth + 1))
        return sorted(streams, key=lambda item: item["name"])

    def list_stream_dates(self, stream: str) -> list[dict[str, Any]]:
        days: dict[str, dict[str, Any]] = {}
        for file in self.iter_files(self.resolve_stream_prefix(stream)):
            date = self._date_from_key(file["key"])
            if not date:
                continue
            day = days.setdefault(date, {"date": date, "size": 0, "fileCount": 0, "keys": [], "objectRefs": []})
            day["size"] += int(file.get("size") or 0)
            day["fileCount"] += 1
            day["keys"].append(file["key"])
            if file.get("objectRef"):
                day["objectRefs"].append(file["objectRef"])
        return [days[date] for date in sorted(days)]

    def list_stream_files(self, stream: str, dates: str | Sequence[str] | None = None) -> list[dict[str, Any]]:
        wanted_dates = self._normalize_dates(dates)
        if not wanted_dates:
            return list(self.iter_files(self.resolve_stream_prefix(stream)))

        files: list[dict[str, Any]] = []
        stream_prefix = self.resolve_stream_prefix(stream)
        for date in sorted(wanted_dates):
            files.extend(self.iter_files(f"{stream_prefix}date={date.replace('-', '')}/"))
        return files

    def download_stream(
        self,
        stream: str,
        dates: str | Sequence[str],
        destination_dir: str | os.PathLike[str],
        *,
        workers: int = 8,
        retries: int = 3,
        show_progress: bool = True,
    ) -> list[Path]:
        wanted_dates = sorted(self._normalize_dates(dates))
        stream_prefix = self.resolve_stream_prefix(stream)
        files_by_date: dict[str, list[dict[str, Any]]] = {
            date: list(self.iter_files(f"{stream_prefix}date={date.replace('-', '')}/"))
            for date in wanted_dates
        }
        file_count = sum(len(files) for files in files_by_date.values())
        if file_count == 0:
            raise AlphaineError(f"No files found for stream={stream!r} dates={dates!r}.")
        if not show_progress:
            return self._download_items_in_batches(
                (file for files in files_by_date.values() for file in files),
                destination_dir,
                workers=workers,
                retries=retries,
                show_progress=False,
            )

        total_bytes = sum(int(file.get("size") or 0) for files in files_by_date.values() for file in files)
        print(
            f"Alphaine download stream={stream} dates={len(wanted_dates)} "
            f"files={file_count} bytes={total_bytes:,} -> {destination_dir}"
        )

        paths: list[Path] = []
        date_bar = tqdm(total=len(wanted_dates), desc=f"{stream} dates", unit="date", position=0)
        try:
            for index, date in enumerate(wanted_dates, start=1):
                date_files = files_by_date.get(date, [])
                date_size = sum(int(file.get("size") or 0) for file in date_files)
                date_bar.set_postfix_str(f"{date} {index}/{len(wanted_dates)} {len(date_files)} files {date_size:,} B")
                for batch_index in range(0, len(date_files), SDK_DOWNLOAD_BATCH_LIMIT):
                    links = self.download_urls(date_files[batch_index:batch_index + SDK_DOWNLOAD_BATCH_LIMIT])
                    for link in links:
                        target = self._download_link(
                            link,
                            destination_dir,
                            preserve_key=True,
                            retries=retries,
                            show_progress=True,
                            progress_position=1,
                            progress_desc=Path(str(link["key"])).name,
                        )
                        paths.append(target)
                date_bar.update(1)
        finally:
            date_bar.close()
        return paths

    def resolve_stream_prefix(self, stream: str) -> str:
        value = stream.strip("/")
        if not value:
            raise AlphaineError("Stream is required.")
        if "stream=" in value:
            return f"{value}/" if not value.endswith("/") else value

        normalized = value.replace("stream=", "")
        for candidate in self.list_streams():
            if candidate["name"] == normalized or candidate["prefix"].rstrip("/").endswith(f"stream={normalized}"):
                return candidate["prefix"]
        raise AlphaineError(f"Unknown stream: {stream}")

    def _download_links(
        self,
        links: list[dict[str, Any]],
        destination_dir: str | os.PathLike[str],
        *,
        workers: int,
        retries: int,
        show_progress: bool,
    ) -> list[Path]:
        if not links:
            return []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = [
                executor.submit(
                    self._download_link,
                    link,
                    destination_dir,
                    preserve_key=True,
                    retries=retries,
                    show_progress=show_progress,
                )
                for link in links
            ]
            return [future.result() for future in as_completed(futures)]

    def _download_link(
        self,
        link: dict[str, Any],
        destination: str | os.PathLike[str],
        *,
        preserve_key: bool,
        retries: int,
        show_progress: bool,
        progress_position: int = 0,
        progress_desc: str | None = None,
    ) -> Path:
        target = self._target_path(link, destination, preserve_key=preserve_key)
        if target.exists() and target.stat().st_size == int(link.get("size") or 0):
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(f"{target.name}.part")
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                self._stream_to_file(
                    link["url"],
                    part,
                    int(link.get("size") or 0),
                    show_progress,
                    position=progress_position,
                    desc=progress_desc or Path(str(link.get("key") or target.name)).name,
                )
                part.replace(target)
                return target
            except Exception as exc:  # noqa: BLE001 - surface the last transport or file error.
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(0.5 * (attempt + 1))
        raise AlphaineError(f"Unable to download {link.get('key')}: {last_error}") from last_error

    def _stream_to_file(
        self,
        url: str,
        path: Path,
        total: int,
        show_progress: bool,
        *,
        position: int = 0,
        desc: str | None = None,
    ) -> None:
        with self._http.stream("GET", url) as response:
            response.raise_for_status()
            progress = tqdm(
                total=total or None,
                desc=desc or path.name,
                unit="B",
                unit_scale=True,
                disable=not show_progress,
                leave=False,
                position=position,
            )
            try:
                with path.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        if chunk:
                            handle.write(chunk)
                            progress.update(len(chunk))
            finally:
                progress.close()

    @staticmethod
    def _target_path(link: dict[str, Any], destination: str | os.PathLike[str], *, preserve_key: bool) -> Path:
        base = Path(destination)
        if preserve_key:
            return base / str(link["key"])
        if str(destination).endswith(("/", "\\")) or base.suffix == "":
            return base / str(link.get("filename") or Path(str(link["key"])).name)
        return base

    @staticmethod
    def _stream_name_from_prefix(prefix: str) -> str | None:
        for part in prefix.split("/"):
            if part.startswith("stream="):
                name = part.removeprefix("stream=")
                return "binance_markprice_all" if name == "mark_price_all" else name
        return None

    @staticmethod
    def _date_from_key(key: str) -> str | None:
        for part in key.split("/"):
            match = re.match(r"^date=(\d{4})(\d{2})(\d{2})(?:\.[^/]*)?$", part)
            if match:
                return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
        return None

    @staticmethod
    def _normalize_dates(dates: str | Sequence[str] | None) -> set[str]:
        if dates is None:
            return set()
        values = [dates] if isinstance(dates, str) else list(dates)
        normalized = set()
        for value in values:
            compact = str(value).strip().replace("-", "")
            if not re.match(r"^\d{8}$", compact):
                raise AlphaineError(f"Invalid date: {value}. Use YYYY-MM-DD or YYYYMMDD.")
            normalized.add(f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}")
        return normalized
