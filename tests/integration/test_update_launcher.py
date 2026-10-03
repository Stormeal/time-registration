"""Prepare the verified updater command without launching or releasing runtime ownership."""

import importlib

import pytest

from qi_flow.application.desktop import AvailableUpdate, UpdateError


def test_prepares_staged_helper_and_verified_arguments_without_launch(tmp_path):
    module = importlib.import_module("qi_flow.infrastructure.update_launcher")
    bundle = tmp_path / "QI Flow"
    bundle.mkdir()
    executable, helper = bundle / "QI Flow.exe", bundle / "QI Flow Updater.exe"
    executable.write_bytes(b"synthetic application")
    helper.write_bytes(b"synthetic helper")
    archive = tmp_path / "verified.zip"
    archive.write_bytes(b"synthetic archive")
    update = AvailableUpdate("v0.2.7", "unused", "a" * 64, 17)
    command = module.prepare_update(update, archive, tmp_path / "data", executable, 1234)
    assert command.program != str(helper)
    assert command.arguments == (
        "--pid",
        "1234",
        "--archive",
        str(archive),
        "--install-dir",
        str(bundle),
        "--sha256",
        "a" * 64,
    )
    assert (
        tmp_path / "data" / "updates" / "QI Flow Updater.exe"
    ).read_bytes() == helper.read_bytes()


def test_source_checkout_refuses_updater_staging(tmp_path):
    module = importlib.import_module("qi_flow.infrastructure.update_launcher")
    update = AvailableUpdate("v0.2.7", "unused", "a" * 64, 17)
    with pytest.raises(UpdateError, match="installed Windows"):
        module.prepare_update(
            update, tmp_path / "package.zip", tmp_path / "data", tmp_path / "python.exe", 1234
        )
