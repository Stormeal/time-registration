from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace

import pytest

from qi_flow.infrastructure.updates import ReleaseClient, UpdateError


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def release_payload(version: str = "v0.2.6", digest: str | None = None) -> bytes:
    return json.dumps(
        {
            "tag_name": version,
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "QI-Flow-Update.zip",
                    "browser_download_url": (
                        "https://github.com/Stormeal/time-registration/releases/download/"
                        f"{version}/QI-Flow-Update.zip"
                    ),
                    "digest": f"sha256:{digest or 'a' * 64}",
                    "size": 5,
                }
            ],
        }
    ).encode()


def test_release_check_finds_only_new_stable_versions() -> None:
    client = ReleaseClient(lambda *_args, **_kwargs: Response(release_payload()))

    update = client.check()

    assert update is not None
    assert update.version == "v0.2.6"
    assert update.sha256 == "a" * 64


@pytest.mark.parametrize("version", ["v0.2.4", "v0.2.2"])
def test_release_check_returns_none_when_not_newer(version: str) -> None:
    client = ReleaseClient(lambda *_args, **_kwargs: Response(release_payload(version)))

    assert client.check() is None


@pytest.mark.parametrize(
    ("version", "digest"),
    [("nightly", None), ("v0.2.6", "sha512:" + "a" * 128)],
)
def test_release_check_rejects_untrusted_or_unversioned_metadata(
    version: str, digest: str | None
) -> None:
    client = ReleaseClient(lambda *_args, **_kwargs: Response(release_payload(version, digest)))

    with pytest.raises(UpdateError):
        client.check()


def test_download_checks_size_and_digest_before_returning(tmp_path) -> None:
    payload = b"zip bytes"
    digest = hashlib.sha256(payload).hexdigest()
    responses = [Response(release_payload()), Response(payload)]
    client = ReleaseClient(lambda *_args, **_kwargs: responses.pop(0))
    update = client.check()
    assert update is not None
    update = replace(update, size=len(payload), sha256=digest)

    downloaded = client.download(update, tmp_path / "updates" / "update.zip")

    assert downloaded.read_bytes() == payload


def test_download_reports_received_bytes_while_streaming(tmp_path) -> None:
    payload = b"zip bytes"
    digest = hashlib.sha256(payload).hexdigest()
    responses = [Response(release_payload()), Response(payload)]
    client = ReleaseClient(lambda *_args, **_kwargs: responses.pop(0))
    update = client.check()
    assert update is not None
    update = replace(update, size=len(payload), sha256=digest)
    progress: list[tuple[int, int]] = []

    client.download(
        update,
        tmp_path / "update.zip",
        progress=lambda received, total: progress.append((received, total)),
    )

    assert progress == [(9, 9)]


def test_download_removes_a_package_that_fails_digest_verification(tmp_path) -> None:
    responses = [Response(release_payload()), Response(b"oops!")]
    client = ReleaseClient(lambda *_args, **_kwargs: responses.pop(0))
    update = client.check()
    assert update is not None
    destination = tmp_path / "update.zip"

    with pytest.raises(UpdateError, match="integrity"):
        client.download(update, destination)

    assert not destination.exists()
