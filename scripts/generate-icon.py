"""Render the source SVG into Windows and documentation icon formats."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "qi_flow" / "assets" / "qiflow-icon.svg"
TARGET = ROOT / "src" / "qi_flow" / "assets"


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(str(SOURCE)).render(painter)
    painter.end()
    return image


def main() -> None:
    sizes = (16, 32, 48, 64, 128, 256)
    pngs: list[tuple[int, bytes]] = []
    for size in sizes:
        image = render(size)
        if not image.save(str(TARGET / f"qiflow-icon-{size}.png")):
            raise RuntimeError(f"Could not create {size}px PNG icon")
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        pngs.append((size, bytes(data)))
    (TARGET / "qiflow-icon.png").write_bytes(dict(pngs)[256])
    offset = 6 + 16 * len(pngs)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(pngs)))
    payload = bytearray()
    for size, data in pngs:
        directory.extend(
            struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        )
        payload.extend(data)
        offset += len(data)
    (TARGET / "qiflow-icon.ico").write_bytes(directory + payload)


if __name__ == "__main__":
    main()
