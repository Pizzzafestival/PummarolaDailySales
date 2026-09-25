#!/usr/bin/env python3
"""Read-only daily sales summary retrieved directly from Lavu."""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from zoneinfo import ZoneInfo


API_URL = "https://admin.poslavu.com/cp/reqserv/"
PAGE_SIZE = 1_000
PARTIAL_NOTICE = "PARTIAL REPORT — The Falls and Coral Springs excluded."
SUBTOTAL_LABEL = "Six-location subtotal"


@dataclass(frozen=True)
class Location:
    name: str
    env_slug: str
    enabled: bool = True


# Keep the complete configuration inventory here. Re-enabling a location requires
# changing only its enabled flag; its credential names and API setup remain intact.
LOCATIONS = (
    Location("Boca Raton", "BOCA_RATON"),
    Location("Coral Gables", "CORAL_GABLES"),
    Location("Midtown", "MIDTOWN"),
    Location("Miami Beach", "MIAMI_BEACH"),
    Location("Fort Lauderdale", "FORT_LAUDERDALE"),
    Location("North Beach", "NORTH_BEACH"),
    Location("The Falls", "THE_FALLS", enabled=False),
    Location("Coral Springs", "CORAL_SPRINGS", enabled=False),
)


@dataclass
class Totals:
    net_sales: Decimal = Decimal("0")
    orders: int = 0
    discounts: Decimal = Decimal("0")
    refunds_voids: Decimal = Decimal("0")
    adjustments: Decimal = Decimal("0")

    @property
    def average_order(self) -> Decimal:
        return self.net_sales / self.orders if self.orders else Decimal("0")

    def add(self, other: "Totals") -> None:
        self.net_sales += other.net_sales
        self.orders += other.orders
        self.discounts += other.discounts
        self.refunds_voids += other.refunds_voids
        self.adjustments += other.adjustments


def money(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", "").replace("$", ""))
    except InvalidOperation as exc:
        raise ValueError("Lavu returned a non-numeric monetary value") from exc


def first(order: dict[str, Any], names: Iterable[str], default: Any = None) -> Any:
    lowered = {str(key).lower(): value for key, value in order.items()}
    return next((lowered[name.lower()] for name in names if name.lower() in lowered), default)


def parse_rows(fragment: str) -> list[dict[str, Any]]:
    """Parse Lavu's sibling XML rows by supplying its omitted wrapper root."""
    try:
        root = ET.fromstring(f"<root>{fragment}</root>")
    except ET.ParseError:
        raise ValueError("Lavu returned an invalid XML response") from None
    if not list(root) and (root.text or "").strip():
        # Authentication/service messages are deliberately not echoed because they
        # can repeat submitted credential material.
        raise RuntimeError("Lavu rejected the request")

    rows: list[dict[str, Any]] = []
    for element in root:
        row = {str(key): value for key, value in element.attrib.items()}
        row.update({child.tag: child.text or "" for child in element})
        # Some responses wrap the columns in a single <row> child.
        if not row and list(element):
            row = {child.tag: child.text or "" for child in element}
        rows.append(row)
    return rows


def summarize(orders: Iterable[dict[str, Any]]) -> Totals:
    totals = Totals()
    for order in orders:
        status = str(first(order, ("status", "order_status"), "")).lower()
        voided = status in {"void", "voided", "cancelled", "canceled"} or str(
            first(order, ("voided", "is_void"), "0")
        ).lower() in {"1", "true", "yes"}
        refunded = money(first(order, ("refund", "refund_amount", "refunded_amount"), 0))
        discount = abs(money(first(order, ("discount", "discount_amount", "discount_total"), 0)))
        adjustment = money(first(order, ("adjustment", "adjustments", "adjustment_amount"), 0))
        net = money(first(order, ("net_sales", "net_sale", "subtotal"), 0))

        totals.orders += 1
        totals.discounts += discount
        totals.adjustments += adjustment
        if voided:
            totals.refunds_voids += abs(net)
        else:
            totals.refunds_voids += abs(refunded)
            totals.net_sales += net - discount - abs(refunded) + adjustment
    return totals


def fetch_orders(location: Location, report_date: date, timeout: int = 60) -> list[dict[str, Any]]:
    key_name = f"LAVU_{location.env_slug}_KEY"
    token_name = f"LAVU_{location.env_slug}_TOKEN"
    api_key, api_token = os.getenv(key_name), os.getenv(token_name)
    if not api_key or not api_token:
        raise RuntimeError(f"Missing credentials for {location.name} ({key_name}/{token_name})")

    next_date = report_date + timedelta(days=1)
    orders: list[dict[str, Any]] = []
    offset = 0
    while True:
        # getData is the legacy read-only table query. value_max is an exclusive
        # midnight bound, avoiding both fractional-second gaps and the next day.
        form = {
            "dataname": "getData",
            "key": api_key,
            "token": api_token,
            "table": "orders",
            "column": "closed",
            "value_min": f"{report_date.isoformat()} 00:00:00",
            "value_max": f"{next_date.isoformat()} 00:00:00",
            "limit": f"{offset},{PAGE_SIZE}",
        }
        request = urllib.request.Request(
            API_URL,
            data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                rows = parse_rows(response.read().decode("utf-8-sig"))
        except (RuntimeError, ValueError):
            raise
        except Exception as exc:
            # Never include a request URL/body: they contain credentials.
            raise RuntimeError(f"Lavu request failed for {location.name}: {type(exc).__name__}") from None
        orders.extend(rows)
        if len(rows) < PAGE_SIZE:
            return orders
        offset += PAGE_SIZE


def currency(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01')):,.2f}"


def report_line(name: str, totals: Totals) -> str:
    return (
        f"{name}: net sales {currency(totals.net_sales)} | orders {totals.orders:,} | "
        f"average order {currency(totals.average_order)} | discounts {currency(totals.discounts)} | "
        f"refunds/voids {currency(totals.refunds_voids)} | adjustments {currency(totals.adjustments)}"
    )


def run(report_date: date) -> int:
    print(PARTIAL_NOTICE)
    print(f"Sales date: {report_date.isoformat()}")
    group = Totals()
    for location in LOCATIONS:
        if not location.enabled:
            continue
        totals = summarize(fetch_orders(location, report_date))
        group.add(totals)
        print(report_line(location.name, totals))
    print(report_line(SUBTOTAL_LABEL, group))
    return 0


def main(argv: list[str] | None = None) -> int:
    yesterday = datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=yesterday, help="sales date (YYYY-MM-DD)")
    args = parser.parse_args(argv)
    try:
        return run(args.date)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
