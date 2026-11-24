import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

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
            yield type("Cart", (), {"rows": [[None] if show_id == first.id else [seat(1), seat(2)]]})()

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
