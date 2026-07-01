"""One-time headed browser login for SolarEdge to harvest a session cookie.

Usage: python -m solaranalysis.tools.se_login
Reads SOLAREDGE_USERNAME / SOLAREDGE_PASSWORD from .env, opens a real browser,
completes the login (solve any captcha/OTP manually if prompted), and caches the
session cookie for ~20 days so the adapter can replay data calls with plain requests.
"""
from __future__ import annotations
import os
from dotenv import load_dotenv
from ..core.session_store import SessionStore

COOKIE_TTL = 20 * 24 * 3600  # ~20 days (community-reported validity)

def harvest_cookie(username: str, password: str, session_store: SessionStore) -> dict:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)  # headed: user can solve challenges
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("https://monitoring.solaredge.com/")
        # Fill the login form (field names per research: j_username/j_password).
        page.fill("input[name='j_username'], input[type='email']", username)
        page.fill("input[name='j_password'], input[type='password']", password)
        page.click("button[type='submit'], input[type='submit']")
        page.wait_for_url("**/solaredge-web/**", timeout=120000)  # allow manual challenge
        cookies = ctx.cookies()
        browser.close()
    if not cookies:
        raise RuntimeError("se_login: no cookie captured")
    cookie_dict = {c["name"]: c["value"] for c in cookies}
    session_store.save("solaredge", {"cookies": cookie_dict}, ttl_seconds=COOKIE_TTL)
    return cookie_dict

def main():
    load_dotenv()
    u = os.environ["SOLAREDGE_USERNAME"]
    p = os.environ["SOLAREDGE_PASSWORD"]
    ss = SessionStore(".session_cache")
    harvest_cookie(u, p, ss)
    print("SolarEdge session cookie cached (~20 days).")

if __name__ == "__main__":
    main()
