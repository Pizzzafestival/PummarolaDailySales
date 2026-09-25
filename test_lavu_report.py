import io
import os
import unittest
import urllib.parse
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import lavu_report as report


class ReporterTests(unittest.TestCase):
    def test_all_configurations_retained_but_only_six_enabled(self):
        self.assertEqual(8, len(report.LOCATIONS))
        self.assertEqual(
            ["The Falls", "Coral Springs"],
            [location.name for location in report.LOCATIONS if not location.enabled],
        )

    def test_summarizes_without_retaining_order_details(self):
        totals = report.summarize(
            [
                {"subtotal": "100", "discount": "10", "adjustment": "2"},
                {"subtotal": "30", "refund_amount": "5"},
                {"subtotal": "20", "status": "voided"},
            ]
        )
        self.assertEqual(Decimal("117"), totals.net_sales)
        self.assertEqual(3, totals.orders)
        self.assertEqual(Decimal("10"), totals.discounts)
        self.assertEqual(Decimal("25"), totals.refunds_voids)
        self.assertEqual(Decimal("2"), totals.adjustments)
        self.assertEqual(Decimal("39"), totals.average_order)

    @patch("lavu_report.fetch_orders", return_value=[])
    def test_partial_report_output_and_no_calls_for_exclusions(self, fetch):
        output = io.StringIO()
        with redirect_stdout(output):
            report.run(date(2026, 9, 24))
        text = output.getvalue()
        self.assertIn(report.PARTIAL_NOTICE, text)
        self.assertIn("Six-location subtotal", text)
        self.assertNotIn("Pummarola group total", text)
        self.assertEqual(6, fetch.call_count)
        self.assertNotIn("The Falls:", text)
        self.assertNotIn("Coral Springs:", text)

    @patch("lavu_report.urllib.request.urlopen", side_effect=OSError("secret detail"))
    def test_transport_errors_do_not_expose_credentials(self, _urlopen):
        location = report.LOCATIONS[0]
        env = {
            "LAVU_BOCA_RATON_KEY": "super-secret-key",
            "LAVU_BOCA_RATON_TOKEN": "super-secret-token",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OSError") as raised:
                report.fetch_orders(location, date(2026, 9, 24))
        self.assertNotIn("super-secret", str(raised.exception))

    def test_wrapper_root_parses_xml_row_fragments(self):
        rows = report.parse_rows("<row><subtotal>12.34</subtotal></row><row><subtotal>5</subtotal></row>")
        self.assertEqual([{"subtotal": "12.34"}, {"subtotal": "5"}], rows)

    @patch("lavu_report.PAGE_SIZE", 2)
    @patch("lavu_report.urllib.request.urlopen")
    def test_legacy_request_uses_exclusive_date_bound_and_pages(self, urlopen):
        class Response:
            def __init__(self, value):
                self.value = value

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return self.value.encode()

        urlopen.side_effect = [
            Response("<row><subtotal>1</subtotal></row><row><subtotal>2</subtotal></row>"),
            Response("<row><subtotal>3</subtotal></row>"),
        ]
        env = {"LAVU_BOCA_RATON_KEY": "key", "LAVU_BOCA_RATON_TOKEN": "token"}
        with patch.dict(os.environ, env, clear=True):
            rows = report.fetch_orders(report.LOCATIONS[0], date(2026, 9, 23))

        self.assertEqual(3, len(rows))
        requests = [call.args[0] for call in urlopen.call_args_list]
        self.assertTrue(all(request.full_url == "https://admin.poslavu.com/cp/reqserv/" for request in requests))
        forms = [urllib.parse.parse_qs(request.data.decode()) for request in requests]
        self.assertEqual(["getData"], forms[0]["dataname"])
        self.assertEqual(["orders"], forms[0]["table"])
        self.assertEqual(["closed"], forms[0]["column"])
        self.assertEqual(["2026-09-23 00:00:00"], forms[0]["value_min"])
        self.assertEqual(["2026-09-24 00:00:00"], forms[0]["value_max"])
        self.assertEqual([["0,2"], ["2,2"]], [form["limit"] for form in forms])


if __name__ == "__main__":
    unittest.main()
