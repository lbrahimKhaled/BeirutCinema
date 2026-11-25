"""Three MCP tools for VOX City Centre Beirut guest checkout."""

import json
import os
from datetime import date, datetime, time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal

import requests
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

import vox

mcp = FastMCP("beirut-cinema")
PartySize = Annotated[int, Field(ge=1, le=10)]
SeatLabels = Annotated[list[str], Field(min_length=1, max_length=10)]


class Options(BaseModel):
    screenings: list[vox.Screening] = []
    seats: list[list[str]] = []
    rating: str | None = None
    admission: str | None = None


class Checkout(BaseModel):
    status: Literal["ready_for_checkout", "unavailable"]
    screening: vox.Screening | None = None
    seats: list[str] = []
    stage: str | None = None
    total: str | None = None
    handoff_id: str | None = None
    detail: str


def as_tool_error(error):
    if isinstance(error, requests.RequestException):
        return ToolError("VOX could not be reached. Check its booking page before retrying.")
    return ToolError(str(error))


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
def find_showtimes(day: date, movie: str = "", experience: str = "",
                   showtime_id: str | None = None, party_size: PartySize = 2) -> Options:
    """Read live City Centre Beirut showtimes in all formats unless experience is supplied.

    With a showtime ID, preview seats together and admission rules.
    """
    if day < datetime.now(vox.ZONE).date():
        raise ToolError("Choose today or a future date in Beirut.")
    try:
        screenings = vox.showtimes(day, movie, experience)
        if showtime_id is None:
            return Options(screenings=screenings)
        match = [screening for screening in screenings if screening.id == showtime_id]
        if not match:
            raise ToolError("That showtime is unavailable for the requested date and format.")
        with vox.guest_cart(showtime_id) as cart:
            groups = vox.adjacent_groups(cart.rows, party_size)
            return Options(screenings=match, seats=[[seat.label for seat in group] for group in groups[:5]],
                           rating=cart.rating, admission=cart.admission)
    except (vox.VoxError, requests.RequestException) as exc:
        raise as_tool_error(exc) from None


def finish(cart, group, rating, screening):
    directory = os.getenv("VOX_HANDOFF_DIR")
    if not directory:
        raise ToolError("Start the included demo client to receive the VOX checkout session.")
    link, stage, total = vox.allocate(cart, group, rating)
    payload = {"url": link, "cookies": vox.checkout_cookies(cart),
               "abandon_url": cart.abandon_url}
    try:
        with NamedTemporaryFile(mode="w", prefix="checkout-", dir=directory,
                                delete=False, encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError:
        cart.kept = False
        raise ToolError("The checkout session could not be handed to the browser.") from None
    return Checkout(status="ready_for_checkout", screening=screening,
                    seats=[seat.label for seat in group], stage=stage, total=total,
                    handoff_id=Path(handle.name).name,
                    detail="Review your seats and total in VOX’s guest checkout. Complete your details and payment there.")


@mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": False})
def select_seats(day: date, showtime_id: str, seats: SeatLabels,
                 acknowledged_rating: str, experience: str = "") -> Checkout:
    """Temporarily hold an exact adjacent seat group and prepare guest checkout after rating acknowledgement."""
    options = find_showtimes(day, experience=experience)
    screening = next((item for item in options.screenings if item.id == showtime_id), None)
    if screening is None:
        raise ToolError("That showtime is unavailable for the requested date and format.")
    try:
        with vox.guest_cart(showtime_id) as cart:
            return finish(cart, vox.exact_group(cart.rows, seats), acknowledged_rating, screening)
    except vox.SeatUnavailable as exc:
        return Checkout(status="unavailable", screening=screening, detail=str(exc))
    except (vox.VoxError, requests.RequestException) as exc:
        raise as_tool_error(exc) from None


@mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": False})
def prepare_checkout(day: date, movie: str, party_size: PartySize, acknowledged_rating: str,
                     after: time = time(0), experience: str = "") -> Checkout:
    """Choose the earliest matching show with adjacent seats and prepare its real guest checkout."""
    options = find_showtimes(day, movie, experience)
    candidates = [show for show in options.screenings
                  if show.starts_at.time() >= after]
    for show in candidates[:4]:
        try:
            with vox.guest_cart(show.id) as cart:
                if cart.rating.casefold() != acknowledged_rating.casefold():
                    continue
                groups = vox.adjacent_groups(cart.rows, party_size)
                if groups:
                    return finish(cart, groups[0], acknowledged_rating, show)
        except vox.SeatUnavailable:
            continue
        except (vox.VoxError, requests.RequestException) as exc:
            raise as_tool_error(exc) from None
    return Checkout(status="unavailable", detail="No matching show had an available group of adjacent seats.")


if __name__ == "__main__":
    mcp.run(show_banner=False)
