"""The front end: served safely, nothing missing, and (when Playwright is installed) working in a real browser."""

import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jaunt.api import WEB, create_app

STATIC = WEB / "static"


@pytest.fixture
def client(engine):
    with TestClient(create_app(engine=engine)) as c:
        yield c


def test_home_page_is_served_with_a_strict_security_policy(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp


def test_page_has_no_inline_code_the_policy_would_block():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> found"
    assert "<style" not in html and " style=" not in html and not re.search(r"\son[a-z]+=", html)


def test_every_file_the_page_uses_exists_and_has_the_right_type(client):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    paths = set(re.findall(r'(?:href|src)="(/static/[^"]+)"', html))
    paths |= {"/static/" + p for p in re.findall(r'url\("([^"/][^"]+)"\)', css)}
    paths |= set(re.findall(r'"(/static/[^"]+)"', js))
    expected = {".js": "javascript", ".css": "text/css", ".svg": "image/svg+xml", ".jpg": "image/jpeg",
                ".mp4": "video/mp4", ".webm": "video/webm", ".woff2": "font/woff2"}
    assert len(paths) >= 7
    for path in sorted(paths):
        r = client.get(path)
        assert r.status_code == 200, path
        assert expected[Path(path).suffix] in r.headers["content-type"], (path, r.headers["content-type"])


def test_script_never_inserts_html():
    """All text goes in with textContent, so a trip called <script> stays plain text."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert risky not in js, risky


def test_header_video_is_small_enough_for_a_home_page():
    for name, limit_kb in (("hero.mp4", 1500), ("hero.webm", 1500), ("hero.jpg", 300)):
        assert (STATIC / "video" / name).stat().st_size < limit_kb * 1024, name


# ---- In a real browser (skipped unless Playwright and its Chromium are installed) ----------------

@pytest.fixture
def jaunt_server(tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    from jaunt.__main__ import init_demo
    url = f"sqlite:///{tmp_path / 'browser.db'}"
    init_demo(url)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(url), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def browser_page():
    # On a laptop without Playwright this test is skipped. In CI it must run, so a missing browser fails it.
    required = os.environ.get("JAUNT_REQUIRE_BROWSER") == "1"
    if required:
        import playwright.sync_api as sync_api
    else:
        sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:                    # pragma: no cover - browser not downloaded
            if required:
                raise
            pytest.skip(f"Chromium isn't available for Playwright: {e}")
        pg = browser.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("dialog", lambda d: (pg.errors.append("alert: " + d.message), d.dismiss()))
        yield pg
        browser.close()


def test_in_a_browser_plan_hold_and_confirm(jaunt_server, browser_page):
    from playwright.sync_api import expect

    browser_page.goto(jaunt_server)
    expect(browser_page.get_by_role("heading", name="Go on, take a jaunt.")).to_be_visible()
    browser_page.goto(jaunt_server + "#/login")
    browser_page.get_by_role("button", name="Log in as Jo").click()
    browser_page.locator("a.tag", has_text="Bequia").click()

    day2 = browser_page.locator(".day").nth(1)
    day2.locator("summary").click()
    day2.locator("input").first.fill("<b>Snorkel</b>")
    day2.locator("input[type=time]").fill("08:30")
    day2.get_by_role("button", name="Add").click()
    expect(day2.locator(".item-title").first).to_have_text("<b>Snorkel</b>")       # shown as text

    # Holding a second stay for the same nights is fine, but it can't be confirmed over the first
    browser_page.get_by_role("button", name="Search").click()
    browser_page.locator(".option", has_text="Two-bed Villa").get_by_role("button", name="Hold for 15 minutes").click()
    villa = browser_page.locator(".booking.held", has_text="Two-bed Villa")
    expect(villa).to_contain_text("covered by Harbour Lights Inn")
    expect(villa.get_by_role("button", name="Confirm booking")).to_be_disabled()

    # A tour can be held and confirmed, and lands in the plan
    browser_page.get_by_role("tab", name="Tours").click()
    browser_page.get_by_role("button", name="Search").click()
    browser_page.locator(".option", has_text="Sunset Sail").get_by_role("button", name="Hold for 15 minutes").click()
    sail = browser_page.locator(".booking.held", has_text="Sunset Sail")
    expect(sail).to_contain_text("left")
    sail.get_by_role("button", name="Confirm booking").click()
    browser_page.locator("dialog#ask").get_by_role("button", name="Confirm booking").click()
    expect(browser_page.locator(".day").first.locator(".item-title")).to_contain_text(["Sunset Sail Around Bequia"])
    assert browser_page.errors == []
