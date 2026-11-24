# Beirut Cinema Checkout

A three-tool MCP demo for VOX at City Centre Beirut. It reads real showtimes, finds seats that are physically adjacent, and opens VOX’s guest checkout with those seats held. The guest provides their details and pays on VOX.

| Category | MCP tool | Effect |
|---|---|---|
| Data | `find_showtimes` | Lists live showtimes. Given a specific showtime, previews adjacent seat groups and its admission conditions. |
| Action | `select_seats` | Requests a hold for a specific adjacent group in the selected showing. |
| Orchestration | `prepare_checkout` | Searches matching showtimes, chooses the earliest one with enough adjacent seats, and prepares its checkout. |

Automatic selection prefers standard seats in the middle of a row. Blank spaces, inaccessible seats, sold seats, and rows are never crossed to build a group. The site decides whether a group can be held. If the first group fails because availability changed, orchestration checks other matching shows.

## Run

Install Google Chrome and use Python 3.11+. The launcher installs the project dependencies into its own virtual environment.

```bash
./start_demo.sh
```

Enter a date, movie preference, party size, and earliest time. Leave the movie blank to let the demo choose the earliest title it finds. Review the admission conditions and authorize the temporary seat hold. The demo prints the selected seats and available basket total, then opens the relevant guest checkout in an isolated Chrome window. Finish the checkout in that window and leave the terminal open until you’re done.

The checkout belongs to the guest session, so use the opened browser window. VOX shows the checkout timer and confirms tickets after payment.

- `./start_demo.sh tools` lists exactly three tools.
- `./start_demo.sh find` reads live showtimes for tomorrow without holding seats.
- `./start_demo.sh manual` lets you choose the exact showing and seat group before calling the Action tool.
- `./start_demo.sh` runs the Orchestration flow.

## Demo talking points

Start with `tools`, then run `find` to show the difference between reading provider inventory and holding it. Use `manual` to show the Action tool by selecting a group. Use the default launcher to demonstrate how the Orchestration tool combines live search, seat selection, and the same VOX hold operation. Show the resulting VOX checkout and timer. Complete payment only for tickets you intend to purchase.

This project communicates with VOX’s public website. The cinema may change its checkout controls and availability.

[VOX showtimes](https://lbn.voxcinemas.com/showtimes) · [VOX booking FAQ](https://lbn.voxcinemas.com/faq)
