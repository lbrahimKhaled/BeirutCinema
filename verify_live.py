"""Run live MCP checkout verification with two temporary holds and release both."""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory

from bs4 import BeautifulSoup
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from playwright.async_api import async_playwright

import vox
from client import (
    CHECKOUT_STEP_SELECTOR, ROOT, ZONE, launch_checkout_browser, read_handoff,
    use_checkout_seat_editor,
)


async def verify():
    with TemporaryDirectory(prefix="beirut-cinema-test-") as directory:
        env = {**os.environ, "VOX_HANDOFF_DIR": directory}
        transport = StdioTransport(sys.executable, [str(ROOT / "server.py")], env=env)
        async with Client(transport, timeout=125) as client, async_playwright() as playwright:
            browser = await launch_checkout_browser(playwright)
            try:
                tools = {tool.name for tool in await client.list_tools()}
                assert tools == {"find_showtimes", "select_seats", "prepare_checkout"}
                print("PASS exactly three MCP tools over stdio", flush=True)
                target, seats, rating = await find_preview(client)
                print(f"PASS live showtimes and seat preview for {target['movie']}", flush=True)
                day = target["starts_at"][:10]
                exact = {"day": day, "showtime_id": target["id"], "seats": seats,
                         "acknowledged_rating": rating, "experience": target["experience"]}
                invalid = await client.call_tool("select_seats", {**exact, "seats": ["ZZ-999"]})
                assert invalid.structured_content["status"] == "unavailable"
                assert not os.listdir(directory)
                print("PASS unavailable selection creates no checkout", flush=True)

                await confirm_checkout(client, browser, directory, "select_seats", exact, expected=seats)
                await confirm_checkout(client, browser, directory, "prepare_checkout", {
                    "day": day, "movie": target["movie"], "party_size": len(seats),
                    "acknowledged_rating": rating, "experience": target["experience"],
                })
            finally:
                await browser.close()


async def find_preview(client):
    now = datetime.now(ZONE).date()
    for offset in (0, 1, 2, 3, 4):
        day = (now + timedelta(days=offset)).isoformat()
        result = await client.call_tool("find_showtimes", {"day": day, "experience": "Standard"})
        candidates = result.structured_content["screenings"]
        # Prefer general-admission titles for the temporary test.
        candidates = sorted(candidates, key=lambda item: item["rating"] not in {"G", "PG"})
        for show in candidates[:4]:
            preview = await client.call_tool("find_showtimes", {
                "day": day, "showtime_id": show["id"], "party_size": 2,
            }, raise_on_error=False)
            data = preview.structured_content or {}
            if data.get("seats") and data.get("rating"):
                return show, data["seats"][0], data["rating"]
    raise RuntimeError("VOX did not offer an adjacent pair in the sampled screenings.")


async def confirm_checkout(client, browser, directory, tool, arguments, expected=None):
    result = await client.call_tool(tool, arguments)
    checkout = result.structured_content
    assert checkout["status"] == "ready_for_checkout", checkout["detail"]
    handoff = read_handoff(directory, checkout)
    context = await browser.new_context()
    try:
        assert len(checkout["seats"]) == 2
        if expected:
            assert set(checkout["seats"]) == set(expected)
        assert checkout["total"]
        await context.add_cookies(handoff["cookies"])
        page = await context.new_page()
        await use_checkout_seat_editor(page, handoff["url"])
        response = await page.goto(handoff["url"], wait_until="commit", timeout=25000)
        assert response and response.ok
        step = page.locator(CHECKOUT_STEP_SELECTOR)
        await step.first.wait_for(timeout=25000)
        heading = await step.first.locator("h2").first.inner_text()
        print(f"PASS {tool} · {', '.join(checkout['seats'])} · VOX {heading.strip()}", flush=True)
        for _ in range(2):
            refreshed = await page.reload(wait_until="commit", timeout=25000)
            assert refreshed and refreshed.ok
            await step.first.wait_for(timeout=20000)
        print("PASS checkout survives two browser reloads", flush=True)
        if expected:
            await confirm_seat_change(page, checkout["seats"])
    finally:
        try:
            assert await asyncio.to_thread(vox.abandon_handoff, handoff), "VOX could not release the test hold."
            print("PASS temporary hold released", flush=True)
        finally:
            await context.close()



async def confirm_seat_change(page, original):
    await page.locator("#seats.summary a.edit").click(timeout=15000)
    form = page.locator("#seats.current form#seat-selection")
    await form.wait_for(state="attached", timeout=40000)
    await form.locator(".preload").wait_for(timeout=15000)
    print("PASS Edit opens VOX’s seat map", flush=True)

    markup = await form.evaluate("(form) => form.outerHTML")
    rows = vox.parse_rows(BeautifulSoup(markup, "html.parser").form)
    alternatives = vox.adjacent_groups(rows, len(original))
    replacement = next((group for group in alternatives
                        if all(seat.label not in original for seat in group)), None)
    assert replacement, "The current screening has no different adjacent group to test."

    # This is an opted-in test checkout for a group that meets the displayed rating.
    agree = page.get_by_text("I Agree", exact=True)
    for button in await agree.all():
        if await button.is_visible():
            await button.click(timeout=5000)
            break
    checked = form.locator('input[name="seat"]:checked')
    previous = await checked.evaluate_all("(inputs) => inputs.map(input => input.dataset.label)")
    for label in [*previous, *(seat.label for seat in replacement)]:
        await form.locator(f"label[title={json.dumps(label)}]").click(timeout=7000)
    selected = await checked.evaluate_all("(inputs) => inputs.map(input => input.dataset.label)")
    expected = {seat.label for seat in replacement}
    assert set(selected) == expected
    consent = page.get_by_role("dialog", name="cookieconsent")
    if await consent.is_visible():
        dismiss = consent.locator(".cc-deny, .cc-dismiss").first
        if not await dismiss.is_visible():
            dismiss = consent.get_by_text(re.compile(r"reject|decline|necessary only", re.I)).first
        if not await dismiss.is_visible():
            dismiss = consent.get_by_text("Accept All Cookies", exact=True)
        # This setting is confined to the isolated, temporary test profile.
        await dismiss.click(timeout=7000)
    await page.locator('button[form="seat-selection"]').click(timeout=10000)
    await page.locator(CHECKOUT_STEP_SELECTOR).first.wait_for(timeout=25000)
    await page.reload(wait_until="commit", timeout=25000)
    await page.locator(CHECKOUT_STEP_SELECTOR).first.wait_for(timeout=20000)
    summary = await page.locator("#seats.summary").inner_text()
    assert all(re.search(r"(?<![\w-])" + re.escape(label) + r"(?![\w-])", summary)
               for label in expected), summary
    print("PASS changed seats remain selected after reload · " + ", ".join(sorted(expected)), flush=True)


if __name__ == "__main__":
    if sys.argv[1:] != ["--hold-and-release"]:
        raise SystemExit("Run with --hold-and-release to authorize two temporary live VOX holds.")
    asyncio.run(verify())
