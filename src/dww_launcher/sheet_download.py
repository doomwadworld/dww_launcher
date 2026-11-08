"""Background download of the spreadsheet CSVs using Qt's network stack.

Qt networking is used (instead of urllib) so downloads never block the UI and
HTTPS certificates come from the operating system, which avoids certificate
problems in bundled Python apps on macOS.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from dww_launcher import __version__
from dww_launcher.sheets import SHEETS, validate_csv

TIMEOUT_MS = 60_000


class SheetDownloader(QObject):
    """Download every sheet; emit ``finished(texts, errors)`` once all requests complete.

    ``texts`` maps sheet key -> CSV text for successful downloads. ``errors`` is a list of
    human-readable messages (empty when everything succeeded).
    """

    finished = Signal(dict, list)

    def __init__(self, urls: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self.urls = urls or {key: s.url for key, s in SHEETS.items()}
        self._manager = QNetworkAccessManager(self)
        self._pending: dict[QNetworkReply, str] = {}
        self._texts: dict[str, str] = {}
        self._errors: list[str] = []

    @property
    def running(self) -> bool:
        return bool(self._pending)

    def start(self) -> None:
        if self.running:
            return
        self._texts, self._errors = {}, []
        for key, url in self.urls.items():
            request = QNetworkRequest(QUrl(url))
            request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader,
                              f"DWW-Launcher/{__version__}")
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                 QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
            request.setTransferTimeout(TIMEOUT_MS)
            reply = self._manager.get(request)
            self._pending[reply] = key
            reply.finished.connect(lambda r=reply: self._on_reply(r))

    def _on_reply(self, reply: QNetworkReply) -> None:
        key = self._pending.pop(reply)
        title = SHEETS[key].title if key in SHEETS else key
        if reply.error() != QNetworkReply.NetworkError.NoError:
            self._errors.append(f"{title}: {reply.errorString()}")
        else:
            text = bytes(reply.readAll()).decode("utf-8-sig", errors="replace")
            try:
                if key in SHEETS:
                    validate_csv(text, SHEETS[key])
            except ValueError as exc:
                self._errors.append(str(exc))
            else:
                self._texts[key] = text
        reply.deleteLater()
        if not self._pending:
            self.finished.emit(self._texts, self._errors)
