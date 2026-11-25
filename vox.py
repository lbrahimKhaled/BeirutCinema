"""VOX Lebanon's public showtimes and guest checkout forms."""

import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from time import sleep
from unicodedata import normalize
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel

BASE = "https://lbn.voxcinemas.com"
ZONE = ZoneInfo("Asia/Beirut")
SHOW_ID = re.compile(r"[0-9]{4}-[0-9]+")
POLL_URL = re.compile(r"""vox\.data\.status\(['"]([^'"]+)""")


class VoxError(Exception):
    pass


class SeatUnavailable(VoxError):
    pass


class Screening(BaseModel):
    id: str
    movie: str
    rating: str
    experience: str
    starts_at: datetime
    cinema: str = "City Centre Beirut"


@dataclass(frozen=True)
class Seat:
    label: str
    value: str
    area: str
    standard: bool


@dataclass
class Cart:
    session: requests.Session
    page: BeautifulSoup
    url: str
    form: Tag
    rating: str
    admission: str
    rows: list[list[Seat | None]]
    abandon_url: str | None
    kept: bool = False


def url(path: str) -> str:
    link = urljoin(BASE, path)
    parts = urlsplit(link)
    if parts.scheme != "https" or parts.netloc != "lbn.voxcinemas.com":
        raise VoxError("VOX directed checkout away from its Lebanon site.")
    return link


def soup(response: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(response.text, "html.parser")


def request(session, method, path, **kwargs):
    response = session.request(method, url(path), timeout=10, **kwargs)
    response.raise_for_status()
    url(response.url)
    return response


def search_text(value: str) -> str:
    return " ".join(re.findall(r"\w+", normalize("NFKC", value).casefold()))


def format_filter(value: str) -> str:
    query = value.strip().casefold()
    return "" if query in {"any", "all", "all formats"} else query


def showtimes(day: date, movie: str, experience: str) -> list[Screening]:
    with requests.Session() as session:
        response = request(session, "GET", "/showtimes", params={"d": day.strftime("%Y%m%d")})
    page = soup(response)
    now = datetime.now(ZONE)
    movie_query = search_text(movie)
    format_query = format_filter(experience)
    films = []
    for article in page.select("article.movie-compare"):
        title = article.select_one(".movie-hero h2")
        cinema = article.select_one(".dates h3")
        classification = article.select_one(".movie-hero .classification")
        if not title or not cinema or cinema.get_text(strip=True) != "City Centre Beirut":
            continue
        name = title.get_text(" ", strip=True)
        if movie_query not in search_text(name):
            continue
        for category in article.select(".dates ol.showtimes > li"):
            label = category.select_one("strong")
            if not label:
                continue
            format_name = label.get_text(" ", strip=True)
            if format_query and format_name.casefold() != format_query:
                continue
            for link in category.select("a.showtime[data-id]:not(.disabled)"):
                show_id = link["data-id"]
                if not SHOW_ID.fullmatch(show_id):
                    continue
                try:
                    local_time = datetime.strptime(link.get_text(strip=True), "%I:%M%p").time()
                except ValueError:
                    continue
                start = datetime.combine(day, local_time, ZONE)
                if start > now:
                    films.append(Screening(id=show_id, movie=name, rating=(
                        classification.get_text(strip=True) if classification else "See VOX"),
                        experience=format_name, starts_at=start))
    return sorted(films, key=lambda film: (film.starts_at, film.movie, film.experience))


def settle(session, response):
    for _ in range(5):
        poll = POLL_URL.search(response.text)
        if not poll:
            return response
        for _ in range(6):
            try:
                status = request(session, "GET", poll[1]).json()["data"]
                if not isinstance(status, dict):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                raise VoxError("VOX returned an unexpected checkout status.") from None
            if status.get("error"):
                raise VoxError(f"VOX could not prepare the cart: {str(status['error'])[:200]}")
            if next_page := status.get("url"):
                response = request(session, "GET", next_page)
                break
            sleep(1)
        else:
            raise VoxError("VOX is still preparing the checkout. Please try again later.")
    raise VoxError("VOX did not finish preparing the checkout.")


def parse_rows(form: Tag) -> list[list[Seat | None]]:
    rows = []
    for tr in form.select(".scroll-horizontal table tr"):
        row = []
        for cell in tr.find_all("td", recursive=False):
            button = cell.select_one('input[name="seat"][data-label][value]:not([disabled])')
            classes = cell.get("class", [])
            if button and "normal" in classes:
                row.append(Seat(button["data-label"], button["value"],
                                cell.get("data-area", ""), "standard" in classes))
            else:
                row.append(None)
        if row:
            rows.append(row)
    return rows


def adjacent_groups(rows: list[list[Seat | None]], count: int) -> list[list[Seat]]:
    options = []
    for row_number, row in enumerate(rows):
        for offset in range(len(row) - count + 1):
            group = row[offset:offset + count]
            if any(seat is None for seat in group):
                continue
            if len({seat.area for seat in group}) != 1:
                continue
            score = (not all(seat.standard for seat in group),
                     abs(row_number - (len(rows) - 1) * 0.44),
                     abs(offset + (count - 1) / 2 - (len(row) - 1) / 2))
            options.append((score, group))
    return [group for _, group in sorted(options, key=lambda option: option[0])]


def exact_group(rows: list[list[Seat | None]], labels: list[str]) -> list[Seat]:
    wanted = set(labels)
    for group in adjacent_groups(rows, len(labels)):
        if {seat.label for seat in group} == wanted:
            return group
    raise SeatUnavailable("Those seats are no longer available together in one row.")


@contextmanager
def guest_cart(show_id: str):
    if not SHOW_ID.fullmatch(show_id):
        raise VoxError("Use a showtime ID returned by find_showtimes.")
    with requests.Session() as session:
        abandon = None
        cart = None
        try:
            initial = settle(session, request(session, "GET", "/booking/" + show_id))
            page = soup(initial)
            cancel = page.select_one('a[href*="/abandon?"]')
            abandon = cancel["href"] if cancel else None
            guest = page.find("a", string=lambda text: text and text.strip() == "Continue as Guest")
            # VOX sometimes opens the anonymous seating step directly.
            response = settle(session, request(session, "GET", guest["href"])) if guest else initial
            page = soup(response)
            if not abandon and (cancel := page.select_one('a[href*="/abandon?"]')):
                abandon = cancel["href"]
            form = page.select_one("form#seat-selection[action]")
            if not form:
                raise SeatUnavailable("VOX did not publish a selectable seat map for this showtime.")
            ratings = page.select_one(".movie-hero .classification")
            rules = page.select_one("#restrictions-blocker")
            ratings_text = ratings.get_text(strip=True) if ratings else "See VOX"
            rules_text = " ".join(paragraph.get_text(" ", strip=True)
                                  for paragraph in rules.select("p") if not paragraph.select("a")) if rules else ""
            cart = Cart(session, page, response.url, form, ratings_text, rules_text,
                        parse_rows(form), url(abandon) if abandon else None)
            yield cart
        finally:
            if abandon and not (cart and cart.kept):
                try:
                    request(session, "GET", abandon)
                except (requests.RequestException, VoxError):
                    pass


def allocate(cart: Cart, group: list[Seat], acknowledged_rating: str):
    if acknowledged_rating.casefold() != cart.rating.casefold():
        raise VoxError(f"VOX lists this film as {cart.rating}. Review its admission conditions before holding seats.")
    fields = [(item["name"], "true" if item["name"] == "ticketRestrictions" else item.get("value", ""))
              for item in cart.form.select('input[type="hidden"][name]')]
    fields.extend(("seat", seat.value) for seat in group)
    response = settle(cart.session, request(
        cart.session, "POST", cart.form["action"], data=fields, headers={"Referer": cart.url}))
    page = soup(response)
    section = page.select_one("main section.current")
    stage = section.get("id") if section else None
    if stage == "seats":
        raise SeatUnavailable("VOX returned to the seat map. The requested group could not be held.")
    if stage not in {"offers", "extras", "details", "payment"}:
        raise VoxError("VOX did not display a checkout step for the requested seats.")
    total = page.select_one(".order-overlay .total")
    cart.kept = True
    return response.url, stage, total.get_text(" ", strip=True) if total else None


def is_checkout_cookie(name: str, domain: str) -> bool:
    return domain.lstrip(".") == "lbn.voxcinemas.com" and (
        name in {"JSESSIONID", "AWSALB", "AWSALBCORS"} or name.startswith("vox_pes_")
    )


def checkout_cookies(cart: Cart) -> list[dict]:
    """Export only the app's own first-party guest order and affinity cookies."""
    return [
        {"name": cookie.name, "value": cookie.value, "domain": cookie.domain,
         "path": cookie.path, "secure": cookie.secure,
         "httpOnly": cookie.has_nonstandard_attr("HttpOnly")}
        for cookie in cart.session.cookies
        if is_checkout_cookie(cookie.name, cookie.domain)
    ]


def checkout_session(cookies: list[dict]) -> requests.Session:
    session = requests.Session()
    for cookie in cookies:
        if is_checkout_cookie(cookie["name"], cookie["domain"]):
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"],
                                path=cookie["path"], secure=cookie["secure"])
    return session


