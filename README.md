# Beirut Cinema Checkout

An MCP demo for buying seats together at VOX Cinemas in City Centre Beirut. Tell the assistant your date, movie, group size, and earliest time. It checks live VOX availability, holds an adjacent group, and opens the official guest checkout. The customer completes their details and pays there.

## Tools

| Tool | Category | What it does |
|---|---|---|
| `find_showtimes(day, movie, experience, showtime_id, party_size)` | Data | Reads showtimes for City Centre Beirut. With a showtime ID, it previews adjacent seat groups and reads VOX’s admission conditions. |
| `select_seats(day, showtime_id, seats, acknowledged_rating, experience)` | Action | Rechecks the exact seat group the customer selected and requests a temporary hold in VOX. |
| `prepare_checkout(day, movie, party_size, acknowledged_rating, after, experience)` | Orchestration | Searches for a matching show, ranks contiguous seat groups, and prepares checkout for the earliest suitable screening. |

Tool results include the movie, local start time, selected seats, basket total when VOX supplies it, and checkout stage. A live checkout is a temporary hold. VOX confirms the tickets after payment.

## Architecture

```text
Customer
  └─ Terminal host (client.py or agent_client.py)
       ├─ MCP client over stdio → FastMCP server (server.py)
       │                             └─ VOX showtimes and guest forms (vox.py)
       └─ Isolated Chrome window → VOX guest checkout
```

The natural-language host uses the OpenAI Agents SDK. The same MCP server also works with the menu-driven launcher. The hosts display the tool category when each call runs. After VOX accepts a seat hold, the host transfers only the temporary guest session to a fresh Chrome window so checkout has the correct basket.

## Setup and run

Use Python 3.11 or later and Google Chrome. The launcher creates a virtual environment and installs the Python dependencies.

```bash
./start_demo.sh
```

Provide a date, movie or permission to pick, group size, earliest time, and format. Review the admission requirements and approve the hold in the terminal. Keep the terminal open while you finish in Chrome. To view or change the seats, use **Edit** next to **Choose Seats** in VOX. The new choice stays in the same temporary basket. Dismiss VOX’s cookie notice if it covers **Confirm Seats**.

| Command | Use it to |
|---|---|
| `./start_demo.sh tools` | List the three MCP tools. |
| `./start_demo.sh find --movie "Movie Title"` | Read showtimes with no seat hold. Add `--date YYYY-MM-DD` to select a date. |
| `./start_demo.sh manual` | Choose a showing and an exact group to demonstrate Action. |
| `./start_demo.sh` | Let Orchestration pick a showing and seats for you. |
| `./start_demo.sh chat` | Use a natural-language MCP agent. |

For chat, copy `.env.example` to `.env`, set `MODEL` and the matching provider API key, and run the chat command. The first chat launch installs the additional SDK dependencies. The terminal modes work without a model key.

## Failure handling

| Situation | What happens |
|---|---|
| No showtimes match the date, movie, or format | Data returns an empty list so the customer can revise the search. |
| The selected seats are taken, separated by an aisle, or in different areas | The exact-seat request returns `unavailable`. |
| Orchestration encounters a sold-out or unavailable showing | It checks up to four matching showtimes and returns `unavailable` if it finds no group. |
| The admission conditions changed or the customer declines the hold | The hold request stops before submitting seats. |
| VOX checkout is temporarily unavailable | The tool reports the provider problem. If Chrome cannot launch, the host attempts to release the unpaid hold. |

## Verification

Run the offline tests after starting the launcher once.

```bash
.venv/bin/python -m unittest -v
```

To verify the live round trip, run the following from an interactive desktop. It exercises all three tools, opens both checkouts in Chrome, reloads them, edits the Action seat group, and releases both temporary holds.

```bash
.venv/bin/python verify_live.py --hold-and-release
```

VOX inventory is live. Verification requires a current showing with an adjacent pair. The test stops at guest checkout before customer details and payment.

## Presentation

The [5–7 minute screencast script](docs/SCREENCAST.md) includes spoken lines, prompts, and recording cues.

[Official VOX showtimes](https://lbn.voxcinemas.com/showtimes) · [VOX Lebanon booking FAQ](https://lbn.voxcinemas.com/faq)
