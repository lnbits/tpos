from datetime import datetime

from ..models import render_summary_text


def test_render_summary_text_totals():
    text = render_summary_text(
        start=datetime(2026, 7, 19, 0, 0),
        end=datetime(2026, 7, 20, 0, 0),
        sales_count=3,
        total_sats=15000,
        totals_by_currency={"usd": 12.5, "eur": 4.0},
        business_name="Test Store",
        business_address="Rua Exemplo, 123",
        business_vat_id="VAT123",
    )
    lines = text.splitlines()

    assert lines[0] == "DAILY SUMMARY"
    assert "2026-07-19" in lines[1]
    assert "Sales: 3" in lines
    assert "Total (sats): 15000" in lines
    assert "Total (USD): 12.50" in lines
    assert "Total (EUR): 4.00" in lines
    assert "Test Store" in lines
    assert "VAT: VAT123" in lines


def test_render_summary_text_no_sales():
    text = render_summary_text(
        start=datetime(2026, 7, 19, 0, 0),
        end=datetime(2026, 7, 20, 0, 0),
        sales_count=0,
        total_sats=0,
        totals_by_currency={},
    )

    assert "Sales: 0" in text
    assert "Total (sats): 0" in text


def test_render_summary_text_pt_br():
    text = render_summary_text(
        start=datetime(2026, 7, 19, 0, 0),
        end=datetime(2026, 7, 20, 0, 0),
        sales_count=2,
        total_sats=5000,
        totals_by_currency={"brl": 10.0},
        business_vat_id="123.456.789-00",
        lang="br",
    )
    lines = text.splitlines()

    assert lines[0] == "RESUMO DIÁRIO"
    assert "Vendas: 2" in lines
    assert "Total (BRL): 10.00" in lines
    assert "Obrigado!" in lines
    assert "Doc. Fiscal: 123.456.789-00" in lines


def test_render_summary_text_unknown_lang_falls_back_to_english():
    text = render_summary_text(
        start=datetime(2026, 7, 19, 0, 0),
        end=datetime(2026, 7, 20, 0, 0),
        sales_count=1,
        total_sats=100,
        totals_by_currency={},
        lang="xx",
    )

    assert text.splitlines()[0] == "DAILY SUMMARY"


def test_render_summary_text_locale_variants_normalize():
    # LNbits UI locales are forwarded verbatim; region suffixes and Portuguese
    # variants must still resolve to the right summary language.
    def title(lang):
        return render_summary_text(
            start=datetime(2026, 7, 19, 0, 0),
            end=datetime(2026, 7, 20, 0, 0),
            sales_count=0,
            total_sats=0,
            totals_by_currency={},
            lang=lang,
        ).splitlines()[0]

    for pt in ("br", "BR", "pt", "pt-BR", "pt_br"):
        assert title(pt) == "RESUMO DIÁRIO", pt
    for en in ("en", "en-US", None):
        assert title(en) == "DAILY SUMMARY", en
