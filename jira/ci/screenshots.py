#!/usr/bin/env python3
"""Capture screenshots with headless Chromium (Playwright).

Two modes, both intended to run inside the official Playwright container
(mcr.microsoft.com/playwright/python), which ships Chromium preinstalled:

    report --html FILE --out PNG
        Render a local HTML file (the CI report) to a full-page PNG.

    app --url URL --email E --password P [--project KEY] --out-dir DIR
        Log into a running Trackly instance and screenshot key screens
        (board, backlog, project insights, admin insights). Best-effort:
        a screen that can't be reached is skipped, not fatal.

Designed to never hard-fail the CI run — it prints what it captured and exits 0
unless given no work at all.
"""
from __future__ import annotations

import argparse
import sys

from playwright.sync_api import sync_playwright

VIEWPORT = {"width": 1440, "height": 900}


def shot_report(html_path: str, out_path: str) -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
        page.goto(f"file://{html_path}", wait_until="networkidle")
        page.screenshot(path=out_path, full_page=True)
        browser.close()
    print(f"captured report -> {out_path}")
    return 0


ACCESS_KEY = "trackly_access_token"
REFRESH_KEY = "trackly_refresh_token"


def _api_login(ctx, url: str, email: str, password: str) -> dict | None:
    """Authenticate via the API (form-encoded OAuth2 flow) and return tokens."""
    try:
        resp = ctx.request.post(f"{url}/api/auth/login", form={"username": email, "password": password})
        if not resp.ok:
            print(f"API login failed: HTTP {resp.status}")
            return None
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"API login error: {exc}")
        return None


def _goto_settle(page, target: str) -> None:
    """Navigate and wait for data/charts/animations to settle."""
    page.goto(target, wait_until="networkidle", timeout=20000)
    page.wait_for_timeout(1200)


def shot_app(url: str, email: str, password: str, project: str, out_dir: str) -> int:
    import os

    os.makedirs(out_dir, exist_ok=True)
    url = url.rstrip("/")
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        captured = 0

        # 1) The login screen (unauthenticated) — captured BEFORE seeding tokens.
        login_page = ctx.new_page()
        try:
            login_page.goto(url, wait_until="networkidle", timeout=15000)
            login_page.wait_for_timeout(1200)
            login_page.screenshot(path=f"{out_dir}/01-login.png", full_page=True)
            captured += 1
            print("captured login")
        except Exception as exc:  # noqa: BLE001
            print(f"skipped login: {exc}")
        login_page.close()

        # 2) Authenticate via API and seed localStorage so every page that the
        #    context opens afterwards loads already logged in (no UI race).
        tokens = _api_login(ctx, url, email, password)
        if not tokens:
            browser.close()
            return 0 if captured else 1
        ctx.add_init_script(
            f"localStorage.setItem('{ACCESS_KEY}', {tokens['access_token']!r});"
            f"localStorage.setItem('{REFRESH_KEY}', {tokens['refresh_token']!r});"
        )

        page = ctx.new_page()

        # 3) Full-page content routes (numbered 02..08 + admin 15..22).
        targets = [
            ("02-projects", f"{url}/projects"),
            ("03-board", f"{url}/projects/{project}/board"),
            ("04-backlog", f"{url}/projects/{project}/backlog"),
            ("05-project-insights", f"{url}/projects/{project}/insights"),
            ("06-issue-detail", f"{url}/browse/{project}-1"),
            ("07-search", f"{url}/search"),
            ("08-profile", f"{url}/profile"),
            ("15-admin-insights", f"{url}/admin/insights"),
            ("16-admin-auth", f"{url}/admin/auth"),
            ("17-admin-mail", f"{url}/admin/mail"),
            ("18-admin-jira-connections", f"{url}/admin/jira-connections"),
            ("19-admin-identity-providers", f"{url}/admin/identity-providers"),
            ("20-admin-groups", f"{url}/admin/groups"),
            ("21-admin-global-permissions", f"{url}/admin/global-permissions"),
            ("22-admin-permission-schemes", f"{url}/admin/permission-schemes"),
        ]
        for name, target in targets:
            try:
                _goto_settle(page, target)
                page.screenshot(path=f"{out_dir}/{name}.png", full_page=True)
                captured += 1
                print(f"captured {name}")
            except Exception as exc:  # noqa: BLE001
                print(f"skipped {name}: {exc}")

        # 4) Project settings — one page, each tab clicked then screenshotted.
        #    DOM text is lowercase (CSS capitalizes); match case-insensitively.
        settings_tabs = [
            ("09-settings-details", "details"),
            ("10-settings-people", "people"),
            ("11-settings-permissions", "permissions"),
            ("12-settings-components", "components"),
            ("13-settings-versions", "versions"),
            ("14-settings-jira-sync", "jira sync"),
        ]
        try:
            _goto_settle(page, f"{url}/projects/{project}/settings")
            for idx, (name, label) in enumerate(settings_tabs):
                try:
                    clicked = False
                    try:
                        page.click(
                            f"button.tab:has-text('{label}')", timeout=4000
                        )
                        clicked = True
                    except Exception:  # noqa: BLE001
                        # Fall back to clicking the tab by positional index.
                        tabs = page.locator("button.tab")
                        if tabs.count() > idx:
                            tabs.nth(idx).click(timeout=4000)
                            clicked = True
                    if not clicked:
                        print(f"skipped {name}: tab button not found")
                        continue
                    page.wait_for_timeout(800)
                    page.screenshot(path=f"{out_dir}/{name}.png", full_page=True)
                    captured += 1
                    print(f"captured {name}")
                except Exception as exc:  # noqa: BLE001
                    print(f"skipped {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"skipped settings tabs: {exc}")

        # 5) Modal / overlay shots (viewport, so the overlay is framed).
        # 23 - Create-issue modal from the board's top-bar Create button.
        try:
            _goto_settle(page, f"{url}/projects/{project}/board")
            page.click("button:has-text('Create')", timeout=5000)
            page.wait_for_timeout(1000)
            page.screenshot(path=f"{out_dir}/23-create-issue-modal.png")
            captured += 1
            print("captured 23-create-issue-modal")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        except Exception as exc:  # noqa: BLE001
            print(f"skipped 23-create-issue-modal: {exc}")

        # 24 - Notifications dropdown from the top-bar bell.
        try:
            bell = page.locator("button[aria-label*='notif' i]")
            if bell.count() == 0:
                bell = page.locator(
                    "button[title*='notif' i], button:has(svg[class*='bell' i]), "
                    "button[class*='notif' i]"
                )
            if bell.count() == 0:
                print("skipped 24-notifications: bell not found")
            else:
                bell.first.click(timeout=4000)
                page.wait_for_timeout(800)
                page.screenshot(path=f"{out_dir}/24-notifications.png")
                captured += 1
                print("captured 24-notifications")
        except Exception as exc:  # noqa: BLE001
            print(f"skipped 24-notifications: {exc}")

        browser.close()
    print(f"captured {captured} app screenshot(s) -> {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Trackly screenshot capture")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report")
    r.add_argument("--html", required=True)
    r.add_argument("--out", required=True)

    a = sub.add_parser("app")
    a.add_argument("--url", default="http://localhost:8080")
    a.add_argument("--email", default="admin@trackly.local")
    a.add_argument("--password", default="admin")
    a.add_argument("--project", default="DEMO")
    a.add_argument("--out-dir", required=True)

    args = p.parse_args(argv)
    if args.cmd == "report":
        return shot_report(args.html, args.out)
    return shot_app(args.url, args.email, args.password, args.project, args.out_dir)


if __name__ == "__main__":
    sys.exit(main())