def seat_editor_document(address: str, cookies: list[dict]) -> tuple[bytes, dict] | None:
    """Load VOX's own editable seat map through the app's HTTPS transport."""
    with checkout_session(cookies) as session:
        try:
            response = settle(session, request(session, "GET", address))
        except (requests.RequestException, VoxError):
            return None
    editor_path = urlsplit(address).path.removesuffix("/fetch")
    if (urlsplit(response.url).path != editor_path
            or not soup(response).select_one("#seats.current form#seat-selection")):
        return None

    # requests has decoded the body. Keep VOX's document and security headers,
    # and leave cookie management with the Chrome guest session.
    omitted = {"content-encoding", "content-length", "connection", "transfer-encoding",
               "set-cookie", "alt-svc"}
    headers = {name: value for name, value in response.headers.items()
               if name.lower() not in omitted}
    return response.content, headers


def confirm_seat_edit(address: str, cookies: list[dict], form_data: str) -> str | None:
    """Submit exactly the seat form the guest confirmed, once."""
    basket = urlsplit(address).path.removesuffix("/allocate")
    with checkout_session(cookies) as session:
        try:
            response = settle(session, request(
                session, "POST", address, data=form_data,
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Referer": url(basket + "/seats")},
            ))
        except (requests.RequestException, VoxError):
            return None
    if urlsplit(response.url).path in {basket + "/" + step
                                       for step in ("seats", "offers", "extras", "details", "payment")}:
        return response.url
    return None


def abandon_handoff(handoff: dict) -> bool:
    """Release the app's unpaid test cart or a cart whose browser could not start."""
    if not handoff.get("abandon_url"):
        return False
    with checkout_session(handoff["cookies"]) as session:
        try:
            request(session, "GET", handoff["abandon_url"])
        except (requests.RequestException, VoxError):
            return False
    return True
