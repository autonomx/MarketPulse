from __future__ import annotations

from decimal import Decimal

from scripts.trade.build_stockmarketwatch_top_gainer_cache import (
    parse_digest_top_gainers,
)


def test_parse_digest_top_gainers_from_rendered_html() -> None:
    tickers = "".join(
        f'<div><a href="/stock/{symbol}">{symbol}</a>'
        f'<strong>+{index}.25%</strong></div>'
        for index, symbol in enumerate(
            ("VIOT", "BIAF", "GPRO", "LHAI", "SWVL", "FCUV", "NCPL", "TLYS"),
            start=1,
        )
    )
    html = f"The day's top movers{tickers}Losers<a href=\"/stock/ADBT\">ADBT</a>"

    assert parse_digest_top_gainers(html) == [
        (symbol, Decimal(f"{index}.25"))
        for index, symbol in enumerate(
            ("VIOT", "BIAF", "GPRO", "LHAI", "SWVL", "FCUV", "NCPL", "TLYS"),
            start=1,
        )
    ]


def test_parse_digest_top_gainers_from_next_rsc_markup() -> None:
    html = (
        "The day's top movers "
        + "".join(
            f'\\"/stock/{symbol}\\" children=\\"+{index}.00%\\" '
            for index, symbol in enumerate(
                ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"),
                start=1,
            )
        )
        + "Losers"
    )

    assert [symbol for symbol, _gain in parse_digest_top_gainers(html)] == [
        "AAA",
        "BBB",
        "CCC",
        "DDD",
        "EEE",
        "FFF",
        "GGG",
        "HHH",
    ]


def test_parse_digest_top_gainers_prefers_visible_section_over_after_hours_copy() -> None:
    symbols = ("VIOT", "BIAF", "GPRO", "LHAI", "SWVL", "FCUV", "NCPL", "TLYS")
    html = (
        "The day&#x27;s top movers"
        + "".join(
            f'<div><a href="/stock/{symbol}">{symbol}</a>'
            f'<strong>+{index}.00%</strong></div>'
            for index, symbol in enumerate(symbols, start=1)
        )
        + "Losers"
        + '<div><a href="/stock/TLYS">TLYS</a><strong>+31.58%</strong></div>'
    )

    assert parse_digest_top_gainers(html) == [
        (symbol, Decimal(f"{index}.00"))
        for index, symbol in enumerate(symbols, start=1)
    ]
