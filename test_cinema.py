import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup
from fastmcp import Client

import server
import vox


def seat(number, area="main", standard=True):
    return vox.Seat(f"E-{number}", str(number), area, standard)


class SeatTest(unittest.TestCase):
    def test_rejects_across_an_aisle_or_tier_boundary(self):
        row = [seat(1), None, seat(2), seat(3, "vip"), seat(4, "vip")]
        self.assertEqual([[item.label for item in group] for group in vox.adjacent_groups([row], 2)],
                         [["E-3", "E-4"]])

    def test_prefers_standard_seats(self):
        row = [seat(1, "vip", False), seat(2, "vip", False), seat(3), seat(4)]
        self.assertEqual([item.label for item in vox.adjacent_groups([row], 2)[0]], ["E-3", "E-4"])

    def test_disabled_and_accessible_cells_are_outside_standard_groups(self):
        markup = """
        <form><div class="scroll-horizontal"><table><tr>
          <td class="seat normal standard" data-area="1">
            <input name="seat" value="10" data-label="E-10"></td>
          <td class="seat normal standard" data-area="1">
            <input name="seat" value="11" data-label="E-11" disabled></td>
          <td class="seat wheelchair" data-area="1">
            <input name="seat" value="12" data-label="E-12"></td>
        </tr></table></div></form>"""
        rows = vox.parse_rows(BeautifulSoup(markup, "html.parser").form)
        self.assertEqual([[s.label if s else None for s in row] for row in rows],
                         [["E-10", None, None]])


class McpTest(unittest.IsolatedAsyncioTestCase):
    async def test_server_exposes_exactly_three_tools(self):
        async with Client(server.mcp) as client:
            tools = await client.list_tools()
        self.assertEqual({tool.name: tool.annotations.read_only_hint for tool in tools},
                         {"find_showtimes": True, "select_seats": False, "prepare_checkout": False})

    async def test_orchestration_chooses_next_show_when_first_cannot_fit_party(self):
        start = datetime.now(vox.ZONE) + timedelta(days=1)
        first = vox.Screening(id="0008-1", movie="Film", rating="G",
                              experience="Standard", starts_at=start)
        second = first.model_copy(update={"id": "0008-2", "starts_at": start+timedelta(minutes=5)})

        @contextmanager
        def lookup(show_id):
            yield type("Cart", (), {"rating": "G", "rows": [[None] if show_id == first.id else [seat(1), seat(2)]]})()

        def finish(cart, group, rating, screening):
            return server.Checkout(status="ready_for_checkout", screening=screening,
                                   seats=[s.label for s in group], detail="ready")

        with patch("vox.showtimes", return_value=[first, second]), patch("vox.guest_cart", lookup), \
             patch("server.finish", side_effect=finish) as allocation:
            async with Client(server.mcp) as client:
                result = await client.call_tool("prepare_checkout", {
                    "day": start.date().isoformat(), "movie": "Film", "party_size": 2,
                    "acknowledged_rating": "G",
                })
        self.assertEqual(result.structured_content["screening"]["id"], second.id)
        self.assertEqual(result.structured_content["seats"], ["E-1", "E-2"])
        allocation.assert_called_once()


class GuestFlowTest(unittest.TestCase):
    def test_guest_can_land_directly_on_seat_map(self):
        initial = type("Response", (), {
            "url": vox.BASE + "/booking/example/seats",
            "text": """
                <aside class="movie-hero"><span class="classification">G</span></aside>
                <a href="/booking/example/abandon?success_url=/showtimes">Change Showtime</a>
                <form id="seat-selection" action="/booking/example/allocate">
                <div class="scroll-horizontal"><table><tr>
                    <td class="seat normal standard" data-area="1">
                        <input name="seat" data-label="E-1" value="seat-1"></td>
                </tr></table></div></form>""",
        })()
        with patch("vox.request", return_value=initial) as request:
            with vox.guest_cart("0008-123") as cart:
                self.assertEqual(cart.rating, "G")
                self.assertEqual(cart.rows[0][0].label, "E-1")
            self.assertEqual([call.args[1] for call in request.call_args_list], ["GET", "GET"])
            self.assertIn("/abandon?", request.call_args.args[2])

    def test_exact_group_rejects_seats_across_aisle(self):
        with self.assertRaises(vox.SeatUnavailable):
            vox.exact_group([[seat(1), None, seat(2)]], ["E-1", "E-2"])

    def test_rating_mismatch_stops_before_allocating(self):
        cart = type("Cart", (), {"rating": "16+"})()
        with patch("vox.request") as request:
            with self.assertRaises(vox.VoxError):
                vox.allocate(cart, [seat(1)], "G")
        request.assert_not_called()

    def test_guest_waits_for_initial_basket_before_looking_for_guest_link(self):
        processing = type("Response", (), {
            "url": vox.BASE + "/booking/example/guest/processing",
            "text": """<script>vox.data.status('/booking/example/guest/processing?json=1');</script>""",
        })()
        ready = type("Response", (), {"json": lambda _: {"data": {"url": "/booking/example/guest"}}})()
        guest = type("Response", (), {
            "text": """<a href="/booking/example/seats/fetch">Continue as Guest</a>
                       <a href="/booking/example/abandon?success_url=/showtimes">Cancel</a>"""
        })()
        seats = type("Response", (), {
            "url": vox.BASE + "/booking/example/seats",
            "text": '<form id="seat-selection" action="/booking/example/allocate"></form>',
        })()
        with patch("vox.request", side_effect=[processing, ready, guest, seats, None]) as call:
            with vox.guest_cart("0008-123") as cart:
                self.assertEqual(cart.url, seats.url)
        self.assertEqual(call.call_args_list[3].args[2], "/booking/example/seats/fetch")


class AgentGuardTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        try:
            from agent_client import CinemaServer
        except ImportError:
            self.skipTest("Install requirements-agent.txt to test the chat host.")
        self.host = CinemaServer("/tmp")

    async def test_hold_requires_live_admission_preview(self):
        result = await self.host.call_tool("select_seats", {
            "showtime_id": "0008-1", "acknowledged_rating": "G", "seats": ["F-11", "F-12"],
        })
        self.assertTrue(result.is_error)
        self.assertIn("Preview", result.content[0].text)
        self.assertIsNone(self.host.checkout)

    async def test_declining_the_hold_stops_before_calling_the_provider(self):
        self.host.previews["0008-1"] = {
            "show": {"movie": "Film"}, "day": "2026-11-26", "rating": "G", "admission": "Rated G",
        }
        with patch("builtins.input", return_value="n"):
            result = await self.host.call_tool("select_seats", {
                "showtime_id": "0008-1", "acknowledged_rating": "G", "seats": ["F-11", "F-12"],
            })
        self.assertTrue(result.is_error)
        self.assertIn("declined", result.content[0].text)
        self.assertIsNone(self.host.checkout)


class CheckoutNavigationTest(unittest.IsolatedAsyncioTestCase):
    def test_status_request_keeps_only_guest_checkout_cookies(self):
        origin = "lbn.voxcinemas.com"
        cookies = [
            {"name": name, "domain": domain, "value": "test", "path": "/", "secure": True}
            for name, domain in [("JSESSIONID", origin), ("vox_pes_test", origin),
                                 ("tracking", origin), ("JSESSIONID", "example.org")]
        ]
        with vox.checkout_session(cookies) as session:
            self.assertEqual({(cookie.name, cookie.domain) for cookie in session.cookies},
                             {("JSESSIONID", origin), ("vox_pes_test", origin)})

    async def test_editor_transport_is_scoped_to_this_basket_and_get_requests(self):
        from client import use_checkout_seat_editor

        page = SimpleNamespace(route=AsyncMock())
        await use_checkout_seat_editor(page, vox.BASE + "/booking/current/details#details")
        matcher, handle = page.route.await_args.args
        self.assertTrue(matcher.fullmatch(vox.BASE + "/booking/current/seats"))
        self.assertTrue(matcher.fullmatch(vox.BASE + "/booking/current/seats/fetch#seats"))
        self.assertFalse(matcher.fullmatch(vox.BASE + "/booking/other/seats"))
        self.assertFalse(matcher.fullmatch(vox.BASE + "/booking/current/payment"))
        route = SimpleNamespace(
            request=SimpleNamespace(method="POST", resource_type="document", url=vox.BASE + "/booking/current/seats"),
            continue_=AsyncMock(), fulfill=AsyncMock(),
        )
        await handle(route)
        route.continue_.assert_awaited_once()
        route.fulfill.assert_not_awaited()

    async def test_confirming_seats_submits_once_through_the_existing_basket(self):
        from client import use_checkout_seat_editor

        page = SimpleNamespace(route=AsyncMock(), context=SimpleNamespace(
            cookies=AsyncMock(return_value=[]),
        ))
        basket = vox.BASE + "/booking/current"
        await use_checkout_seat_editor(page, basket + "/details")
        matcher, handle = page.route.await_args.args
        self.assertTrue(matcher.fullmatch(basket + "/allocate"))
        form_data = "_csrf=test&seat=seat-1&seat=seat-2"
        route = SimpleNamespace(
            request=SimpleNamespace(method="POST", resource_type="document",
                                    url=basket + "/allocate", post_data=form_data),
            continue_=AsyncMock(), fulfill=AsyncMock(),
        )
        with patch("vox.confirm_seat_edit", return_value=basket + "/details") as confirm:
            await handle(route)
        confirm.assert_called_once_with(basket + "/allocate", [], form_data)
        route.continue_.assert_not_awaited()
        self.assertEqual(route.fulfill.await_args.kwargs["status"], 303)

    def test_seat_edit_rejects_a_response_for_a_different_basket(self):
        origin = vox.BASE + "/booking/current"
        response = SimpleNamespace(url=vox.BASE + "/booking/other/details")
        with patch("vox.request", return_value=response), patch("vox.settle", return_value=response):
            self.assertIsNone(vox.confirm_seat_edit(origin + "/allocate", [], "seat=seat-1"))

    async def test_uncertain_seat_submission_returns_to_checkout_without_retrying(self):
        from client import use_checkout_seat_editor

        page = SimpleNamespace(route=AsyncMock(), context=SimpleNamespace(
            cookies=AsyncMock(return_value=[]),
        ))
        basket = vox.BASE + "/booking/current"
        await use_checkout_seat_editor(page, basket + "/details")
        _, handle = page.route.await_args.args
        route = SimpleNamespace(
            request=SimpleNamespace(method="POST", resource_type="document",
                                    url=basket + "/allocate", post_data="seat=seat-1"),
            continue_=AsyncMock(), fulfill=AsyncMock(),
        )
        with patch("vox.confirm_seat_edit", return_value=None) as confirm:
            await handle(route)
        confirm.assert_called_once()
        route.continue_.assert_not_awaited()
        self.assertEqual(route.fulfill.await_args.kwargs["headers"]["location"], basket + "/details")


