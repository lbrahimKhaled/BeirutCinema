"""Natural-language host for the same VOX MCP server."""

import os
import sys
from datetime import datetime

from agents import Agent, Runner, SQLiteSession, set_tracing_disabled
from agents.mcp import MCPServerStdio
from dotenv import load_dotenv
from mcp.types import CallToolResult, TextContent

from client import CATEGORIES, ROOT, ZONE, open_checkout

INSTRUCTIONS = """You help guests choose seats together at VOX City Centre Beirut.
Today is {today} in Asia/Beirut. Ask for the day, group size, movie or permission to pick,
and any time or format preference. Default to Standard if the user has no preference.
Use find_showtimes to return current screenings. For a specific screening call
find_showtimes again with its showtime_id and party_size to get live seat groups and
admission conditions. Use select_seats when the guest chooses an exact group. Use
prepare_checkout when the guest asks you to pick a showing and seats for them.
Before either hold tool, preview that showing with find_showtimes, and pass the
returned rating. The terminal separately asks the guest to authorize the live hold.
If they decline, end the turn. Never state that tickets are purchased. Report the actual
selected movie, time, seats and basket total when given. The host opens VOX checkout for
the guest to enter their own details and pay. Do not ask for card or contact details.
Never reveal a checkout session identifier. Keep the conversation short."""


class CinemaServer(MCPServerStdio):
    def __init__(self, directory):
        super().__init__(
            name="beirut-cinema",
            params={"command": sys.executable, "args": [str(ROOT / "server.py")],
                    "env": {"VOX_HANDOFF_DIR": directory}},
            client_session_timeout_seconds=125,
            cache_tools_list=True,
        )
        self.previews = {}
        self.checkout = None

    @staticmethod
    def decline(message):
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=message)])

    async def call_tool(self, tool_name, arguments, meta=None):
        args = arguments or {}
        print(f"[{CATEGORIES.get(tool_name, 'MCP')}] {tool_name}", flush=True)
        if tool_name in {"select_seats", "prepare_checkout"}:
            if self.checkout is not None:
                return self.decline("A checkout is already prepared for this request.")
            preview = self.previews.get(args.get("showtime_id"))
            if tool_name == "prepare_checkout":
                preview = next((item for item in self.previews.values()
                                if item["show"]["movie"].casefold() == str(args.get("movie", "")).casefold()
                                and item["day"] == args.get("day")
                                and item["experience"].casefold()
                                == str(args.get("experience", "Standard")).casefold()), None)
            if not preview or preview["rating"] != args.get("acknowledged_rating"):
                return self.decline("Preview the specific showtime and its admission conditions first.")
            print(f"{preview['show']['movie']} · {preview['day']} · Rating {preview['rating']}")
            if preview["admission"]:
                print(preview["admission"])
            if tool_name == "select_seats":
                print("Requested seats " + ", ".join(args.get("seats", [])))
            else:
                print(f"Requested group size {args.get('party_size')} · Earliest {args.get('after', '00:00')}")
            if input("Does everyone meet VOX’s admission conditions? Hold seats now? [y/N]: ").strip().lower() != "y":
                return self.decline("The guest declined the seat hold. End this turn.")

        result = await super().call_tool(tool_name, arguments, meta=meta)
        data = result.structured_content or {}
        if tool_name == "find_showtimes":
            shows = data.get("screenings") or []
            if args.get("showtime_id") and shows and data.get("rating"):
                self.previews[args["showtime_id"]] = {
                    "show": shows[0], "day": args.get("day"),
                    "experience": shows[0]["experience"],
                    "rating": data["rating"], "admission": data.get("admission"),
                }
            print(f"  VOX returned {len(shows)} screenings and {len(data.get('seats') or [])} previewed seat groups.")
        elif data.get("status") == "ready_for_checkout":
            self.checkout = data
            print("  VOX held seats " + ", ".join(data["seats"]))
        return result


async def chat(directory):
    load_dotenv(ROOT / ".env")
    model = os.environ.get("MODEL")
    if not model:
        raise ValueError("Set MODEL and a provider key in .env to use chat. See .env.example.")
    set_tracing_disabled(True)
    async with CinemaServer(directory) as server:
        print("Connected to MCP tools " + ", ".join(tool.name for tool in await server.list_tools()))
        agent = Agent(name="Beirut Cinema Checkout", model=model, mcp_servers=[server],
                      instructions=INSTRUCTIONS.format(today=datetime.now(ZONE).date()))
        session = SQLiteSession("beirut-cinema")
        print("Ask about a movie or seats together. Type /quit to exit.")
        while True:
            message = input("you: ").strip()
            if message == "/quit":
                return 0
            if not message:
                continue
            result = await Runner.run(agent, message, session=session, max_turns=10)
            print("Agent:", result.final_output, flush=True)
            if server.checkout:
                await open_checkout(directory, server.checkout)
                server.checkout = None
