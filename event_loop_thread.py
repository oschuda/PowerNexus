"""
Dedicated asyncio Event-Loop Thread for Flask Integration
"""
from __future__ import annotations

import asyncio
import threading
import logging

logger = logging.getLogger(__name__)


class EventLoopThread:
    """
    Startet einen dedizierten asyncio-Event-Loop in einem Background-Thread.
    Flask bleibt synchron (WSGI), PolicyEngine läuft asynchron.
    """

    def __init__(self) -> None:
        self._loop:   asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    def start(self) -> asyncio.AbstractEventLoop:
        """Startet den Event-Loop-Thread und gibt den Loop zurück."""
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="asyncio-event-loop",
        )
        self._thread.start()
        self._started.wait(timeout=5.0)
        if self._loop is None:
            raise RuntimeError("Event-Loop-Thread konnte nicht gestartet werden")
        return self._loop

    def _run_loop(self) -> None:
        """Läuft in Background-Thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._started.set()
        logger.info("🔄 asyncio Event-Loop-Thread gestartet")
        self._loop.run_forever()

    def stop(self) -> None:
        """Stoppt den Event-Loop sauber."""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            logger.info("🛑 asyncio Event-Loop wird beendet...")

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """Gibt den laufenden Loop zurück."""
        if self._loop is None:
            raise RuntimeError("Event-Loop-Thread nicht gestartet")
        return self._loop
