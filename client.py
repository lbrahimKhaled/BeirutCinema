"""Terminal demo for VOX City Centre Beirut group checkout."""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError
from playwright.async_api import Error, async_playwright

import vox

ROOT = Path(__file__).resolve().parent
ZONE = ZoneInfo("Asia/Beirut")
CATEGORIES = {"find_showtimes": "Data", "select_seats": "Action",
              "prepare_checkout": "Orchestration"}
CHECKOUT_STEP_SELECTOR = "#offers.current, #extras.current, #details.current, #payment.current"


async def call(client, name, **arguments):
    print(f"[{CATEGORIES[name]}] {name}", flush=True)
    return (await client.call_tool(name, arguments)).structured_content


def show_listing(screenings):
    for number, show in enumerate(screenings, 1):
        start = datetime.fromisoformat(show["starts_at"])
        print(f"  {number:2}. {start:%H:%M}  {show['movie']} ({show['rating']}, {show['experience']})")


def choose(options, prompt):
    selection = int(input(prompt))
    if not 1 <= selection <= len(options):
        raise ValueError("Choose one of the numbers shown.")
    return options[selection - 1]


def read_handoff(directory, result):
    identifier = result["handoff_id"]
    if not re.fullmatch(r"checkout-[a-zA-Z0-9_-]+", identifier):
        raise ValueError("The checkout session could not be opened.")
    path = Path(directory) / identifier
    handoff = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    address = urlsplit(handoff["url"])
    if address.scheme != "https" or address.netloc != "lbn.voxcinemas.com":
        raise ValueError("The checkout link must belong to VOX Lebanon.")
    return handoff


async def launch_checkout_browser(playwright):
    # VOX can reset an HTTP/2 stream during checkout navigation. Use HTTPS
    # over HTTP/1.1 in this separate browser process, including user reloads.
    return await playwright.chromium.launch(
        channel="chrome", headless=False, args=["--disable-http2", "--disable-quic"],
    )


async def use_checkout_seat_editor(page, checkout_url):
    # Keep this basket's seat map and seat confirmation on the same HTTPS
    # transport as the MCP tool. Details and payment load directly in Chrome.
    basket = urlsplit(checkout_url).path.rsplit("/", 1)[0]
    editor = re.compile("^" + re.escape(vox.BASE + basket)
                        + r"/(?:seats(?:/fetch)?|allocate)(?:[?#].*)?$")

    async def load_editor(route):
        request = route.request
        if request.resource_type != "document":
            await route.continue_()
            return
        path = urlsplit(request.url).path
        if request.method == "GET" and not path.endswith("/allocate"):
            cookies = await page.context.cookies(request.url)
            document = await asyncio.to_thread(vox.seat_editor_document, request.url, cookies)
            if document:
                body, headers = document
                await route.fulfill(status=200, headers=headers, body=body)
                return
        elif request.method == "POST" and path.endswith("/allocate") and request.post_data:
            cookies = await page.context.cookies(request.url)
            target = await asyncio.to_thread(
                vox.confirm_seat_edit, request.url, cookies, request.post_data,
            )
            if not target:
                print("VOX could not confirm the seat change. Check the basket in Chrome.",
                      file=sys.stderr)
            await route.fulfill(
                status=303,
                headers={"location": target or checkout_url, "cache-control": "no-store"},
            )
            return
        await route.continue_()

    await page.route(editor, load_editor)


async def open_checkout(directory, result):
    handoff = read_handoff(directory, result)
    async with async_playwright() as playwright:
        try:
            browser = await launch_checkout_browser(playwright)
        except Error:
            await asyncio.to_thread(vox.abandon_handoff, handoff)
            raise ValueError("Chrome could not start. Install Google Chrome and try again.") from None
        try:
            context = await browser.new_context()
            await context.add_cookies(handoff["cookies"])
            page = await context.new_page()
            await use_checkout_seat_editor(page, handoff["url"])
            try:
                await page.goto(handoff["url"], wait_until="commit", timeout=20000)
                step = page.locator(CHECKOUT_STEP_SELECTOR)
                await step.first.wait_for(timeout=25000)
            except Error:
                print("The guest checkout tab is open. If VOX is still loading, reload that tab.",
                      file=sys.stderr)
            else:
                print("VOX checkout is open in Chrome. Complete your details and payment there.")
            print("Keep this terminal open while you use the guest checkout.")
            while browser.is_connected() and not page.is_closed():
                await asyncio.sleep(0.5)
        finally:
            await browser.close()


