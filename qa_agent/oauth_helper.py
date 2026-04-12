"""Login helpers for QA Playwright runs.

Strategies (tried in order):
  1. `basic_auth_login` — HTTP basic auth + Authentik OAuth (dev.magic-inspection.com)
  2. `mock_login` — hits the dev-only /test-login endpoint
  3. `google_login` / `github_login` — drives a real OAuth flow with a test account
"""
from __future__ import annotations

import base64
import os

from playwright.async_api import Page


async def basic_auth_login(page: Page, base_url: str) -> bool:
    """Log in through HTTP basic auth (Layer 1) + Authentik OAuth (Layer 2).

    For dev.magic-inspection.com: nginx requires basic auth, then
    oauth2-proxy checks for an Authentik session. We set the
    Authorization header for basic auth, then drive the Authentik
    login form if we're redirected to the sign-in page.
    """
    ba_user = os.environ.get("BASIC_AUTH_USER")
    ba_pass = os.environ.get("BASIC_AUTH_PASSWORD")
    if not ba_user or not ba_pass:
        return False

    # Set basic auth credentials on the browser context
    creds = base64.b64encode(f"{ba_user}:{ba_pass}".encode()).decode()
    await page.set_extra_http_headers({"Authorization": f"Basic {creds}"})

    await page.goto(base_url, wait_until="networkidle", timeout=30_000)

    # If oauth2-proxy redirected us to the Authentik sign-in page,
    # drive the login form with the test Google/Authentik account.
    if "/oauth2/sign_in" in page.url or "/if/flow/" in page.url:
        oauth_email = os.environ.get("TEST_GOOGLE_EMAIL")
        oauth_pass = os.environ.get("TEST_GOOGLE_PASSWORD")
        if not oauth_email or not oauth_pass:
            return False

        # Click the "Sign in" button on oauth2-proxy's page
        sign_in_btn = page.locator("button:has-text('Sign in'), a:has-text('Sign in')")
        if await sign_in_btn.count() > 0:
            await sign_in_btn.first.click()
            await page.wait_for_load_state("networkidle", timeout=15_000)

        # Authentik login form
        email_input = page.locator(
            "input[name='uidField'], input[name='username'], "
            "input[type='email']"
        )
        if await email_input.count() > 0:
            await email_input.first.fill(oauth_email)
            submit = page.locator(
                "button[type='submit'], input[type='submit']"
            )
            await submit.first.click()
            await page.wait_for_load_state("networkidle", timeout=10_000)

        password_input = page.locator(
            "input[name='password'], input[type='password']"
        )
        if await password_input.count() > 0:
            await password_input.first.fill(oauth_pass)
            submit = page.locator(
                "button[type='submit'], input[type='submit']"
            )
            await submit.first.click()
            await page.wait_for_load_state("networkidle", timeout=30_000)

    return True


async def mock_login(page: Page, base_url: str, user: str = "qa-test-user") -> bool:
    """Hit the TESTING_MODE bypass endpoint. Returns True if it worked."""
    if os.environ.get("TESTING_MODE", "").lower() != "true":
        return False
    try:
        await page.goto(f"{base_url}/test-login?user={user}", wait_until="networkidle")
        return True
    except Exception:
        return False


async def google_login(page: Page, base_url: str) -> bool:
    email = os.environ.get("TEST_GOOGLE_EMAIL")
    password = os.environ.get("TEST_GOOGLE_PASSWORD")
    if not email or not password:
        return False
    await page.goto(f"{base_url}/login")
    await page.click("button[data-provider='google']")
    await page.fill("input[type='email']", email)
    await page.click("#identifierNext")
    await page.wait_for_selector("input[type='password']", timeout=10_000)
    await page.fill("input[type='password']", password)
    await page.click("#passwordNext")
    await page.wait_for_url(f"{base_url}/**", timeout=30_000)
    return True


async def github_login(page: Page, base_url: str) -> bool:
    username = os.environ.get("TEST_GITHUB_USERNAME")
    password = os.environ.get("TEST_GITHUB_PASSWORD")
    if not username or not password:
        return False
    await page.goto(f"{base_url}/login")
    await page.click("button[data-provider='github']")
    await page.fill("input[name='login']", username)
    await page.fill("input[name='password']", password)
    await page.click("input[type='submit']")
    await page.wait_for_url(f"{base_url}/**", timeout=30_000)
    return True


async def login(page: Page, base_url: str) -> str:
    """Try the fastest strategy that is configured. Returns the strategy name."""
    if await basic_auth_login(page, base_url):
        return "basic_auth+oauth"
    if await mock_login(page, base_url):
        return "mock"
    if await google_login(page, base_url):
        return "google"
    if await github_login(page, base_url):
        return "github"
    return "anonymous"
