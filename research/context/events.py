"""Curated database of major market events with canonical date windows.

Each EventRecord holds the authoritative date range, a keyword list for
rule-based matching, and representative tickers for the event.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class EventRecord:
    label: str
    keywords: List[str]
    date_start: str          # YYYY-MM-DD
    date_end: str            # YYYY-MM-DD
    tickers: List[str]
    description: str
    sector: Optional[str] = None


EVENTS: Dict[str, EventRecord] = {
    # -----------------------------------------------------------------------
    # Mega-crashes
    # -----------------------------------------------------------------------
    "great_depression_1929": EventRecord(
        label="Wall Street Crash (1929)",
        keywords=["1929 crash", "black tuesday", "wall street crash 1929",
                  "great depression crash", "stock market crash 1929"],
        date_start="1929-10-24",
        date_end="1932-07-08",
        tickers=["GE", "GM", "XOM"],
        description="Dow fell 89 % peak to trough; triggered the Great Depression.",
        sector="broad market",
    ),
    "black_monday_1987": EventRecord(
        label="Black Monday (1987)",
        keywords=["black monday", "1987 crash", "october 1987", "dow crash 1987"],
        date_start="1987-10-14",
        date_end="1987-10-19",
        tickers=["SPY", "DIA", "GE", "IBM", "XOM"],
        description="Dow Jones fell 22.6 % in a single day — largest one-day drop in history.",
        sector="broad market",
    ),
    "dot_com_crash": EventRecord(
        label="Dot-com crash (2000–2002)",
        keywords=["dot-com", "dotcom", "tech bubble", "nasdaq crash 2000",
                  "internet bubble", "tech bubble burst", "2000 crash"],
        date_start="2000-03-10",
        date_end="2002-10-09",
        tickers=["MSFT", "INTC", "CSCO", "AMZN", "AAPL"],
        description="NASDAQ Composite fell 78 % from peak to trough.",
        sector="technology",
    ),
    "gfc_2008": EventRecord(
        label="Global Financial Crisis (2008–2009)",
        keywords=["financial crisis", "2008 crash", "lehman", "subprime",
                  "great recession", "credit crunch", "housing crisis", "gfc"],
        date_start="2007-10-09",
        date_end="2009-03-09",
        tickers=["GS", "JPM", "BAC", "C", "AIG", "WFC"],
        description="S&P 500 fell 57 % — worst bear market since the Great Depression.",
        sector="financials",
    ),
    "covid_crash": EventRecord(
        label="COVID-19 crash (2020)",
        keywords=["covid", "coronavirus", "pandemic crash", "march 2020",
                  "covid crash", "pandemic selloff", "lockdown crash"],
        date_start="2020-02-19",
        date_end="2020-03-23",
        tickers=["AAPL", "MSFT", "AMZN", "TSLA", "UAL", "CCL", "MGM"],
        description="S&P 500 fell 34 % in 33 days — fastest bear market in history.",
        sector="broad market",
    ),
    "bear_market_2022": EventRecord(
        label="2022 Bear Market",
        keywords=["2022 bear market", "rate hike 2022", "inflation crash 2022",
                  "tech selloff 2022", "growth crash 2022", "nasdaq 2022 bear"],
        date_start="2022-01-03",
        date_end="2022-10-13",
        tickers=["MSFT", "GOOGL", "AMZN", "META", "NVDA", "NFLX"],
        description="Fed rate-hike cycle triggered a growth-stock rout; NASDAQ fell 35 %.",
        sector="technology",
    ),

    # -----------------------------------------------------------------------
    # Flash crashes
    # -----------------------------------------------------------------------
    "flash_crash_2010": EventRecord(
        label="Flash Crash (May 2010)",
        keywords=["flash crash", "may 2010 crash", "flash crash 2010",
                  "dow 1000 points", "hft crash"],
        date_start="2010-05-06",
        date_end="2010-05-06",
        tickers=["SPY", "PG", "ACN", "MCD"],
        description="Dow dropped ~1,000 points intraday (9 %) in minutes before recovering.",
        sector="broad market",
    ),
    "yuan_devaluation_2015": EventRecord(
        label="China yuan devaluation (Aug 2015)",
        keywords=["yuan devaluation", "china currency 2015", "august 2015 crash",
                  "china devaluation", "rmb devaluation"],
        date_start="2015-08-10",
        date_end="2015-08-25",
        tickers=["BABA", "JD", "FXI", "SPY", "EEM"],
        description="PBoC surprise devaluation triggered a global equity sell-off.",
        sector="emerging markets",
    ),
    "volmageddon_2018": EventRecord(
        label="Volmageddon (Feb 2018)",
        keywords=["volmageddon", "vix spike 2018", "february 2018 crash",
                  "short vol blow-up", "xiv collapse"],
        date_start="2018-02-05",
        date_end="2018-02-09",
        tickers=["SPY", "VXX", "QQQ"],
        description="VIX doubled overnight; inverse-vol ETFs were wiped out.",
        sector="broad market",
    ),
    "china_circuit_breaker_2016": EventRecord(
        label="China circuit breaker crash (Jan 2016)",
        keywords=["china circuit breaker", "china market halt 2016",
                  "csi 300 circuit breaker", "china stock halt 2016"],
        date_start="2016-01-04",
        date_end="2016-01-07",
        tickers=["FXI", "BABA", "JD", "EEM", "SPY"],
        description="New CSI 300 circuit breakers halted Chinese markets twice in one week.",
        sector="emerging markets",
    ),

    # -----------------------------------------------------------------------
    # Fed / Macro events
    # -----------------------------------------------------------------------
    "fed_rate_shock_1994": EventRecord(
        label="Fed rate shock (1994)",
        keywords=["1994 bond crash", "fed 1994", "rate shock 1994",
                  "greenspan 1994", "bond market massacre"],
        date_start="1994-02-04",
        date_end="1994-11-15",
        tickers=["TLT", "GS", "JPM"],
        description="Fed doubled the funds rate in 12 months; bonds fell 20 %.",
        sector="fixed income",
    ),
    "taper_tantrum_2013": EventRecord(
        label="Taper tantrum (2013)",
        keywords=["taper tantrum", "bernanke taper", "2013 bond selloff",
                  "quantitative easing taper", "qe taper"],
        date_start="2013-05-22",
        date_end="2013-06-24",
        tickers=["TLT", "EEM", "IEF", "SPY"],
        description="Bernanke hints at tapering QE; 10-yr Treasury yield surged 100 bp.",
        sector="fixed income",
    ),
    "fed_hike_cycle_2022": EventRecord(
        label="Fed hike cycle (2022–2023)",
        keywords=["fed rate hikes 2022", "fomc 2022", "interest rate hikes",
                  "inflation fight 2022", "fed 75bp", "fed funds rate 2022"],
        date_start="2022-03-16",
        date_end="2023-07-26",
        tickers=["TLT", "XLF", "SPY", "QQQ"],
        description="Fed hiked 525 bp in 16 months to combat 40-year-high inflation.",
        sector="macro",
    ),
    "ltcm_russian_default_1998": EventRecord(
        label="LTCM collapse / Russian default (1998)",
        keywords=["ltcm", "long term capital management", "russian default 1998",
                  "russia bond default", "ltcm bailout", "hedge fund crisis 1998"],
        date_start="1998-08-17",
        date_end="1998-10-15",
        tickers=["GS", "JPM", "MS", "BAC", "SPY"],
        description="Russia defaulted; LTCM required a Fed-orchestrated $3.6 B bailout.",
        sector="macro",
    ),
    "uk_gilt_crisis_2022": EventRecord(
        label="UK gilt crisis (Sep–Oct 2022)",
        keywords=["uk gilt crisis", "liz truss mini budget", "gilt selloff 2022",
                  "uk pension crisis 2022", "kwarteng budget"],
        date_start="2022-09-23",
        date_end="2022-10-14",
        tickers=["EWU", "FXB", "GBP", "VGK"],
        description="Truss mini-budget triggered a gilt rout; BoE emergency bond purchases.",
        sector="macro",
    ),

    # -----------------------------------------------------------------------
    # Oil shocks
    # -----------------------------------------------------------------------
    "oil_embargo_1973": EventRecord(
        label="OPEC oil embargo (1973–1974)",
        keywords=["oil embargo", "opec 1973", "arab oil embargo", "oil crisis 1973",
                  "energy crisis 1973"],
        date_start="1973-10-16",
        date_end="1974-03-18",
        tickers=["XOM", "CVX", "HAL"],
        description="OPEC embargo caused oil prices to quadruple; stagflation followed.",
        sector="energy",
    ),
    "oil_crash_2014": EventRecord(
        label="Oil price crash (2014–2016)",
        keywords=["oil crash 2014", "crude oil collapse", "wti crash",
                  "opec 2014", "oil glut", "shale oil crash"],
        date_start="2014-06-20",
        date_end="2016-01-20",
        tickers=["XOM", "CVX", "OXY", "HAL", "USO"],
        description="Brent crude fell from $115 to $27 on supply glut + OPEC war.",
        sector="energy",
    ),
    "negative_oil_2020": EventRecord(
        label="Negative oil prices (Apr 2020)",
        keywords=["negative oil", "oil negative", "wti negative", "april 2020 oil",
                  "oil below zero", "crude negative"],
        date_start="2020-04-20",
        date_end="2020-04-21",
        tickers=["USO", "XOM", "CVX", "OXY"],
        description="WTI crude briefly traded at −$37.63/barrel — storage capacity exhausted.",
        sector="energy",
    ),

    # -----------------------------------------------------------------------
    # Geopolitical events
    # -----------------------------------------------------------------------
    "nine_eleven": EventRecord(
        label="September 11 attacks (2001)",
        keywords=["9/11", "september 11", "9-11", "sept 11 2001",
                  "world trade center attack", "september 11 attack"],
        date_start="2001-09-10",
        date_end="2001-09-21",
        tickers=["UAL", "AAL", "BA", "LMT", "SPY"],
        description="NYSE closed 4 days; markets fell ~14 % on reopening week.",
        sector="broad market",
    ),
    "russia_ukraine_2022": EventRecord(
        label="Russia–Ukraine war (2022)",
        keywords=["russia ukraine", "ukraine war", "russia invasion", "ukraine invasion",
                  "kyiv attack", "russia sanctions 2022", "nato ukraine"],
        date_start="2022-02-24",
        date_end="2022-03-08",
        tickers=["URTH", "XOM", "LMT", "RTX", "XLE"],
        description="Russian invasion sparked commodity surge and European equity sell-off.",
        sector="broad market",
    ),
    "brexit_referendum": EventRecord(
        label="Brexit referendum (Jun 2016)",
        keywords=["brexit", "uk leave eu", "brexit vote", "eu referendum",
                  "britain eu", "leave vote 2016"],
        date_start="2016-06-24",
        date_end="2016-06-27",
        tickers=["GBP", "EWU", "SPY", "VGK"],
        description="GBP fell 10 % in hours; FTSE 100 dropped 8 % at open.",
        sector="forex / international",
    ),
    "gulf_war_1991": EventRecord(
        label="Gulf War rally (1991)",
        keywords=["gulf war 1991", "operation desert storm", "iraq war 1991",
                  "gulf war stock rally", "desert storm market"],
        date_start="1991-01-17",
        date_end="1991-02-28",
        tickers=["BA", "LMT", "RTX", "XOM", "SPY"],
        description="Markets rallied 18 % during Desert Storm as the war ended quickly.",
        sector="broad market",
    ),
    "us_election_2016": EventRecord(
        label="Trump election night rally (Nov 2016)",
        keywords=["trump election 2016", "election night 2016", "trump win 2016",
                  "november 2016 election", "trump stock rally"],
        date_start="2016-11-08",
        date_end="2016-11-14",
        tickers=["SPY", "XLF", "GS", "JPM", "BAC"],
        description="Surprise Trump win triggered a bank/infrastructure rally; futures reversed from -5% to +1%.",
        sector="broad market",
    ),

    # -----------------------------------------------------------------------
    # Single-stock / sector earnings shocks
    # -----------------------------------------------------------------------
    "nflx_subscriber_miss_2022": EventRecord(
        label="Netflix subscriber miss (Q1 2022)",
        keywords=["netflix subscriber miss", "nflx q1 2022", "netflix earnings 2022",
                  "netflix losing subscribers", "netflix crash 2022"],
        date_start="2022-04-19",
        date_end="2022-04-20",
        tickers=["NFLX"],
        description="NFLX fell 35 % after disclosing first subscriber loss in a decade.",
        sector="communication services",
    ),
    "meta_earnings_q3_2022": EventRecord(
        label="Meta earnings crash (Q3 2022)",
        keywords=["meta earnings crash", "facebook crash 2022", "meta q3 2022",
                  "zuckerberg metaverse", "meta losses"],
        date_start="2022-10-26",
        date_end="2022-10-27",
        tickers=["META"],
        description="META fell 25 % on metaverse losses and declining ad revenue.",
        sector="technology",
    ),
    "svb_collapse_2023": EventRecord(
        label="SVB collapse (Mar 2023)",
        keywords=["svb collapse", "silicon valley bank", "svb failure",
                  "bank run 2023", "svb bank run", "regional bank crisis 2023"],
        date_start="2023-03-08",
        date_end="2023-03-17",
        tickers=["SIVB", "FRC", "SBNY", "KRE", "XLF"],
        description="SVB collapsed in 48 hours; triggered regional banking contagion fears.",
        sector="financials",
    ),
    "nvidia_ai_boom_2023": EventRecord(
        label="Nvidia AI earnings boom (2023)",
        keywords=["nvidia earnings", "nvda ai", "nvidia ai boom", "nvidia q2 2023",
                  "nvidia data center", "ai chip boom"],
        date_start="2023-05-24",
        date_end="2023-05-26",
        tickers=["NVDA", "AMD", "INTC", "SMCI", "AMAT"],
        description="NVDA surged 24 % after data-center revenue guidance shocked to the upside.",
        sector="technology",
    ),
    "gamestop_short_squeeze_2021": EventRecord(
        label="GameStop short squeeze (Jan 2021)",
        keywords=["gamestop", "gme short squeeze", "reddit wsb", "wallstreetbets",
                  "meme stock", "robinhood gamestop"],
        date_start="2021-01-11",
        date_end="2021-01-29",
        tickers=["GME", "AMC", "BB", "NOK"],
        description="GME rose 1,700 % in weeks driven by retail co-ordination on Reddit.",
        sector="retail",
    ),
    "enron_worldcom_collapse": EventRecord(
        label="Enron/WorldCom accounting scandals (2001–2002)",
        keywords=["enron collapse", "worldcom fraud", "enron bankruptcy",
                  "worldcom bankruptcy", "accounting scandal 2002", "arthur andersen"],
        date_start="2001-10-16",
        date_end="2002-07-21",
        tickers=["ENE", "WCOM", "XLK", "SPY"],
        description="Enron and WorldCom collapsed in the largest bankruptcies in US history at the time.",
        sector="broad market",
    ),
    "nvidia_deepseek_recovery_2025": EventRecord(
        label="AI recovery rally (Feb–Mar 2025)",
        keywords=["nvidia recovery 2025", "ai rebound 2025", "nvda recovery",
                  "ai stocks recover 2025", "deepseek recovery"],
        date_start="2025-02-03",
        date_end="2025-03-28",
        tickers=["NVDA", "MSFT", "GOOGL", "AMD", "META"],
        description="AI stocks rebounded after the DeepSeek shock as earnings confirmed AI capex growth.",
        sector="technology",
    ),

    # -----------------------------------------------------------------------
    # Crypto contagion (relevant if user has crypto-adjacent stocks)
    # -----------------------------------------------------------------------
    "ftx_collapse_2022": EventRecord(
        label="FTX collapse (Nov 2022)",
        keywords=["ftx collapse", "ftx bankruptcy", "sam bankman-fried", "ftx fraud",
                  "crypto crash 2022", "crypto exchange collapse"],
        date_start="2022-11-07",
        date_end="2022-11-14",
        tickers=["COIN", "MSTR", "MARA", "RIOT"],
        description="FTX filed for bankruptcy; crypto and related equities cratered.",
        sector="technology / crypto",
    ),

    # -----------------------------------------------------------------------
    # Pandemic / biotech events
    # -----------------------------------------------------------------------
    "covid_recovery_2020": EventRecord(
        label="COVID market recovery (Apr–Dec 2020)",
        keywords=["covid recovery 2020", "v-shaped recovery", "pandemic recovery rally",
                  "fed stimulus 2020", "cares act rally"],
        date_start="2020-03-23",
        date_end="2020-12-31",
        tickers=["AAPL", "MSFT", "AMZN", "TSLA", "NVDA", "SPY"],
        description="S&P 500 recovered 100 % from COVID trough by year-end on fiscal and monetary stimulus.",
        sector="broad market",
    ),
    "covid_vaccine_pfizer": EventRecord(
        label="Pfizer vaccine announcement (Nov 2020)",
        keywords=["pfizer vaccine", "biontech vaccine", "covid vaccine november 2020",
                  "vaccine approval 2020", "pfizer 90% efficacy"],
        date_start="2020-11-09",
        date_end="2020-11-10",
        tickers=["PFE", "BNTX", "UAL", "CCL", "DAL", "XLV"],
        description="PFE/BNTX 90 % efficacy news triggered a rotation from tech to cyclicals.",
        sector="healthcare",
    ),

    # -----------------------------------------------------------------------
    # Sovereign debt crises
    # -----------------------------------------------------------------------
    "eurozone_debt_crisis": EventRecord(
        label="Eurozone debt crisis (2010–2012)",
        keywords=["eurozone crisis", "greek debt", "greece bailout", "eu debt crisis",
                  "piigs crisis", "sovereign debt europe", "ecb draghi whatever it takes"],
        date_start="2010-04-23",
        date_end="2012-09-06",
        tickers=["EWG", "EWI", "EWP", "VGK", "EEM"],
        description="Greek/peripheral debt crisis threatened euro zone integrity until Draghi pledge.",
        sector="international",
    ),

    # -----------------------------------------------------------------------
    # Asian financial crisis
    # -----------------------------------------------------------------------
    "asian_financial_crisis_1997": EventRecord(
        label="Asian financial crisis (1997–1998)",
        keywords=["asian financial crisis", "thai baht crisis", "asia crisis 1997",
                  "asian currency crisis", "korea imf 1997", "indonesia crisis 1997"],
        date_start="1997-07-02",
        date_end="1998-08-17",
        tickers=["EEM", "FXI", "SPY", "GS", "JPM"],
        description="Thai baht devaluation triggered currency and equity collapses across Southeast Asia.",
        sector="emerging markets",
    ),

    # -----------------------------------------------------------------------
    # Trade war
    # -----------------------------------------------------------------------
    "us_china_trade_war": EventRecord(
        label="US–China trade war (2018–2019)",
        keywords=["trade war", "us china tariffs", "trump tariffs", "trade war 2018",
                  "china tariffs 2019", "trade deal phase one"],
        date_start="2018-03-22",
        date_end="2020-01-15",
        tickers=["AAPL", "BABA", "CAT", "BA", "SPY"],
        description="Tit-for-tat tariffs created volatility across tech, industrials, and ag.",
        sector="broad market",
    ),

    # -----------------------------------------------------------------------
    # SVB / banking follow-on
    # -----------------------------------------------------------------------
    "regional_bank_crisis_followon_2023": EventRecord(
        label="Regional banking contagion (Mar–May 2023)",
        keywords=["first republic bank", "frb collapse", "pac west 2023",
                  "western alliance 2023", "regional bank 2023", "bank contagion 2023"],
        date_start="2023-03-17",
        date_end="2023-05-05",
        tickers=["KRE", "FRC", "PACW", "WAL", "XLF"],
        description="SVB contagion continued: First Republic and PacWest also failed or were seized.",
        sector="financials",
    ),

    # -----------------------------------------------------------------------
    # Recent AI / macro
    # -----------------------------------------------------------------------
    "ai_capex_boom_2023": EventRecord(
        label="AI capex boom / NASDAQ rally (H2 2023)",
        keywords=["ai rally 2023", "nasdaq rally 2023", "magnificent seven 2023",
                  "ai capex 2023", "chatgpt effect stocks", "llm boom stocks"],
        date_start="2023-05-26",
        date_end="2023-12-29",
        tickers=["NVDA", "MSFT", "META", "GOOGL", "AMZN", "AAPL", "TSLA"],
        description="AI investment surge drove the 'Magnificent Seven' and a 40 % NASDAQ rally in H2 2023.",
        sector="technology",
    ),
    "deepseek_ai_shock_2025": EventRecord(
        label="DeepSeek AI shock (Jan 2025)",
        keywords=["deepseek", "deepseek r1", "ai cheap model", "deepseek shock",
                  "nvidia crash 2025", "deepseek china ai"],
        date_start="2025-01-27",
        date_end="2025-01-28",
        tickers=["NVDA", "AMD", "MSFT", "GOOGL", "AMZN"],
        description="Chinese open-weight model DeepSeek-R1 triggered a $600 B NVDA market cap loss.",
        sector="technology",
    ),
    "us_tariff_shock_2025": EventRecord(
        label="Liberation Day tariff shock (Apr 2025)",
        keywords=["liberation day", "trump tariffs 2025", "reciprocal tariffs",
                  "april 2025 tariffs", "trade war 2025"],
        date_start="2025-04-02",
        date_end="2025-04-09",
        tickers=["SPY", "QQQ", "AAPL", "AMZN", "TSLA"],
        description="Broad reciprocal tariffs announcement caused a multi-day global selloff.",
        sector="broad market",
    ),
    "us_tariff_pause_2025": EventRecord(
        label="Tariff pause rally (Apr 2025)",
        keywords=["tariff pause", "trump tariff pause 2025", "90 day tariff pause",
                  "trade war pause 2025", "april tariff pause"],
        date_start="2025-04-09",
        date_end="2025-04-11",
        tickers=["SPY", "QQQ", "AAPL", "AMZN", "NVDA"],
        description="90-day tariff pause announcement triggered S&P 500's largest single-day gain since 2020.",
        sector="broad market",
    ),
}


def search_events(text: str) -> list[tuple[str, EventRecord, int]]:
    """Return (key, EventRecord, match_count) for events whose keywords appear in text.

    Results are sorted by match_count descending so the best match is first.
    """
    text_lower = text.lower()
    matches: list[tuple[str, EventRecord, int]] = []
    for key, record in EVENTS.items():
        count = sum(1 for kw in record.keywords if kw in text_lower)
        if count > 0:
            matches.append((key, record, count))
    return sorted(matches, key=lambda x: x[2], reverse=True)
