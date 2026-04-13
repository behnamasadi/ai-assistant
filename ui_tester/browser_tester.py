"""Playwright-driven UI smoke test for the web app under review."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import async_playwright

from ui_tester.oauth_helper import login


@dataclass
class BrowserReport:
    login_strategy: str = "anonymous"
    pages_visited: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    network_errors: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    health_score: int = 100
    passed: bool = True

    def to_markdown(self) -> str:
        lines = [
            f"**Login:** {self.login_strategy}",
            f"**Health score:** {self.health_score}/100",
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
    """Run systematic page-by-page smoke test of the Gradio app."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report = BrowserReport()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        page.on("console", lambda msg: (
            report.console_errors.append(msg.text) if msg.type == "error" else None
        ))
        page.on("pageerror", lambda err: report.console_errors.append(str(err)))
        page.on("requestfailed", lambda req: report.network_errors.append(
            f"{req.method} {req.url} — {req.failure}"
        ))

        try:
            # --- Login ---
            report.login_strategy = await login(page, base_url)
            report.pages_visited.append(page.url)
            shot = artifacts_dir / "01_login.png"
            await page.screenshot(path=str(shot), full_page=True)
            report.screenshots.append(str(shot))

            # --- Home / Main page ---
            await page.goto(base_url, wait_until="networkidle", timeout=60_000)
            report.pages_visited.append(page.url)
            shot = artifacts_dir / "02_home.png"
            await page.screenshot(path=str(shot), full_page=True)
            report.screenshots.append(str(shot))

            title = await page.title()
            report.assertions.append(f"page title: {title!r}")

            # --- Gradio container check ---
            gradio_app = page.locator("gradio-app, .gradio-container")
            if await gradio_app.count() > 0:
                report.assertions.append("OK: Gradio app container found")
            else:
                report.passed = False
                report.health_score -= 30
                report.assertions.append("FAIL: no Gradio app container on page")

            # --- Project selector ---
            proj_sel = page.locator("#project-selector")
            if await proj_sel.count() > 0:
                report.assertions.append("OK: project selector present")
            else:
                report.health_score -= 10
                report.assertions.append("WARN: project selector not found")

            # --- Pipeline step buttons ---
            step_btns = page.locator(
                "button:has-text('Run'), button:has-text('Prepare')"
            )
            btn_count = await step_btns.count()
            report.assertions.append(
                f"OK: {btn_count} pipeline step button(s) visible"
            )
            if btn_count == 0:
                report.health_score -= 15

            # --- Tab navigation ---
            tabs = page.locator(".tab-nav button, [role='tab']")
            tab_count = await tabs.count()
            report.assertions.append(f"OK: {tab_count} tab(s) found")

            # Screenshot each visible tab
            for i in range(min(tab_count, 6)):
                try:
                    tab = tabs.nth(i)
                    tab_text = (await tab.text_content() or f"tab_{i}").strip()
                    await tab.click()
                    await page.wait_for_timeout(1000)
                    shot = artifacts_dir / f"03_tab_{i}_{tab_text[:20]}.png"
                    await page.screenshot(path=str(shot), full_page=True)
                    report.screenshots.append(str(shot))
                    report.pages_visited.append(f"tab:{tab_text}")
                except Exception as exc:
                    report.assertions.append(f"WARN: tab {i} click failed — {exc}")

            # --- Projects panel ---
            projects_el = page.locator("#project-manager")
            if await projects_el.count() > 0:
                shot = artifacts_dir / "04_projects.png"
                await page.screenshot(path=str(shot), full_page=True)
                report.screenshots.append(str(shot))

            # --- Console errors deduction ---
            if report.console_errors:
                report.passed = False
                report.health_score -= min(5 * len(report.console_errors), 25)
                report.assertions.append(
                    f"FAIL: {len(report.console_errors)} console error(s) observed"
                )

            # --- Network errors deduction ---
            if report.network_errors:
                report.health_score -= min(5 * len(report.network_errors), 15)
                report.assertions.append(
                    f"WARN: {len(report.network_errors)} network error(s) observed"
                )

            report.health_score = max(0, report.health_score)
        except Exception as exc:
            report.passed = False
            report.health_score = 0
            report.assertions.append(f"FAIL: browser exception — {exc}")
        finally:
            await context.close()
            await browser.close()

    return report
