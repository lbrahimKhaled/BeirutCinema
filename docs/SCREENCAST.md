# Beirut Cinema Checkout screencast

Aim for 5–7 minutes. Read the quoted lines in your own voice. The other text tells you what to show.

## Before recording

- Open the GitHub repo and its README. Have Terminal and Chrome ready.
- Run `./start_demo.sh find` and pick an actual title and date with at least two showtimes. Pick a film whose rating suits your group. Use the exact movie, times, and seats returned during recording in place of the bracketed text.
- If using chat, configure your model in the ignored `.env` file and run `./start_demo.sh chat`. Keep the credential file closed.
- Use one window for the terminal and one for checkout. Stop the demonstration at VOX’s **Your Details**, **Food & Drinks**, or **Review & Pay** step. Close the checkout when finished. If you need to release seats immediately, use VOX’s **Change Showtime** or cancel control where shown.

## 0:00–0:40 · Introduce the project

Show the README and the three tool names.

> This is Beirut Cinema Checkout. It helps a group find seats together at VOX Cinemas in City Centre Beirut. The guest gives a date, a movie preference, and a group size. The assistant finds a live showing and opens checkout with the group’s seats held. The guest enters their own details and pays on VOX.

## 0:40–1:20 · Explain the MCP round trip

Point to the architecture diagram. Run `./start_demo.sh tools` in a second terminal if you want to show the live tool list.

> The terminal runs the host. Its MCP client starts a FastMCP server and sends structured calls over standard input and output. The server has exactly three tools. Data reads showtimes and seats. Action requests a hold on a selected group. Orchestration chooses a viable showing and seat group, then uses the same hold operation. The tool result comes back to the host, which opens VOX checkout if the hold succeeded.

If recording the chat mode, add this line.

> The natural-language host uses the OpenAI Agents SDK to choose the appropriate MCP tool from the guest’s request.

## 1:20–2:15 · Demonstrate Data

In chat, say or type:

```text
What Standard showtimes are available for [live movie] on [date] after [time]?
Please preview seats together for two at [returned showtime]. I’m just checking availability.
```

Point to the `[Data] find_showtimes` line and the screening or seat groups it returns. In terminal mode, run `./start_demo.sh find --date YYYY-MM-DD --movie "Movie Title"`, then use `manual` to show seat previews.

> The Data tool reads VOX’s live schedule and seat map. It gives us the showtime, the film rating, and the groups that can sit next to each other. At this point we can look at options and choose one.

## 2:15–3:25 · Demonstrate Action

Use a group actually returned in the preview.

```text
Hold the exact seats [seat labels] for that show. I’ll review the VOX checkout myself.
```

The terminal displays VOX’s rating and asks for approval. Enter `y` when you are ready. In menu mode, run `./start_demo.sh manual`, choose the showtime number and then a group number, and approve.

> I chose a specific group. The Action tool checks the exact seats against the current map and requests a temporary hold. The terminal shows the seats and the basket total, and the browser opens the same guest checkout session. This checkout came from VOX for the group I selected.

Point to the browser step and basket. Close it before starting the next example.

## 3:25–4:40 · Demonstrate Orchestration

Use a different real time. In chat, say or type:

```text
Pick a Standard showing for [live movie] on [date] after [time].
We need three seats together. Choose the seats for me and prepare checkout.
```

Approve the live hold when prompted. In menu mode, run `./start_demo.sh` and enter the same preferences. Point to `[Orchestration] prepare_checkout` and the returned showing and seats.

> This time I gave it constraints and asked it to make the decision. The Orchestration tool searches the live showtimes, checks each candidate for three adjacent seats, and starts checkout for the earliest show that fits. It favors standard seats near the middle and keeps the group in the same row. VOX makes the final availability check.

## 4:40–5:35 · Demonstrate a failure and explain the hard parts

Run a search with a nonexistent title.

```bash
./start_demo.sh find --movie "No Such Demo Film"
```

> If the request matches nothing or there are no seats together, the tools return that outcome. The seat map can change between search and checkout, so the Action tool checks again at the moment it puts seats on hold. The handoff also needs to keep the temporary guest session, since that session is how VOX knows which basket to show.

## 5:35–6:15 · Wrap up

Return to the repo tab. Point to the README and the verification command.

> This demo uses live VOX showtimes and a real guest checkout. I can demonstrate reading data, requesting an exact hold, and delegating the whole selection across the three MCP tools. The repo includes the launcher, tests, and an opt-in live check that opens both checkout flows and releases the test holds. VOX confirms tickets after the customer finishes payment.

End on the separate BeirutCinema GitHub repo.
