"""Playwright <-> existing Brave (CDP). All Playwright calls run on ONE thread."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import requests

from .form_filler import fill_all
from .form_inspector import FormAccessError, InspectionResult, inspect_form
from .models import AnswerPlan, FillResult, OperationCancelled, Question

log = logging.getLogger(__name__)

BRAVE_HELP = (
    "Brave is not reachable on the debugging port.\n"
    "Close ALL Brave windows, then start it with:\n"
    '  & "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe" '
    "--remote-debugging-port=9222\n"
    "(add --user-data-dir only if you already use a dedicated profile), then try again."
)


class BrowserError(Exception):
    pass


class BrowserService:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")
        self._pw = self._browser = self._page = None
        self._page_url = ""

    # Safe to call from any thread (plain HTTP, no Playwright objects).
    def check_connection(self) -> tuple[bool, str]:
        try:
            r = requests.get(f"{self.config.get('cdp_url', 'http://127.0.0.1:9222').rstrip('/')}/json/version", timeout=2)
            r.raise_for_status()
            return True, r.json().get("Browser", "Connected")
        except Exception:
            return False, "Not reachable on debugging port"

    def _run(self, fn: Callable, *args: Any) -> Any:
        return self._exec.submit(fn, *args).result()

    def _ensure_connected(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        from playwright.sync_api import sync_playwright  # lazy import
        if self._pw is None:
            self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(self.config.get("cdp_url", "http://127.0.0.1:9222"))
        except Exception as exc:
            raise BrowserError(BRAVE_HELP) from exc
        if not self._browser.contexts:
            raise BrowserError("Brave has no browser context. Open a normal Brave window first.")

    def _page_alive(self) -> bool:
        try:
            return self._page is not None and not self._page.is_closed()
        except Exception:
            return False

    def _open_and_inspect(self, url: str, cancel: threading.Event) -> InspectionResult:
        self._ensure_connected()
        if cancel.is_set():
            raise OperationCancelled()
        if self._page_alive() and self._page_url == url:
            self._page.bring_to_front()  # keep the user's progress on the same form
        else:
            self._page = self._browser.contexts[0].new_page()
            self._page_url = url
            self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            return inspect_form(self._page, int(self.config.get("form_ready_timeout_seconds", 30)))
        except FormAccessError:
            raise
        except Exception as exc:
            raise BrowserError(f"Browser error while reading the form: {exc.__class__.__name__}") from exc

    def _fill(self, questions: list[Question], plans: dict[int, AnswerPlan], cancel: threading.Event,
              on_result: Callable[[FillResult], None]) -> list[FillResult]:
        if not self._page_alive():
            raise BrowserError("The form tab was closed. Analyze the form again.")
        self._page.bring_to_front()
        return fill_all(self._page, questions, plans, self.config, cancel, on_result)

    def _close(self) -> None:
        try:
            if self._pw:
                self._pw.stop()  # disconnects only; Brave and its tabs stay open
        finally:
            self._pw = self._browser = self._page = None

    # ---- public API (thread-safe) ----
    def open_and_inspect(self, url: str, cancel: threading.Event) -> InspectionResult:
        return self._run(self._open_and_inspect, url, cancel)

    def fill(self, questions, plans, cancel, on_result) -> list[FillResult]:
        return self._run(self._fill, questions, plans, cancel, on_result)

    def shutdown(self) -> None:
        try:
            self._run(self._close)
        finally:
            self._exec.shutdown(wait=False)
