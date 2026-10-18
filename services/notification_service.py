"""Windows toast notifications with a silent fallback."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def notify(title: str, message: str, enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        from winotify import Notification  # Windows-only, optional
        Notification(app_id="SmartForm AI", title=title, msg=message[:250], duration="long").show()
    except Exception as exc:
        log.info("Notification unavailable: %s", exc.__class__.__name__)
