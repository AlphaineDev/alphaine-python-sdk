import json

import httpx
import pytest

from alphaine import AlphaineClient, AlphaineRateLimitError


def make_client(tmp_path, direct_body=b"abc"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sdk/v1/me":
            return httpx.Response(200, json={"user": {"email": "user@example.com"}})
        if request.url.path == "/api/sdk/v1/data/list":
            prefix = request.url.params.get("prefix", "")
            if prefix == "":
                return httpx.Response(200, json={
                    "prefix": "",
                    "folders": [{"type": "folder", "name": "exchange=binance", "prefix": "exchange=binance/", "size": 6, "fileCount": 2, "summaryTruncated": False}],
                    "files": [],
                    "truncated": False,
                    "cursor": None,
                })
            if prefix == "exchange=binance/":
                return httpx.Response(200, json={
                    "prefix": prefix,
                    "folders": [{"type": "folder", "name": "stream=trades", "prefix": "exchange=binance/stream=trades/", "size": 6, "fileCount": 2, "summaryTruncated": False}],
                    "files": [],
                    "truncated": False,
                    "cursor": None,
                })
            if prefix == "exchange=binance/stream=trades/":
                return httpx.Response(200, json={
                    "prefix": prefix,
                    "folders": [],
                    "files": [
                        {"type": "file", "key": "exchange=binance/stream=trades/date=20260513/a.txt", "objectRef": "oref_a", "name": "a.txt", "size": len(direct_body), "uploadedAt": None, "etag": None},
                        {"type": "file", "key": "exchange=binance/stream=trades/date=20260514/b.txt", "objectRef": "oref_b", "name": "b.txt", "size": len(direct_body), "uploadedAt": None, "etag": None},
                    ],
                    "truncated": False,
                    "cursor": None,
                })
            if prefix == "exchange=binance/stream=trades/date=20260513/":
                return httpx.Response(200, json={
                    "prefix": prefix,
                    "folders": [],
                    "files": [
                        {"type": "file", "key": "exchange=binance/stream=trades/date=20260513/a.txt", "objectRef": "oref_a", "name": "a.txt", "size": len(direct_body), "uploadedAt": None, "etag": None},
                    ],
                    "truncated": False,
                    "cursor": None,
                })
            if prefix == "exchange=binance/stream=trades/date=20260514/":
                return httpx.Response(200, json={
                    "prefix": prefix,
                    "folders": [],
                    "files": [
                        {"type": "file", "key": "exchange=binance/stream=trades/date=20260514/b.txt", "objectRef": "oref_b", "name": "b.txt", "size": len(direct_body), "uploadedAt": None, "etag": None},
                    ],
                    "truncated": False,
                    "cursor": None,
                })
            if prefix == "folder/":
                return httpx.Response(200, json={
                    "prefix": prefix,
                    "folders": [],
                    "files": [{"type": "file", "key": "folder/file.txt", "objectRef": "oref_folder_file", "name": "file.txt", "size": len(direct_body), "uploadedAt": None, "etag": None}],
                    "truncated": False,
                    "cursor": None,
                })
            if prefix == "legacy-root/":
                return httpx.Response(200, json={
                    "prefix": prefix,
                    "folders": [{"type": "folder", "name": "folder", "prefix": "folder/", "size": 3, "fileCount": 1, "summaryTruncated": False}],
                    "files": [],
                    "truncated": False,
                    "cursor": None,
                })
            return httpx.Response(200, json={
                "prefix": prefix,
                "folders": [],
                "files": [{"type": "file", "key": "folder/file.txt", "objectRef": "oref_folder_file", "name": "file.txt", "size": len(direct_body), "uploadedAt": None, "etag": None}],
                "truncated": False,
                "cursor": None,
            })
        if request.url.path == "/api/sdk/v1/data/download-url":
            payload = json.loads(request.content.decode())
            key = payload.get("key") or "folder/file.txt"
            object_ref = payload.get("objectRef") or "oref_legacy"
            return httpx.Response(200, json={
                "key": key,
                "objectRef": object_ref,
                "url": "https://r2.example/file",
                "expiresAt": "2026-01-01T00:00:00.000Z",
                "size": len(direct_body),
                "etag": None,
                "filename": "file.txt",
            })
        if request.url.path == "/api/sdk/v1/data/download-batch":
            payload = json.loads(request.content.decode())
            keys = payload.get("keys") or []
            object_refs = payload.get("objectRefs") or []
            entries = [{"key": key, "objectRef": "oref_legacy"} for key in keys]
            ref_to_key = {
                "oref_a": "exchange=binance/stream=trades/date=20260513/a.txt",
                "oref_b": "exchange=binance/stream=trades/date=20260514/b.txt",
                "oref_folder_file": "folder/file.txt",
            }
            entries.extend({"key": ref_to_key.get(object_ref, "folder/file.txt"), "objectRef": object_ref} for object_ref in object_refs)
            return httpx.Response(200, json={
                "links": [{
                    "key": entry["key"],
                    "objectRef": entry["objectRef"],
                    "url": "https://r2.example/file",
                    "expiresAt": "2026-01-01T00:00:00.000Z",
                    "size": len(direct_body),
                    "etag": None,
                    "filename": entry["key"].rsplit("/", 1)[-1],
                } for entry in entries]
            })
        if request.url.host == "r2.example":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=direct_body)
        return httpx.Response(404, json={"error": "not found"})

    return AlphaineClient(
        api_key="alphaine_live_test",
        base_url="https://alphaine.test",
        transport=httpx.MockTransport(handler),
    )