async def demo(client, directory, manual):
    tomorrow = datetime.now(ZONE).date() + timedelta(days=1)
    raw_day = input(f"Cinema date [{tomorrow:%Y-%m-%d}]: ").strip()
    day = date.fromisoformat(raw_day) if raw_day else tomorrow
    movie = input("Movie title or part of the title [any]: ").strip()
    party = int(input("How many people? [2]: ").strip() or 2)
    if not 1 <= party <= 10:
        raise ValueError("VOX supports 1 to 10 tickets per checkout.")
    earliest = time.fromisoformat(input("Earliest time [00:00]: ").strip() or "00:00")
    if earliest.tzinfo:
        raise ValueError("Enter a local Beirut time as HH:MM.")
    experience = input("Screening format [Standard]: ").strip() or "Standard"
    criteria = {"day": day.isoformat(), "movie": movie, "experience": experience}
    options = await call(client, "find_showtimes", **criteria)
    screenings = [show for show in options["screenings"]
                  if datetime.fromisoformat(show["starts_at"]).time() >= earliest]
    if not screenings:
        print("VOX listed no matching showtimes for that date and time.")
        return 1
    show_listing(screenings)
    candidates = [choose(screenings, "Select a showtime number: ")] if manual else screenings[:4]
    for show in candidates:
        try:
            details = await call(client, "find_showtimes", **criteria, showtime_id=show["id"],
                                 party_size=party)
            break
        except ToolError:
            if manual:
                raise
    else:
        print("VOX did not offer a guest seat map for the first matching screenings.")
        return 1
    if details.get("admission"):
        print(details["admission"])
    if manual:
        if not details["seats"]:
            print("VOX listed no adjacent group for this show.")
            return 1
        for index, labels in enumerate(details["seats"], 1):
            print(f"  {index}. {', '.join(labels)}")
        labels = choose(details["seats"], "Select a seat group number: ")
        tool = "select_seats"
        args = {"day": day.isoformat(), "showtime_id": show["id"], "seats": labels,
                "experience": experience}
    else:
        tool = "prepare_checkout"
        args = {**criteria, "movie": show["movie"], "party_size": party,
                "after": earliest.isoformat()}
    print(f"{show['movie']} is rated {details['rating']}.")
    if input("Does everyone meet VOX’s admission conditions? Hold seats now? [y/N]: ").strip().lower() != "y":
        return 0
    result = await call(client, tool, **args, acknowledged_rating=details["rating"])
    if result["status"] != "ready_for_checkout":
        print(result["detail"])
        return 1
    selected = result["screening"]
    print(f"{selected['movie']} · {selected['starts_at']} · Seats {', '.join(result['seats'])}")
    if result["total"]:
        print(f"VOX basket total {result['total']}")
    await open_checkout(directory, result)
    return 0


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("auto", "manual", "tools", "find", "chat"), default="auto")
    parser.add_argument("--date", default=(datetime.now(ZONE).date() + timedelta(days=1)).isoformat())
    parser.add_argument("--movie", default="")
    options = parser.parse_args()
    with TemporaryDirectory(prefix="beirut-cinema-") as directory:
        if options.mode == "chat":
            from agent_client import chat

            return await chat(directory)
        env = {**os.environ, "VOX_HANDOFF_DIR": directory}
        transport = StdioTransport(sys.executable, [str(ROOT / "server.py")], env=env)
        async with Client(transport, timeout=125) as client:
            if options.mode == "tools":
                for tool in await client.list_tools():
                    print(f"{CATEGORIES[tool.name]:14} {tool.name}")
                return 0
            if options.mode == "find":
                result = await call(client, "find_showtimes", day=options.date, movie=options.movie)
                if result["screenings"]:
                    show_listing(result["screenings"])
                else:
                    print("VOX listed no matching showtimes.")
                return 0
            return await demo(client, directory, options.mode == "manual")


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (ToolError, ValueError, Error, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    except (EOFError, KeyboardInterrupt):
        raise SystemExit(130) from None
