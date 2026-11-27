"""A small MEGA client.

MEGA files are stored encrypted; the download URL is fetched from the public
API and the payload is decrypted with AES-128-CTR.  The file key is the
second half of a ``mega.nz/file/<handle>#<key>`` link.

The API refuses connections from some networks.  When that happens the
helpers raise :class:`MegaError` so the caller can report it and fail over to
another host.
"""

from __future__ import annotations

import base64
import binascii
import os
import shutil
import subprocess
import tempfile
from typing import Callable, Optional, Tuple

API_URL = "https://g.api.mega.co.nz/cs?id=1"
FILE_URL = "https://mega.nz/file/{handle}#{key}"


class MegaError(Exception):
    """Raised when a MEGA file cannot be resolved or downloaded."""


def decode_key(key: str) -> bytes:
    """Decode the base64url file key of a MEGA link."""
    if not key:
        raise MegaError("the MEGA link does not contain a file key")
    padded = key.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    try:
        raw = base64.b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise MegaError("the MEGA file key is not valid base64") from exc
    if len(raw) != 32:
        raise MegaError("the MEGA file key does not hold 32 bytes")
    return raw


def _api_request(command, timeout: int = 45):
    import json

    try:
        from curl_cffi import requests as curl_requests
    except Exception:
        curl_requests = None

    payload = json.dumps([command]).encode()
    headers = {"Content-Type": "application/json"}
    try:
        if curl_requests is not None:
            response = curl_requests.post(
                API_URL,
                data=payload,
                headers=headers,
                timeout=timeout,
                impersonate="chrome",
            )
            return response.text
        import urllib.request

        request = urllib.request.Request(API_URL, data=payload, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        raise MegaError(f"the MEGA API could not be reached: {exc}") from exc


def resolve_file(handle: str, key: str, *, timeout: int = 45) -> Optional[Tuple[int, str]]:
    """Ask MEGA for the download URL of a file.

    Returns ``(size, url)`` or ``None`` when MEGA does not know the file.
    """
    import json

    answer = _api_request(["g", handle, key], timeout=timeout).strip()
    try:
        payload = json.loads(answer)
    except json.JSONDecodeError as exc:
        raise MegaError("the MEGA API returned an unreadable answer") from exc

    if not isinstance(payload, list) or not payload:
        raise MegaError("the MEGA API returned an unexpected answer")
    if isinstance(payload[0], int) and payload[0] < 0:
        raise MegaError(f"the MEGA API rejected the request (code {payload[0]})")
    if len(payload) < 2 or not isinstance(payload[1], str):
        raise MegaError("the MEGA API did not return a download URL")
    return int(payload[0]), payload[1]


def _decrypt_stream(source, destination, key: bytes) -> None:
    """Decrypt an encrypted MEGA payload into *destination*."""
    aes_key = key[:16]
    counter = key[16:32]
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except Exception:
        Cipher = None

    if Cipher is not None:
        decryptor = Cipher(algorithms.AES(aes_key), modes.CTR(counter)).decryptor()
        with open(destination, "wb") as handle:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(decryptor.update(chunk))
            handle.write(decryptor.finalize())
        return

    openssl = shutil.which("openssl")
    if not openssl:
        raise MegaError("neither cryptography nor openssl is available for decryption")
    process = subprocess.Popen(
        [
            openssl,
            "enc",
            "-aes-128-ctr",
            "-d",
            "-K",
            aes_key.hex(),
            "-iv",
            counter.hex(),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            process.stdin.write(chunk)
        process.stdin.close()
        with open(destination, "wb") as handle:
            while True:
                chunk = process.stdout.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    finally:
        process.stdout.close()
        error = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
        if process.stderr:
            process.stderr.close()
        code = process.wait()
    if code != 0:
        raise MegaError(f"the MEGA payload could not be decrypted: {error.strip()[:120]}")


def download(
    handle: str,
    key: str,
    destination: str,
    *,
    timeout: int = 45,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Download and decrypt a MEGA file to *destination*."""
    resolved = resolve_file(handle, key, timeout=timeout)
    if not resolved:
        raise MegaError("MEGA does not know this file")
    size, url = resolved

    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        raise MegaError(f"the MEGA download could not be started: {exc}") from exc

    handle_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(destination) or ".", suffix=".part")
    os.close(handle_fd)
    try:
        with response as stream, open(temp_path, "wb") as target:
            source = _skip_meta_mac(stream)
            _decrypt_stream(source, target, decode_key(key))
        os.replace(temp_path, destination)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    if on_progress:
        on_progress(size, size)
    return destination


def _skip_meta_mac(stream):
    """Return a stream that starts after the 16 byte meta mac."""
    import io

    data = stream.read(16)
    if len(data) < 16:
        return io.BytesIO(b"")
    return _PrefixedStream(stream, data)


class _PrefixedStream:
    """A read only stream that yields *prefix* before the wrapped stream."""

    def __init__(self, stream, prefix: bytes) -> None:
        self._stream = stream
        self._prefix = prefix

    def read(self, size: int = -1) -> bytes:
        if self._prefix:
            if size is None or size < 0:
                data, self._prefix = self._prefix, b""
                return data + self._stream.read()
            chunk, self._prefix = self._prefix[:size], self._prefix[size:]
            if chunk:
                return chunk
            return self._stream.read(size)
        return self._stream.read(size)
