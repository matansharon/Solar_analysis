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

LOGIN_URL = "https://monitoring.solaredge.com/solaredge-apigw/api/login"
DASHBOARD_URL = "https://monitoring.solaredge.com/solaredge-web/p/home"
COOKIE_TTL = 20 * 24 * 3600  # ~20 days (community-reported validity)

def harvest_cookie(username: str, password: str, session_store: SessionStore) -> str:
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
    # Pick the session cookie (name drifts during migration; prefer known candidates).
    wanted = ("SPRING_SECURITY_REMEMBER_ME_COOKIE", "JSESSIONID", "SolarEdge_Session")
    value = next((c["value"] for name in wanted for c in cookies if c["name"] == name), None)
    if not value and cookies:
        value = cookies[0]["value"]
    if not value:
        raise RuntimeError("se_login: no cookie captured")
    session_store.save("solaredge", {"cookie": value}, ttl_seconds=COOKIE_TTL)
    return value

def main():
    load_dotenv()
    u = os.environ["SOLAREDGE_USERNAME"]
    p = os.environ["SOLAREDGE_PASSWORD"]
    ss = SessionStore(".session_cache")
    harvest_cookie(u, p, ss)
    print("SolarEdge session cookie cached (~20 days).")

if __name__ == "__main__":
    main()
