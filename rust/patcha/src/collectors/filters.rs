/// Returns true if the domain should be excluded from browser history collection.
pub fn is_banking_domain(domain: &str) -> bool {
    const BANKING_KEYWORDS: &[&str] = &[
        "bank",
        "chase",
        "wellsfargo",
        "citibank",
        "bofa",
        "bankofamerica",
        "capitalone",
        "discover",
        "paypal",
        "venmo",
        "cashapp",
        "zelle",
        "schwab",
        "fidelity",
        "vanguard",
        "robinhood",
        "etrade",
        "tdameritrade",
        "coinbase",
        "binance",
        "kraken",
    ];
    let d = domain.to_lowercase();
    BANKING_KEYWORDS.iter().any(|kw| d.contains(kw))
}

/// Returns true if the window title suggests an incognito/private session.
pub fn is_incognito_window(title: &str) -> bool {
    let t = title.to_lowercase();
    t.contains("incognito") || t.contains("private") || t.contains("inprivate")
}
