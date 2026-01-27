"""Playwright client wrapper for browser automation."""

from __future__ import annotations

import base64
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse


class PlaywrightClient:
    """Client for browser automation using Playwright."""

    def __init__(
        self,
        headless: bool = True,
        browser_type: str = "chromium",
        timeout_ms: int = 30000,
        screenshot_dir: str = "/tmp/playwright",
    ):
        self.headless = headless
        self.browser_type = browser_type
        self.timeout_ms = timeout_ms
        self.screenshot_dir = screenshot_dir
        self._browser = None
        self._context = None
        self._page = None

        # Ensure screenshot directory exists
        Path(self.screenshot_dir).mkdir(parents=True, exist_ok=True)

    async def _ensure_browser(self) -> None:
        """Ensure browser is launched."""
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()

            if self.browser_type == "firefox":
                self._browser = await self._playwright.firefox.launch(headless=self.headless)
            elif self.browser_type == "webkit":
                self._browser = await self._playwright.webkit.launch(headless=self.headless)
            else:
                self._browser = await self._playwright.chromium.launch(headless=self.headless)

            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            self._page = await self._context.new_page()
            self._page.set_default_timeout(self.timeout_ms)

    def _safe_url(self, url: str) -> str:
        """Ensure URL has a scheme."""
        if not url.startswith(("http://", "https://")):
            return f"https://{url}"
        return url

    async def health_check(self) -> Dict[str, Any]:
        """Check Playwright availability."""
        try:
            from playwright.async_api import async_playwright
            return {
                "ok": True,
                "playwright_available": True,
                "browser_type": self.browser_type,
                "headless": self.headless,
            }
        except ImportError as e:
            return {
                "ok": False,
                "error": f"Playwright not installed: {e}",
            }

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Dict[str, Any]:
        """Navigate to a URL.

        Args:
            url: URL to navigate to
            wait_until: When to consider navigation finished (load, domcontentloaded, networkidle)

        Returns:
            Navigation result with page info
        """
        try:
            await self._ensure_browser()
            safe_url = self._safe_url(url)
            response = await self._page.goto(safe_url, wait_until=wait_until)

            return {
                "ok": True,
                "url": self._page.url,
                "title": await self._page.title(),
                "status": response.status if response else None,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_page_content(self, selector: Optional[str] = None) -> Dict[str, Any]:
        """Get page content or specific element content.

        Args:
            selector: Optional CSS selector to get specific element

        Returns:
            Page content
        """
        try:
            await self._ensure_browser()

            if selector:
                element = await self._page.query_selector(selector)
                if element:
                    return {
                        "ok": True,
                        "content": await element.inner_text(),
                        "html": (await element.inner_html())[:5000],
                        "selector": selector,
                    }
                return {"ok": False, "error": f"Element not found: {selector}"}

            return {
                "ok": True,
                "url": self._page.url,
                "title": await self._page.title(),
                "content": (await self._page.inner_text("body"))[:10000],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def screenshot(
        self,
        full_page: bool = False,
        selector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Take a screenshot.

        Args:
            full_page: Capture full scrollable page
            selector: Capture specific element

        Returns:
            Screenshot info with file path
        """
        try:
            await self._ensure_browser()

            filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.png"
            filepath = os.path.join(self.screenshot_dir, filename)

            if selector:
                element = await self._page.query_selector(selector)
                if element:
                    await element.screenshot(path=filepath)
                else:
                    return {"ok": False, "error": f"Element not found: {selector}"}
            else:
                await self._page.screenshot(path=filepath, full_page=full_page)

            # Read and encode for preview
            with open(filepath, "rb") as f:
                img_data = base64.b64encode(f.read()).decode()

            return {
                "ok": True,
                "filepath": filepath,
                "filename": filename,
                "url": self._page.url,
                "full_page": full_page,
                "base64_preview": img_data[:1000] + "..." if len(img_data) > 1000 else img_data,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def click(self, selector: str) -> Dict[str, Any]:
        """Click an element.

        Args:
            selector: CSS selector for element to click

        Returns:
            Click result
        """
        try:
            await self._ensure_browser()
            await self._page.click(selector)
            return {
                "ok": True,
                "clicked": selector,
                "url": self._page.url,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def fill(self, selector: str, value: str) -> Dict[str, Any]:
        """Fill an input field.

        Args:
            selector: CSS selector for input
            value: Value to fill

        Returns:
            Fill result
        """
        try:
            await self._ensure_browser()
            await self._page.fill(selector, value)
            return {
                "ok": True,
                "selector": selector,
                "filled": True,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def type_text(self, selector: str, text: str, delay: int = 50) -> Dict[str, Any]:
        """Type text character by character.

        Args:
            selector: CSS selector for input
            text: Text to type
            delay: Delay between keystrokes in ms

        Returns:
            Type result
        """
        try:
            await self._ensure_browser()
            await self._page.type(selector, text, delay=delay)
            return {
                "ok": True,
                "selector": selector,
                "typed": True,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def press_key(self, key: str, selector: Optional[str] = None) -> Dict[str, Any]:
        """Press a keyboard key.

        Args:
            key: Key to press (e.g., Enter, Tab, Escape)
            selector: Optional element to focus first

        Returns:
            Key press result
        """
        try:
            await self._ensure_browser()
            if selector:
                await self._page.press(selector, key)
            else:
                await self._page.keyboard.press(key)
            return {
                "ok": True,
                "key": key,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def wait_for_selector(self, selector: str, state: str = "visible") -> Dict[str, Any]:
        """Wait for an element.

        Args:
            selector: CSS selector
            state: State to wait for (attached, detached, visible, hidden)

        Returns:
            Wait result
        """
        try:
            await self._ensure_browser()
            await self._page.wait_for_selector(selector, state=state)
            return {
                "ok": True,
                "selector": selector,
                "state": state,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_links(self, selector: str = "a") -> Dict[str, Any]:
        """Get all links on the page.

        Args:
            selector: CSS selector for link elements

        Returns:
            List of links
        """
        try:
            await self._ensure_browser()
            links = await self._page.eval_on_selector_all(
                selector,
                """elements => elements.map(e => ({
                    text: e.innerText.trim().substring(0, 100),
                    href: e.href,
                })).filter(l => l.href)"""
            )
            return {
                "ok": True,
                "count": len(links),
                "links": links[:50],  # Limit to 50
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_forms(self) -> Dict[str, Any]:
        """Get all forms on the page.

        Returns:
            List of forms with their inputs
        """
        try:
            await self._ensure_browser()
            forms = await self._page.eval_on_selector_all(
                "form",
                """forms => forms.map((f, i) => ({
                    index: i,
                    action: f.action,
                    method: f.method,
                    inputs: Array.from(f.querySelectorAll('input, select, textarea')).map(inp => ({
                        type: inp.type || inp.tagName.toLowerCase(),
                        name: inp.name,
                        id: inp.id,
                        placeholder: inp.placeholder,
                    }))
                }))"""
            )
            return {
                "ok": True,
                "count": len(forms),
                "forms": forms,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def evaluate(self, script: str) -> Dict[str, Any]:
        """Execute JavaScript on the page.

        Args:
            script: JavaScript code to execute

        Returns:
            Script result
        """
        try:
            await self._ensure_browser()
            result = await self._page.evaluate(script)
            return {
                "ok": True,
                "result": str(result)[:5000] if result else None,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_page_info(self) -> Dict[str, Any]:
        """Get current page information.

        Returns:
            Page URL, title, and metadata
        """
        try:
            await self._ensure_browser()
            return {
                "ok": True,
                "url": self._page.url,
                "title": await self._page.title(),
                "viewport": self._page.viewport_size,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def scroll(self, direction: str = "down", amount: int = 500) -> Dict[str, Any]:
        """Scroll the page.

        Args:
            direction: Scroll direction (up, down)
            amount: Pixels to scroll

        Returns:
            Scroll result
        """
        try:
            await self._ensure_browser()
            if direction == "up":
                amount = -abs(amount)
            else:
                amount = abs(amount)
            await self._page.evaluate(f"window.scrollBy(0, {amount})")
            return {
                "ok": True,
                "scrolled": amount,
                "direction": direction,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def go_back(self) -> Dict[str, Any]:
        """Go back in browser history.

        Returns:
            Navigation result
        """
        try:
            await self._ensure_browser()
            await self._page.go_back()
            return {
                "ok": True,
                "url": self._page.url,
                "title": await self._page.title(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def go_forward(self) -> Dict[str, Any]:
        """Go forward in browser history.

        Returns:
            Navigation result
        """
        try:
            await self._ensure_browser()
            await self._page.go_forward()
            return {
                "ok": True,
                "url": self._page.url,
                "title": await self._page.title(),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def close(self) -> Dict[str, Any]:
        """Close the browser.

        Returns:
            Close result
        """
        try:
            if self._browser:
                await self._browser.close()
                await self._playwright.stop()
                self._browser = None
                self._context = None
                self._page = None
            return {"ok": True, "closed": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}
