"""Stage the verified updater in user storage; launch belongs to the composition root."""

import shutil
from pathlib import Path

from qi_flow.application.desktop import AvailableUpdate, RestartCommand, UpdateError


def prepare_update(
    update: AvailableUpdate, archive: Path, data_dir: Path, executable: Path, parent_pid: int
) -> RestartCommand:
    install_dir = executable.resolve().parent
    bundled = install_dir / "QI Flow Updater.exe"
    if not bundled.is_file() or install_dir.name.casefold() != "qi flow":
        raise UpdateError("In-app updates are available from an installed Windows build only.")
    if not archive.is_file():
        raise UpdateError("The verified update package is no longer available; download it again.")
    staging = data_dir / "updates"
    helper = staging / "QI Flow Updater.exe"
    try:
        staging.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, helper)
    except OSError as error:
        raise UpdateError(
            "The update helper could not be staged in your user data folder."
        ) from error
    return RestartCommand(
        str(helper),
        (
            "--pid",
            str(parent_pid),
            "--archive",
            str(archive),
            "--install-dir",
            str(install_dir),
            "--sha256",
            update.sha256,
        ),
        str(staging),
    )
