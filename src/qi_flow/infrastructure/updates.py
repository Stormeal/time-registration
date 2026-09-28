"""Privacy-preserving checks and verified downloads from the public QI Flow release feed."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qi_flow import __version__

_RELEASE_URL = "https://api.github.com/repos/Stormeal/time-registration/releases/latest"
_ASSET_NAME = "QI-Flow-Update.zip"
_ASSET_PREFIX = "https://github.com/Stormeal/time-registration/releases/download/"
_MAX_PACKAGE_BYTES = 1_000_000_000
_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True, slots=True)
class AvailableUpdate:
    version: str
    download_url: str
    sha256: str
    size: int


class UpdateError(Exception):
    """Safe-to-display update check or download failure."""


class ReleaseClient:
    """Read the public GitHub release feed and stage its verified app bundle."""

    def __init__(self, opener: Callable[..., Any] = urllib.request.urlopen) -> None:
        self._opener = opener

    def check(self) -> AvailableUpdate | None:
        request = urllib.request.Request(
            _RELEASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "QI-Flow-Updater",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener(request, timeout=15) as response:
                payload = json.load(response)
        except (
            OSError,
            urllib.error.URLError,
            http.client.HTTPException,
            json.JSONDecodeError,
        ) as error:
            raise UpdateError("Could not reach the QI Flow release service.") from error
        except (TypeError, ValueError) as error:
            raise UpdateError("The release service returned invalid update information.") from error

        if not isinstance(payload, dict) or payload.get("draft") or payload.get("prerelease"):
            raise UpdateError("The release service returned invalid update information.")
        version = payload.get("tag_name")
        if not isinstance(version, str):
            raise UpdateError("The release version is not in a supported format.")
        match = _VERSION_PATTERN.fullmatch(version)
        if match is None:
            raise UpdateError("The release version is not in a supported format.")
        remote_version = tuple(int(part) for part in match.groups())
        local_match = _VERSION_PATTERN.fullmatch(__version__)
        if local_match is None:
            raise UpdateError("This QI Flow build has an invalid version and cannot update.")
        if remote_version <= tuple(int(part) for part in local_match.groups()):
            return None

        assets = payload.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("The release does not contain a valid update package.")
        for asset in assets:
            if not isinstance(asset, dict) or asset.get("name") != _ASSET_NAME:
                continue
            url = asset.get("browser_download_url")
            digest = asset.get("digest")
            size = asset.get("size")
            if (
                not isinstance(url, str)
                or not url.startswith(_ASSET_PREFIX)
                or not isinstance(digest, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size <= 0
                or size > _MAX_PACKAGE_BYTES
            ):
                raise UpdateError("The release update package is missing valid integrity metadata.")
            return AvailableUpdate(version, url, digest.removeprefix("sha256:"), size)
        raise UpdateError("The release does not contain a valid update package.")

    def download(
        self,
        update: AvailableUpdate,
        destination: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        if not update.download_url.startswith(_ASSET_PREFIX):
            raise UpdateError("The update package URL is not trusted.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            update.download_url,
            headers={"Accept": "application/octet-stream", "User-Agent": "QI-Flow-Updater"},
        )
        digest = hashlib.sha256()
        received = 0
        try:
            with self._opener(request, timeout=30) as response, destination.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > update.size or received > _MAX_PACKAGE_BYTES:
                        raise UpdateError("The update package exceeded its published size.")
                    digest.update(chunk)
                    output.write(chunk)
                    if progress is not None:
                        progress(received, update.size)
            if received != update.size or digest.hexdigest() != update.sha256:
                raise UpdateError("The update package failed its integrity check.")
        except (OSError, urllib.error.URLError, http.client.HTTPException) as error:
            destination.unlink(missing_ok=True)
            raise UpdateError("The update package could not be downloaded.") from error
        except UpdateError:
            destination.unlink(missing_ok=True)
            raise
        return destination
