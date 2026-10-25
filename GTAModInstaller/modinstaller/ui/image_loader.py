from __future__ import annotations

import hashlib
from pathlib import Path

import requests
from PySide6.QtCore import QObject, QRect, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

from ..config import IMAGES_DIR
from ..site import USER_AGENT

_IMAGE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www.gta5-mods.com/",
    "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8",
}
_pool: QThreadPool | None = None


def _thread_pool() -> QThreadPool:
    global _pool
    if _pool is None:
        _pool = QThreadPool()
        _pool.setMaxThreadCount(4)
    return _pool


def _cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return IMAGES_DIR / f"{digest}.img"


def load_cached(url: str) -> QImage | None:
    if not url:
        return None
    path = _cache_path(url)
    if not path.is_file():
        return None
    image = QImage(str(path))
    return None if image.isNull() else image


class _Signals(QObject):
    loaded = Signal(str, QImage)


class _ImageJob(QRunnable):
    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url
        self.signals = _Signals()

    def run(self) -> None:
        path = _cache_path(self.url)
        image = QImage(str(path)) if path.is_file() else QImage()
        if image.isNull():
            try:
                response = requests.get(self.url, headers=_IMAGE_HEADERS, timeout=25)
                if response.status_code == 200 and response.content:
                    image = QImage()
                    image.loadFromData(response.content)
                    if not image.isNull():
                        try:
                            IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                            image.save(str(path), "JPG", 88)
                        except Exception:
                            pass
            except requests.exceptions.RequestException:
                image = QImage()
        if not image.isNull():
            self.signals.loaded.emit(self.url, image)


class AsyncCover(QWidget):
    clicked = Signal()

    def __init__(self, parent=None, height: int = 148) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._url = ""
        self._placeholder = "MOD"
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def set_placeholder(self, text: str) -> None:
        self._placeholder = (text or "MOD")[:12].upper()
        self.update()

    def set_url(self, url: str) -> None:
        self._url = url or ""
        cached = load_cached(self._url)
        if cached is not None:
            self._image = cached
            self.update()
            return
        if not self._url:
            return
        job = _ImageJob(self._url)
        job.signals.loaded.connect(self._on_loaded)
        _thread_pool().start(job)

    def _on_loaded(self, url: str, image: QImage) -> None:
        if url != self._url:
            return
        try:
            self._image = image
            self.update()
        except RuntimeError:
            pass

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = self.rect()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 13, 13)
        top_clip = QPainterPath()
        top_clip.addRect(rect.adjusted(0, 0, 0, -13))
        painter.setClipPath(clip.intersected(top_clip))
        if self._image is not None and not self._image.isNull():
            pixmap = QPixmap.fromImage(self._image).scaled(
                rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            x = max(0, (pixmap.width() - rect.width()) // 2)
            y = max(0, (pixmap.height() - rect.height()) // 2)
            source = QRect(x, y, rect.width(), rect.height()).intersected(pixmap.rect())
            painter.drawPixmap(rect, pixmap, source)
            overlay = QImage(rect.size(), QImage.Format_ARGB32_Premultiplied)
            overlay.fill(Qt.transparent)
            op = QPainter(overlay)
            op.setRenderHint(QPainter.Antialiasing)
            from PySide6.QtGui import QColor, QLinearGradient

            gradient = QLinearGradient(0, rect.height() * 0.35, 0, rect.height())
            gradient.setColorAt(0, QColor(10, 13, 10, 10))
            gradient.setColorAt(1, QColor(10, 13, 10, 205))
            op.fillRect(overlay.rect(), gradient)
            op.end()
            painter.drawImage(rect, overlay)
        else:
            from PySide6.QtGui import QColor, QLinearGradient

            gradient = QLinearGradient(0, 0, rect.width(), rect.height())
            gradient.setColorAt(0, QColor("#1B2918"))
            gradient.setColorAt(1, QColor("#101510"))
            painter.fillRect(rect, gradient)
            painter.setPen(QColor("#31422C"))
            font = self.font()
            font.setFamily("Bahnschrift")
            font.setPointSize(13)
            font.setBold(True)
            font.setLetterSpacing(font.SpacingType.AbsoluteSpacing, 2)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, self._placeholder)
        painter.end()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def prefetch_images(urls: list[str]) -> None:
    for url in urls:
        if not url or load_cached(url) is not None:
            continue
        job = _ImageJob(url)
        _thread_pool().start(job)
