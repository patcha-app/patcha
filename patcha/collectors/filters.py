_BANKING_DOMAINS = {
    "chase.com",
    "bankofamerica.com",
    "wellsfargo.com",
    "citibank.com",
    "citi.com",
    "usbank.com",
    "capitalone.com",
    "ally.com",
    "pnc.com",
    "tdbank.com",
    "regions.com",
    "suntrust.com",
    "bbt.com",
    "fidelity.com",
    "schwab.com",
    "vanguard.com",
    "etrade.com",
    "robinhood.com",
    "americanexpress.com",
    "amex.com",
    "discover.com",
    "synchrony.com",
    "paypal.com",
    "venmo.com",
    "cash.app",
    "zelle.com",
    "wise.com",
    "revolut.com",
    "stripe.com",
    "sbi.co.in",
    "hdfcbank.com",
    "icicibank.com",
    "axisbank.com",
    "kotak.com",
    "kotakbank.com",
    "yesbank.in",
    "indusind.com",
    "rblbank.com",
    "idfcfirstbank.com",
    "federalbank.co.in",
    "bankofbaroda.in",
    "pnbindia.in",
    "canarabank.in",
    "paytm.com",
    "phonepe.com",
    "razorpay.com",
    "coinbase.com",
    "binance.com",
    "kraken.com",
}

_INCOGNITO_MARKERS = ("incognito", "private browsing", "private window")


def is_banking_domain(text: str) -> bool:
    t = text.lower().lstrip("www.")
    return any(t == b or t.endswith("." + b) or b in t for b in _BANKING_DOMAINS)


def is_incognito_window(window_title: str) -> bool:
    t = window_title.lower()
    return any(m in t for m in _INCOGNITO_MARKERS)