def test_download_writes_part_then_final_file(tmp_path):
    client = make_client(tmp_path)
    target = client.download("folder/file.txt", tmp_path, show_progress=False)

    assert target.read_bytes() == b"abc"
    assert target.name == "file.txt"
    assert not target.with_name("file.txt.part").exists()


def test_download_prefix_preserves_remote_key(tmp_path):
    client = make_client(tmp_path)
    targets = client.download_prefix("folder/", tmp_path, workers=1, show_progress=False)

    assert len(targets) == 1
    assert (tmp_path / "folder" / "file.txt").read_bytes() == b"abc"


def test_download_urls_batches_at_sdk_limit(tmp_path):
    batch_sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sdk/v1/data/download-batch":
            payload = json.loads(request.content.decode())
            object_refs = payload.get("objectRefs") or []
            batch_sizes.append(len(object_refs))
            return httpx.Response(200, json={
                "links": [{
                    "key": f"folder/{object_ref}.txt",
                    "objectRef": object_ref,
                    "url": "https://r2.example/file",
                    "expiresAt": "2026-01-01T00:00:00.000Z",
                    "size": 3,
                    "etag": None,
                    "filename": f"{object_ref}.txt",
                } for object_ref in object_refs]
            })
        return httpx.Response(404, json={"error": "not found"})

    client = AlphaineClient(
        api_key="alphaine_live_test",
        base_url="https://alphaine.test",
        transport=httpx.MockTransport(handler),
    )

    links = client.download_urls({"objectRef": f"oref_{index}"} for index in range(45))

    assert len(links) == 45
    assert batch_sizes == [20, 20, 5]


def test_stream_helpers_download_selected_dates(tmp_path):
    client = make_client(tmp_path)

    assert client.list_streams() == [{
        "name": "trades",
        "prefix": "exchange=binance/stream=trades/",
        "size": 6,
        "fileCount": 2,
    }]

    dates = client.list_stream_dates("trades")
    assert [day["date"] for day in dates] == ["2026-05-13", "2026-05-14"]
    assert dates[0]["objectRefs"] == ["oref_a"]

    files = client.list_stream_files("trades", ["20260514"])
    assert [file["key"] for file in files] == ["exchange=binance/stream=trades/date=20260514/b.txt"]

    targets = client.download_stream("trades", "2026-05-14", tmp_path, workers=1, show_progress=False)
    assert len(targets) == 1
    assert (tmp_path / "exchange=binance" / "stream=trades" / "date=20260514" / "b.txt").read_bytes() == b"abc"


def test_api_rate_limit_retries_then_succeeds(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": "rate_limited", "message": "SDK usage limit exceeded", "retryAfterSeconds": 0},
            )
        return httpx.Response(200, json={"user": {"email": "user@example.com"}})

    client = AlphaineClient(
        api_key="alphaine_live_test",
        base_url="https://alphaine.test",
        transport=httpx.MockTransport(handler),
    )

    assert client.me()["user"]["email"] == "user@example.com"
    assert calls == 2


def test_api_rate_limit_exhaustion_includes_route_and_retry_after(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "0"},
            json={"error": "rate_limited", "message": "SDK usage limit exceeded", "retryAfterSeconds": 0},
        )

    client = AlphaineClient(
        api_key="alphaine_live_test",
        base_url="https://alphaine.test",
        api_retries=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AlphaineRateLimitError, match="/api/sdk/v1/me"):
        client.me()
