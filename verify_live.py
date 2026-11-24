"""Run live MCP checkout verification with two temporary holds and release both."""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from playwright.async_api import async_playwright

import vox
from client import ROOT, ZONE, read_handoff


async def verify():
    with TemporaryDirectory(prefix="beirut-cinema-test-") as directory:
        env = {**os.environ, "VOX_HANDOFF_DIR": directory}
        transport = StdioTransport(sys.executable, [str(ROOT / "server.py")], env=env)
        async with Client(transport, timeout=125) as client, async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chrome", headless=False)
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
    for offset in (1, 0, 2, 3, 4):
        day = (now + timedelta(days=offset)).isoformat()
        result = await client.call_tool("find_showtimes", {"day": day})
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
        response = await page.goto(handoff["url"], wait_until="commit", timeout=25000)
        assert response and response.ok
        step = page.locator("#offers.current, #extras.current, #details.current, #payment.current")
        await step.first.wait_for(timeout=25000)
        heading = await step.first.locator("h2").first.inner_text()
        print(f"PASS {tool} · {', '.join(checkout['seats'])} · VOX {heading.strip()}", flush=True)
    finally:
        try:
            assert await asyncio.to_thread(vox.abandon_handoff, handoff), "VOX could not release the test hold."
            print("PASS temporary hold released", flush=True)
        finally:
            await context.close()


if __name__ == "__main__":
    if sys.argv[1:] != ["--hold-and-release"]:
        raise SystemExit("Run with --hold-and-release to authorize two temporary live VOX holds.")
    asyncio.run(verify())
