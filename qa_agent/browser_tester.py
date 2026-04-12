"""Playwright-driven smoke test of the web app under review."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import async_playwright

from qa_agent.oauth_helper import login


@dataclass
class BrowserReport:
    login_strategy: str = "anonymous"
    pages_visited: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    network_errors: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    passed: bool = True

    def to_markdown(self) -> str:
        lines = [
            f"**Login:** {self.login_strategy}",
            f"**Pages visited:** {', '.join(self.pages_visited) or 'none'}",
            f"**Passed:** {self.passed}",
        ]
        if self.assertions:
            lines.append("\n**Assertions:**")
            lines.extend(f"- {a}" for a in self.assertions)
        if self.console_errors:
            lines.append("\n**Console errors:**")
            lines.extend(f"- {e}" for e in self.console_errors)
        if self.network_errors:
            lines.append("\n**Network errors:**")
            lines.extend(f"- {e}" for e in self.network_errors)
        if self.screenshots:
            lines.append("\n**Screenshots:**")
            lines.extend(f"- {s}" for s in self.screenshots)
        return "\n".join(lines)


async def run_browser_smoke(base_url: str, artifacts_dir: Path) -> BrowserReport:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report = BrowserReport()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        page.on("console", lambda msg: (
            report.console_errors.append(msg.text) if msg.type == "error" else None
        ))
        page.on("pageerror", lambda err: report.console_errors.append(str(err)))
        page.on("requestfailed", lambda req: report.network_errors.append(
            f"{req.method} {req.url} — {req.failure}"
        ))

        try:
            report.login_strategy = await login(page, base_url)
            report.pages_visited.append(page.url)
            shot = artifacts_dir / "01_login.png"
            await page.screenshot(path=str(shot), full_page=True)
            report.screenshots.append(str(shot))

            await page.goto(base_url, wait_until="networkidle",
                            timeout=60_000)
            report.pages_visited.append(page.url)
            shot = artifacts_dir / "02_home.png"
            await page.screenshot(path=str(shot), full_page=True)
            report.screenshots.append(str(shot))

            title = await page.title()
            report.assertions.append(f"page title: {title!r}")

            # Gradio-specific checks
            gradio_app = page.locator("gradio-app, .gradio-container")
            if await gradio_app.count() > 0:
                report.assertions.append("OK: Gradio app container found")
            else:
                report.passed = False
                report.assertions.append(
                    "FAIL: no Gradio app container on page"
                )

            # Check that the project selector exists
            proj_sel = page.locator("#project-selector")
            if await proj_sel.count() > 0:
                report.assertions.append("OK: project selector present")

            # Check for visible pipeline step buttons
            step_btns = page.locator(
                "button:has-text('Run'), button:has-text('Prepare')"
            )
            btn_count = await step_btns.count()
            report.assertions.append(
                f"OK: {btn_count} pipeline step button(s) visible"
            )

            # Screenshot the Projects panel if it exists
            projects_el = page.locator("#project-manager")
            if await projects_el.count() > 0:
                shot = artifacts_dir / "03_projects.png"
                await page.screenshot(path=str(shot), full_page=True)
                report.screenshots.append(str(shot))

            if report.console_errors:
                report.passed = False
                report.assertions.append("FAIL: console errors observed")
        except Exception as exc:
            report.passed = False
            report.assertions.append(f"FAIL: browser exception — {exc}")
        finally:
            await context.close()
            await browser.close()

    return report