class ShowtimeFormatTest(unittest.IsolatedAsyncioTestCase):
    async def test_search_defaults_to_all_formats_and_honors_explicit_filter(self):
        day = (datetime.now(vox.ZONE).date() + timedelta(days=1)).isoformat()
        markup = """
        <article class="movie-compare">
          <aside class="movie-hero">
            <h2>The Hunger Games: Sunrise on the Reaping</h2>
            <span class="classification">PG13</span>
          </aside>
          <div class="dates">
            <h3>City Centre Beirut</h3>
            <ol class="showtimes">
              <li><strong>IMAX</strong><a class="showtime" data-id="0008-1">12:55pm</a></li>
              <li><strong>GOLD</strong><a class="showtime" data-id="0008-2">3:45pm</a></li>
              <li><strong>4DX</strong><a class="showtime" data-id="0008-3">6:20pm</a></li>
            </ol>
          </div>
        </article>"""
        arguments = {"day": day, "movie": "Hunger Games Sunrise on the Reaping"}
        with patch("vox.request", return_value=SimpleNamespace(text=markup)):
            async with Client(server.mcp) as client:
                all_formats = await client.call_tool("find_showtimes", arguments)
                standard = await client.call_tool("find_showtimes", {**arguments, "experience": "Standard"})
                imax = await client.call_tool("find_showtimes", {**arguments, "experience": "imax"})
        self.assertEqual([item["experience"] for item in all_formats.structured_content["screenings"]],
                         ["IMAX", "GOLD", "4DX"])
        self.assertEqual(standard.structured_content["screenings"], [])
        self.assertEqual([item["id"] for item in imax.structured_content["screenings"]], ["0008-1"])

    async def test_agent_passes_the_previewed_format_for_the_hold(self):
        try:
            from agent_client import CinemaServer, MCPServerStdio
        except ImportError:
            self.skipTest("Install requirements-agent.txt to test the chat host.")
        host = CinemaServer("/tmp")
        title = "The Hunger Games: Sunrise on the Reaping"
        host.previews["0008-1"] = {
            "show": {"movie": title}, "day": "2026-11-25", "experience": "IMAX",
            "rating": "PG13", "admission": "",
        }
        with patch("builtins.input", return_value="y"), patch.object(
            MCPServerStdio, "call_tool", new_callable=AsyncMock,
            return_value=SimpleNamespace(structured_content={"status": "unavailable"}),
        ) as call:
            await host.call_tool("prepare_checkout", {
                "day": "2026-11-25", "movie": title, "party_size": 2,
                "acknowledged_rating": "PG13",
            })
        self.assertEqual(call.await_args.args[1]["experience"], "IMAX")


class CheckoutBrowserTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.handoff = {"url": vox.BASE + "/booking/example/details#details", "cookies": []}
        self.enterContext(patch("client.read_handoff", return_value=self.handoff))
        self.launch = self.enterContext(patch("client.webbrowser.open", return_value=True))
        self.guest = self.enterContext(patch("client.open_guest_checkout", new_callable=AsyncMock))
        self.output = self.enterContext(patch("builtins.print"))

    async def test_uses_default_browser_and_also_prints_the_exact_link(self):
        from client import open_checkout

        with patch("builtins.input", return_value=""):
            await open_checkout("/tmp", {})
        self.launch.assert_called_once_with(self.handoff["url"], new=2)
        self.output.assert_any_call("VOX checkout link", self.handoff["url"], sep="\n", flush=True)
        self.guest.assert_not_awaited()

    async def test_keeps_copyable_link_when_the_browser_cannot_launch(self):
        from client import open_checkout

        self.launch.side_effect = OSError("browser unavailable")
        with patch("builtins.input", side_effect=EOFError):
            await open_checkout("/tmp", {})
        self.output.assert_any_call("VOX checkout link", self.handoff["url"], sep="\n", flush=True)
        self.output.assert_any_call("Paste the link above into your regular browser.")
        self.guest.assert_not_awaited()

    async def test_guest_fallback_reuses_the_existing_basket(self):
        from client import open_checkout

        with patch("builtins.input", return_value="g"):
            await open_checkout("/tmp", {})
        self.launch.assert_called_once_with(self.handoff["url"], new=2)
        self.guest.assert_awaited_once_with(self.handoff)
