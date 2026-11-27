"""Offline tests for the host resolvers used by the toonworld source."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources import mega, toonworld_hosts  # noqa: E402


class FakeSession:
    """A stand in for a curl_cffi session."""

    def __init__(self, pages=None, payload=None):
        self.pages = pages or {}
        self.payload = payload
        self.calls = []

    def get(self, url, **_kwargs):
        self.calls.append(url)
        body = self.pages.get(url, "")
        return type("Response", (), {"status_code": 200, "text": body, "headers": {}})()

    def post(self, url, **_kwargs):
        self.calls.append(url)
        body = self.payload if self.payload is not None else "{}"
        return type("Response", (), {"status_code": 200, "text": body, "headers": {}})()


# --------------------------------------------------------------------------- #
# filepress
# --------------------------------------------------------------------------- #


def test_filepress_resolves_through_its_json_api(monkeypatch):
    payload = {
        "status": True,
        "data": {"fileName": "Episode.mkv", "downloadUrl": "https://cdn.filepress.dev/e.mkv"},
    }

    def _fetch(url, **_kwargs):
        import json

        return 200, json.dumps(payload), {}

    monkeypatch.setattr(toonworld_hosts.http, "fetch", _fetch)
    result = toonworld_hosts.resolve_filepress("0123456789abcdef01234567")
    assert result.ready
    assert result.url == "https://cdn.filepress.dev/e.mkv"


def test_filepress_reports_a_missing_file(monkeypatch):
    body = '{"status":false,"statusCode":400,"statusText":"File Not Found","error":"File Not Found"}'

    monkeypatch.setattr(toonworld_hosts.http, "fetch", lambda url, **_kw: (200, body, {}))
    result = toonworld_hosts.resolve_filepress("0123456789abcdef01234567")
    assert result.state == "missing"
    assert "File Not Found" in result.detail


def test_filepress_reports_an_invalid_id(monkeypatch):
    body = '{"status":false,"statusCode":400,"statusText":"Invaild File ID"}'

    monkeypatch.setattr(toonworld_hosts.http, "fetch", lambda url, **_kw: (200, body, {}))
    result = toonworld_hosts.resolve_filepress("not-a-real-id")
    assert result.state == "missing"
    assert "Invaild File ID" in result.detail


# --------------------------------------------------------------------------- #
# gdflix
# --------------------------------------------------------------------------- #


def test_gdflix_resolves_to_the_google_drive_link(monkeypatch):
    body = (
        '<html><body><iframe src="https://drive.google.com/file/d/1AbCdEf/view"></iframe>'
        "</body></html>"
    )

    monkeypatch.setattr(toonworld_hosts.http, "fetch", lambda url, **_kw: (200, body, {}))
    result = toonworld_hosts.resolve_gdflix("abc123")
    assert result.ready
    assert result.url.startswith("https://drive.google.com/file/d/1AbCdEf")


def test_gdflix_reports_a_removed_file(monkeypatch):
    body = "<html><h3>404! Page Not Found</h3><strong>no longer available!</strong></html>"

    monkeypatch.setattr(toonworld_hosts.http, "fetch", lambda url, **_kw: (200, body, {}))
    result = toonworld_hosts.resolve_gdflix("abc123")
    assert result.state == "missing"


# --------------------------------------------------------------------------- #
# hubcloud
# --------------------------------------------------------------------------- #


def test_hubcloud_passes_the_javascript_challenge(monkeypatch):
    challenge = (
        "<html><script>window.location.replace("
        "'https://hubcloud.link/drive/abc123?ch=1&js=TOKEN')</script></html>"
    )
    page = "<html><body>the file page</body></html>"
    session = FakeSession(pages={}, payload='{"success":true,"link":"https://cdn.hubcloud.dev/e.mkv"}')
    session.pages = {
        "https://hubcloud.link/drive/abc123": challenge,
        "https://hubcloud.link/drive/abc123?ch=1&js=TOKEN": page,
    }
    monkeypatch.setattr(toonworld_hosts, "_session", lambda: session)
    result = toonworld_hosts.resolve_hubcloud("abc123")
    assert result.ready
    assert result.url == "https://cdn.hubcloud.dev/e.mkv"
    assert any("ch=1" in call for call in session.calls)


def test_hubcloud_reports_a_forbidden_file(monkeypatch):
    session = FakeSession(pages={"https://hubcloud.link/drive/abc123": "Forbidden"})
    monkeypatch.setattr(toonworld_hosts, "_session", lambda: session)
    result = toonworld_hosts.resolve_hubcloud("abc123")
    assert result.state == "missing"


# --------------------------------------------------------------------------- #
# mega
# --------------------------------------------------------------------------- #


def test_mega_resolves_through_its_api(monkeypatch):
    monkeypatch.setattr(
        mega,
        "resolve_file",
        lambda handle, key, **_kw: (1234, "https://cdn.mega.nz/encrypted"),
    )
    result = toonworld_hosts.resolve_mega("abcdefgh#key0123456789")
    assert result.ready
    assert result.kind == "mega"
    assert result.extra["handle"] == "abcdefgh"


def test_mega_reports_an_unreachable_api(monkeypatch):
    def _boom(handle, key, **_kw):
        raise mega.MegaError("the MEGA API could not be reached")

    monkeypatch.setattr(mega, "resolve_file", _boom)
    result = toonworld_hosts.resolve_mega("abcdefgh#key0123456789")
    assert result.state == "unknown"
    assert "MEGA" in result.detail


@pytest.mark.parametrize(
    "value,expected",
    [
        ("abcdefgh#key0123456789", ("abcdefgh", "key0123456789")),
        ("https://mega.nz/file/abcdefgh#key0123456789", ("abcdefgh", "key0123456789")),
        ("abcdefgh", ("abcdefgh", "")),
    ],
)
def test_split_mega_id(value, expected):
    assert toonworld_hosts.split_mega_id(value) == expected


def test_mega_key_decoding():
    key = "zyeamm5tdieoc1ff469dzejd2je57qhw1wdsj4de3t5"
    assert len(mega.decode_key(key)) == 32


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "host,expected",
    [
        ("HubCloud", "hubcloud"),
        ("Filepress", "filepress"),
        ("GDFlix", "gdflix"),
        ("MEGA", "mega"),
        ("AppDrive", "appdrive"),
    ],
)
def test_normalize_host(host, expected):
    assert toonworld_hosts.normalize_host(host) == expected


def test_unknown_host_has_no_resolver():
    result = toonworld_hosts.resolve("SomeHost", "abc")
    assert result.state == "unknown"
    assert "SomeHost" in result.detail
