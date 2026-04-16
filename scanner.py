"""
Nifty 100 — 5-Strategy Daily Scanner
======================================
Strategy 1: RSI Mean Reversion      — RSI < 30 daily + weekly, low debt, good PE
Strategy 2: Golden Cross             — 50MA crosses above 200MA + volume surge
Strategy 3: MACD Bullish Crossover  — MACD line crosses signal line, histogram turns +ve
Strategy 4: 52-Week Low Reversal    — Near 52W low, high ROE, positive cash flow
Strategy 5: Quality Momentum        — High ROE, price near 52W high, RSI 50–70

Confluence: stocks appearing in 2+ strategies = HIGH CONVICTION alerts
Free AI:    Groq (primary) → Gemini (fallback) → plain text
Email:      Gmail SMTP, HTML report at 6:30 AM IST via GitHub Actions
"""

import os, json, smtplib, datetime, urllib.request
import pandas as pd
import yfinance as yf
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── CONFIG ────────────────────────────────────────────────────────────────────
GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_PASS     = os.environ["GMAIL_PASS"]
TO_EMAIL       = os.environ["TO_EMAIL"]
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
HISTORY_FILE   = "flagged_history.json"

NIFTY100 = [
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","BHARTIARTL.NS","ICICIBANK.NS",
    "INFOSYS.NS","SBIN.NS","HINDUNILVR.NS","ITC.NS","LT.NS",
    "KOTAKBANK.NS","AXISBANK.NS","BAJFINANCE.NS","ASIANPAINT.NS","MARUTI.NS",
    "HCLTECH.NS","SUNPHARMA.NS","TITAN.NS","ULTRACEMCO.NS","WIPRO.NS",
    "ONGC.NS","POWERGRID.NS","NTPC.NS","COALINDIA.NS","BAJAJFINSV.NS",
    "ADANIENT.NS","ADANIPORTS.NS","JSWSTEEL.NS","TATASTEEL.NS","HINDALCO.NS",
    "TECHM.NS","DRREDDY.NS","CIPLA.NS","DIVISLAB.NS","APOLLOHOSP.NS",
    "EICHERMOT.NS","BAJAJ-AUTO.NS","HEROMOTOCO.NS","M&M.NS","TATACONSUM.NS",
    "BRITANNIA.NS","NESTLEIND.NS","DABUR.NS","GODREJCP.NS","COLPAL.NS",
    "PIDILITIND.NS","BERGEPAINT.NS","HAVELLS.NS","VOLTAS.NS","MUTHOOTFIN.NS",
    "INDUSINDBK.NS","BANDHANBNK.NS","PFC.NS","RECLTD.NS","IRFC.NS",
    "TRENT.NS","ZOMATO.NS","NYKAA.NS","DMART.NS","SIEMENS.NS",
    "ABB.NS","BOSCHLTD.NS","CUMMINSIND.NS","GRASIM.NS","AMBUJACEM.NS",
    "ACCLTD.NS","SHREECEM.NS","VEDL.NS","NMDC.NS","SAIL.NS",
    "BPCL.NS","IOC.NS","HINDPETRO.NS","GAIL.NS","TORNTPHARM.NS",
    "LUPIN.NS","AUROPHARMA.NS","BIOCON.NS","ALKEM.NS","HDFCLIFE.NS",
    "SBILIFE.NS","ICICIGI.NS","BAJAJHLDNG.NS","CHOLAFIN.NS","LICHSGFIN.NS",
    "NAUKRI.NS","PERSISTENT.NS","LTIM.NS","COFORGE.NS","MPHASIS.NS",
    "OFSS.NS","KPITTECH.NS","TATAPOWER.NS","ADANIGREEN.NS","ADANITRANS.NS",
]

# ── HELPERS ───────────────────────────────────────────────────────────────────
def rsi(s: pd.Series, n=14) -> float:
    d = s.diff(); g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return round(float(100 - 100 / (1 + g / l)).iloc[-1], 1)

def fetch(ticker: str):
    """Return (tk, info, hist_d, hist_w) or None on failure."""
    try:
        tk     = yf.Ticker(ticker)
        info   = tk.info
        hist_d = tk.history(period="1y",  interval="1d")
        hist_w = tk.history(period="3y",  interval="1wk")
        if len(hist_d) < 60 or len(hist_w) < 40:
            return None
        return tk, info, hist_d, hist_w
    except Exception as e:
        print(f"    fetch error {ticker}: {e}")
        return None

def safe(info, *keys):
    for k in keys:
        v = info.get(k)
        if v is not None and v != 0:
            return v
    return None

def base_filters(info) -> bool:
    """Large cap + low debt — required for every strategy."""
    mcap = info.get("marketCap", 0)
    de   = info.get("debtToEquity")
    if mcap < 20_000_000_000:   return False   # must be large cap
    if de is None or de > 50:   return False   # D/E < 0.5x
    return True

# ── STRATEGY 1: RSI MEAN REVERSION ───────────────────────────────────────────
def s1_rsi_reversion(ticker, info, hist_d, hist_w) -> dict | None:
    if not base_filters(info): return None
    pe = safe(info, "trailingPE", "forwardPE")
    if pe is None or pe <= 0 or pe > 35: return None

    rd = rsi(hist_d["Close"])
    rw = rsi(hist_w["Close"])
    if rd >= 30 or rw >= 30: return None

    price  = round(float(hist_d["Close"].iloc[-1]), 2)
    chg1w  = round((price / float(hist_d["Close"].iloc[-6])  - 1) * 100, 2) if len(hist_d) >= 6  else None
    chg1m  = round((price / float(hist_d["Close"].iloc[-22]) - 1) * 100, 2) if len(hist_d) >= 22 else None
    return dict(ticker=ticker.replace(".NS",""), name=info.get("longName",""),
                price=price, pe=round(pe,1),
                de=round(info.get("debtToEquity",0)/100,2),
                rsi_d=rd, rsi_w=rw, chg_1w=chg1w, chg_1m=chg1m,
                strategy="S1 RSI Reversion", signal_strength="Strong" if rd < 20 else "Moderate")

# ── STRATEGY 2: GOLDEN CROSS ──────────────────────────────────────────────────
def s2_golden_cross(ticker, info, hist_d, hist_w) -> dict | None:
    if not base_filters(info): return None
    close  = hist_d["Close"]
    volume = hist_d["Volume"]
    if len(close) < 210: return None

    ma50  = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()

    # Golden cross: today 50MA > 200MA AND yesterday 50MA <= 200MA (fresh cross)
    if not (ma50.iloc[-1] > ma200.iloc[-1] and ma50.iloc[-2] <= ma200.iloc[-2]):
        return None
    # Confirm with volume: today's volume > 1.5x 20-day avg
    avg_vol = volume.iloc[-21:-1].mean()
    if volume.iloc[-1] < avg_vol * 1.3: return None

    price = round(float(close.iloc[-1]), 2)
    rd    = rsi(close)
    pct_above_200 = round((float(ma50.iloc[-1]) / float(ma200.iloc[-1]) - 1) * 100, 2)
    return dict(ticker=ticker.replace(".NS",""), name=info.get("longName",""),
                price=price, ma50=round(float(ma50.iloc[-1]),2),
                ma200=round(float(ma200.iloc[-1]),2),
                pct_above_200=pct_above_200,
                vol_ratio=round(float(volume.iloc[-1]/avg_vol),2),
                rsi_d=rd,
                strategy="S2 Golden Cross", signal_strength="Strong")

# ── STRATEGY 3: MACD BULLISH CROSSOVER ───────────────────────────────────────
def s3_macd_crossover(ticker, info, hist_d, hist_w) -> dict | None:
    if not base_filters(info): return None
    close = hist_d["Close"]
    if len(close) < 50: return None

    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist   = macd - signal

    # Fresh crossover: today MACD > signal AND yesterday MACD <= signal
    if not (macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-2] <= signal.iloc[-2]):
        return None
    # Histogram must have turned positive
    if hist.iloc[-1] <= 0: return None
    # Avoid overbought entries
    rd = rsi(close)
    if rd > 65: return None

    price = round(float(close.iloc[-1]), 2)
    pe    = safe(info, "trailingPE", "forwardPE")
    return dict(ticker=ticker.replace(".NS",""), name=info.get("longName",""),
                price=price, macd=round(float(macd.iloc[-1]),4),
                signal_line=round(float(signal.iloc[-1]),4),
                histogram=round(float(hist.iloc[-1]),4),
                rsi_d=rd,
                pe=round(pe,1) if pe else None,
                strategy="S3 MACD Crossover", signal_strength="Moderate")

# ── STRATEGY 4: 52-WEEK LOW REVERSAL ─────────────────────────────────────────
def s4_52w_low_reversal(ticker, info, hist_d, hist_w) -> dict | None:
    if not base_filters(info): return None

    roe  = info.get("returnOnEquity")        # decimal, e.g. 0.18 = 18%
    ocf  = info.get("operatingCashflow", 0)

    if roe is None or roe < 0.12:  return None   # ROE > 12%
    if ocf <= 0:                   return None   # must be cash-flow positive

    close = hist_d["Close"]
    low52 = float(close.rolling(252).min().iloc[-1])
    price = float(close.iloc[-1])

    # Within 10% of 52-week low but bouncing (today > yesterday)
    if price > low52 * 1.10: return None
    if price <= float(close.iloc[-2]): return None  # must show bounce

    rd = rsi(close)
    if rd >= 40: return None  # still must be in oversold/depressed zone

    pe = safe(info, "trailingPE", "forwardPE")
    pct_from_low = round((price / low52 - 1) * 100, 2)
    return dict(ticker=ticker.replace(".NS",""), name=info.get("longName",""),
                price=round(price,2), low_52w=round(low52,2),
                pct_from_low=pct_from_low,
                roe=round(roe*100,1), rsi_d=rd,
                pe=round(pe,1) if pe else None,
                strategy="S4 52W Low Reversal", signal_strength="Strong" if pct_from_low < 3 else "Moderate")

# ── STRATEGY 5: QUALITY MOMENTUM ─────────────────────────────────────────────
def s5_quality_momentum(ticker, info, hist_d, hist_w) -> dict | None:
    if not base_filters(info): return None

    roe  = info.get("returnOnEquity")
    fcf  = info.get("freeCashflow", 0)
    if roe is None or roe < 0.18: return None    # ROE > 18%
    if fcf  is None or fcf <= 0:  return None    # positive FCF

    close  = hist_d["Close"]
    high52 = float(close.rolling(252).max().iloc[-1])
    price  = float(close.iloc[-1])

    # Price within 10% of 52-week high (momentum)
    if price < high52 * 0.90: return None

    rd = rsi(close)
    if rd < 50 or rd > 70: return None  # confirmed momentum, not overbought

    # Also require uptrend: price > 50MA > 200MA
    ma50  = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])
    if not (price > ma50 > ma200): return None

    pe = safe(info, "trailingPE", "forwardPE")
    pct_from_high = round((price / high52 - 1) * 100, 2)
    return dict(ticker=ticker.replace(".NS",""), name=info.get("longName",""),
                price=round(price,2), high_52w=round(high52,2),
                pct_from_high=pct_from_high,
                roe=round(roe*100,1), rsi_d=rd,
                pe=round(pe,1) if pe else None,
                strategy="S5 Quality Momentum", signal_strength="Strong" if rd < 65 else "Moderate")

# ── MAIN SCAN ─────────────────────────────────────────────────────────────────
def scan_all() -> dict:
    results = {f"s{i}": [] for i in range(1,6)}
    print(f"Scanning {len(NIFTY100)} stocks across 5 strategies …\n")
    for t in NIFTY100:
        print(f"  {t} …", end=" ", flush=True)
        data = fetch(t)
        if data is None:
            print("skip")
            continue
        tk, info, hist_d, hist_w = data
        hits = []
        for fn, key in [(s1_rsi_reversion,"s1"),(s2_golden_cross,"s2"),
                        (s3_macd_crossover,"s3"),(s4_52w_low_reversal,"s4"),
                        (s5_quality_momentum,"s5")]:
            try:
                r = fn(t, info, hist_d, hist_w)
                if r:
                    results[key].append(r)
                    hits.append(key.upper())
            except Exception as e:
                pass
        print(f"{'✅ ' + '+'.join(hits) if hits else 'skip'}")
    return results

# ── CONFLUENCE DETECTION ───────────────────────────────────────────────────────
def find_confluence(results: dict) -> list[dict]:
    """Find tickers appearing in 2+ strategies."""
    from collections import defaultdict
    ticker_hits = defaultdict(list)
    for key, stocks in results.items():
        for s in stocks:
            ticker_hits[s["ticker"]].append(s["strategy"])
    return [
        {"ticker": t, "strategies": strats, "count": len(strats)}
        for t, strats in ticker_hits.items() if len(strats) >= 2
    ]

# ── HISTORY ───────────────────────────────────────────────────────────────────
def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE) as f: return json.load(f)
    return {}

def save_history(h: dict):
    with open(HISTORY_FILE, "w") as f: json.dump(h, f, indent=2)

def update_history(results: dict, history: dict) -> dict:
    today = datetime.date.today().isoformat()
    all_flagged = [s for key in results for s in results[key]]
    seen = set()
    for s in all_flagged:
        k = s["ticker"]
        if k not in history and k not in seen:
            history[k] = {"flagged_date": today, "entry_price": s["price"],
                          "name": s.get("name",""), "strategy": s["strategy"]}
            seen.add(k)
    return history

def build_followup(history: dict) -> list[dict]:
    followup = []
    today = datetime.date.today()
    for ticker, meta in history.items():
        try:
            price = float(yf.Ticker(ticker+".NS").history(period="5d")["Close"].iloc[-1])
            days  = (today - datetime.date.fromisoformat(meta["flagged_date"])).days
            ret   = round((price / meta["entry_price"] - 1) * 100, 2)
            followup.append({**meta, "ticker": ticker,
                             "current_price": round(price,2),
                             "return_pct": ret, "days_held": days})
        except: pass
    return sorted(followup, key=lambda x: x["return_pct"], reverse=True)

# ── FREE AI ───────────────────────────────────────────────────────────────────
def _prompt(results, confluence, followup):
    totals = {k: len(v) for k,v in results.items()}
    conf_txt = "\n".join(f"  {c['ticker']}: {', '.join(c['strategies'])}" for c in confluence) or "None today"
    fu_txt   = "\n".join(
        f"  {f['ticker']} ({f['strategy']}) — entry Rs{f['entry_price']} → Rs{f['current_price']} "
        f"({'+' if f['return_pct']>0 else ''}{f['return_pct']}% in {f['days_held']} days)"
        for f in followup[:8]) or "No follow-up data yet"

    s1 = "\n".join(f"  {s['ticker']} RSI-D:{s['rsi_d']} RSI-W:{s['rsi_w']} PE:{s['pe']}" for s in results["s1"]) or "None"
    s2 = "\n".join(f"  {s['ticker']} vol_ratio:{s['vol_ratio']}x RSI:{s['rsi_d']}" for s in results["s2"]) or "None"
    s3 = "\n".join(f"  {s['ticker']} MACD:{s['macd']} hist:{s['histogram']} RSI:{s['rsi_d']}" for s in results["s3"]) or "None"
    s4 = "\n".join(f"  {s['ticker']} {s['pct_from_low']}% above 52W-low ROE:{s['roe']}% RSI:{s['rsi_d']}" for s in results["s4"]) or "None"
    s5 = "\n".join(f"  {s['ticker']} {s['pct_from_high']}% from 52W-high ROE:{s['roe']}% RSI:{s['rsi_d']}" for s in results["s5"]) or "None"

    return f"""You are a sharp Indian equity analyst. Summarise today's multi-strategy Nifty 100 scanner results in max 400 words.

SIGNAL COUNTS: S1={totals['s1']} S2={totals['s2']} S3={totals['s3']} S4={totals['s4']} S5={totals['s5']}

HIGH-CONVICTION CONFLUENCE (2+ strategies):
{conf_txt}

S1 RSI REVERSION (oversold quality):
{s1}
S2 GOLDEN CROSS (trend starts):
{s2}
S3 MACD CROSSOVER (momentum shift):
{s3}
S4 52W LOW REVERSAL (contrarian value):
{s4}
S5 QUALITY MOMENTUM (strong companies):
{s5}

FOLLOW-UP ON PAST PICKS:
{fu_txt}

Write:
1. HEADLINE — one sentence capturing today's market message
2. TOP PICKS — focus on confluence stocks first, then strongest individual signals. Give specific reason for each.
3. PAST PICKS UPDATE — highlight best and worst performers with brief reason
4. RISK WATCH — one specific risk or caution for today

Use Rs for prices. Be specific, not generic. No boilerplate disclaimers."""

def _groq(p):
    import json as J
    body = J.dumps({"model":"llama-3.3-70b-versatile","messages":[{"role":"user","content":p}],"max_tokens":1000,"temperature":0.7}).encode()
    req  = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions",data=body,
            headers={"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"})
    return J.loads(urllib.request.urlopen(req,timeout=30).read())["choices"][0]["message"]["content"].strip()

def _gemini(p):
    import json as J
    body = J.dumps({"contents":[{"parts":[{"text":p}]}],"generationConfig":{"maxOutputTokens":1000}}).encode()
    url  = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    req  = urllib.request.Request(url,data=body,headers={"Content-Type":"application/json"})
    return J.loads(urllib.request.urlopen(req,timeout=30).read())["candidates"][0]["content"]["parts"][0]["text"].strip()

def _fallback(results, confluence, followup):
    lines = ["Multi-Strategy Scanner — Plain Text Summary\n"]
    for label, key in [("S1 RSI Reversion","s1"),("S2 Golden Cross","s2"),
                       ("S3 MACD Crossover","s3"),("S4 52W Low Reversal","s4"),("S5 Quality Momentum","s5")]:
        lines.append(f"{label}: {len(results[key])} signals")
        for s in results[key]: lines.append(f"  • {s['ticker']} @ Rs{s['price']}")
    if confluence:
        lines.append("\nHIGH CONVICTION (2+ strategies):")
        for c in confluence: lines.append(f"  *** {c['ticker']}: {', '.join(c['strategies'])}")
    if followup:
        lines.append("\nFollow-up:")
        for f in followup[:5]:
            sign = "+" if f["return_pct"]>0 else ""
            lines.append(f"  {f['ticker']}: {sign}{f['return_pct']}% in {f['days_held']} days")
    lines.append("\nTip: Add GROQ_API_KEY or GEMINI_API_KEY secret for AI analysis (both free).")
    return "\n".join(lines)

def ai_analysis(results, confluence, followup):
    p = _prompt(results, confluence, followup)
    if GROQ_API_KEY:
        try: print("  Groq …"); return _groq(p)
        except Exception as e: print(f"  Groq failed: {e}")
    if GEMINI_API_KEY:
        try: print("  Gemini …"); return _gemini(p)
        except Exception as e: print(f"  Gemini failed: {e}")
    return _fallback(results, confluence, followup)

# ── HTML EMAIL ────────────────────────────────────────────────────────────────
def _tbl_rows_s1(stocks):
    if not stocks: return "<tr><td colspan='8' style='text-align:center;color:#888'>No signals today</td></tr>"
    rows=""
    for s in stocks:
        bg="#d4edda" if s["rsi_d"]<20 else "#fff8e1"
        rows+=f"<tr style='background:{bg}'><td><b>{s['ticker']}</b></td><td>Rs{s['price']}</td><td>{s['pe']}</td><td>{s['de']}</td><td style='color:#c0392b'><b>{s['rsi_d']}</b></td><td style='color:#c0392b'><b>{s['rsi_w']}</b></td><td>{s['chg_1w']}%</td><td>{s.get('signal_strength','')}</td></tr>"
    return rows

def _tbl_rows_s2(stocks):
    if not stocks: return "<tr><td colspan='6' style='text-align:center;color:#888'>No signals today</td></tr>"
    rows=""
    for s in stocks:
        rows+=f"<tr><td><b>{s['ticker']}</b></td><td>Rs{s['price']}</td><td>{s['ma50']}</td><td>{s['ma200']}</td><td>{s['vol_ratio']}x</td><td style='color:#27ae60'><b>{s['rsi_d']}</b></td></tr>"
    return rows

def _tbl_rows_s3(stocks):
    if not stocks: return "<tr><td colspan='6' style='text-align:center;color:#888'>No signals today</td></tr>"
    rows=""
    for s in stocks:
        rows+=f"<tr><td><b>{s['ticker']}</b></td><td>Rs{s['price']}</td><td>{s['macd']}</td><td>{s['histogram']}</td><td>{s['rsi_d']}</td><td>{s.get('pe','—')}</td></tr>"
    return rows

def _tbl_rows_s4(stocks):
    if not stocks: return "<tr><td colspan='7' style='text-align:center;color:#888'>No signals today</td></tr>"
    rows=""
    for s in stocks:
        rows+=f"<tr style='background:#fff3cd'><td><b>{s['ticker']}</b></td><td>Rs{s['price']}</td><td>Rs{s['low_52w']}</td><td>+{s['pct_from_low']}%</td><td>{s['roe']}%</td><td>{s['rsi_d']}</td><td>{s.get('pe','—')}</td></tr>"
    return rows

def _tbl_rows_s5(stocks):
    if not stocks: return "<tr><td colspan='7' style='text-align:center;color:#888'>No signals today</td></tr>"
    rows=""
    for s in stocks:
        rows+=f"<tr style='background:#d4edda'><td><b>{s['ticker']}</b></td><td>Rs{s['price']}</td><td>Rs{s['high_52w']}</td><td>{s['pct_from_high']}%</td><td>{s['roe']}%</td><td>{s['rsi_d']}</td><td>{s.get('pe','—')}</td></tr>"
    return rows

def _confluence_rows(conf):
    if not conf: return "<tr><td colspan='3' style='text-align:center;color:#888'>No confluence today</td></tr>"
    rows=""
    for c in sorted(conf, key=lambda x: -x["count"]):
        badge = "🔥" * c["count"]
        rows+=f"<tr style='background:#fce4ec'><td><b>{badge} {c['ticker']}</b></td><td>{c['count']} strategies</td><td>{', '.join(c['strategies'])}</td></tr>"
    return rows

def _followup_rows(fu):
    if not fu: return "<tr><td colspan='6' style='text-align:center;color:#888'>No history yet</td></tr>"
    rows=""
    for f in fu:
        bg="#d4edda" if f["return_pct"]>0 else "#fde8e8"
        sign="+" if f["return_pct"]>0 else ""
        rows+=f"<tr style='background:{bg}'><td><b>{f['ticker']}</b></td><td>{f['flagged_date']}</td><td>{f.get('strategy','')}</td><td>Rs{f['entry_price']}</td><td>Rs{f['current_price']}</td><td><b>{sign}{f['return_pct']}%</b> ({f['days_held']}d)</td></tr>"
    return rows

HDR = "background:#1a237e;color:white;padding:8px;font-size:12px"

def build_html(results, confluence, followup, ai_text, today):
    ai_html = ai_text.replace("\n","<br>").replace("**","")
    counts  = {k:len(v) for k,v in results.items()}
    total   = sum(counts.values())
    conf_count = len(confluence)

    return f"""<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;max-width:860px;margin:auto;color:#222'>

<!-- HEADER -->
<div style='background:#1a237e;color:white;padding:20px;border-radius:8px 8px 0 0'>
  <h2 style='margin:0'>📈 Nifty 100 Multi-Strategy Scanner — {today}</h2>
  <p style='margin:6px 0 0;opacity:.85'>5 strategies · {total} total signals · <b>{conf_count} HIGH-CONVICTION confluence alerts</b></p>
  <div style='display:flex;gap:12px;margin-top:10px;font-size:12px'>
    <span style='background:rgba(255,255,255,.2);padding:3px 8px;border-radius:4px'>S1 RSI: {counts['s1']}</span>
    <span style='background:rgba(255,255,255,.2);padding:3px 8px;border-radius:4px'>S2 Golden Cross: {counts['s2']}</span>
    <span style='background:rgba(255,255,255,.2);padding:3px 8px;border-radius:4px'>S3 MACD: {counts['s3']}</span>
    <span style='background:rgba(255,255,255,.2);padding:3px 8px;border-radius:4px'>S4 52W Low: {counts['s4']}</span>
    <span style='background:rgba(255,255,255,.2);padding:3px 8px;border-radius:4px'>S5 Quality: {counts['s5']}</span>
  </div>
</div>

<!-- AI ANALYSIS -->
<div style='padding:20px;background:#f9f9f9;border:1px solid #ddd'>
  <h3 style='color:#1a237e;margin-top:0'>🤖 AI Analysis</h3>
  <div style='background:white;padding:16px;border-left:4px solid #1a237e;border-radius:4px;line-height:1.7'>{ai_html}</div>
</div>

<!-- CONFLUENCE — HIGH CONVICTION -->
<div style='padding:20px'>
  <h3 style='color:#c62828;margin-top:0'>🔥 High-Conviction: Stocks in 2+ Strategies</h3>
  <table width='100%' border='1' cellpadding='8' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
    <tr style='{HDR}'><th>Ticker</th><th>Strategies Hit</th><th>Strategy Names</th></tr>
    {_confluence_rows(confluence)}
  </table>
  <p style='font-size:11px;color:#888'>🔥 = 2 strategies, 🔥🔥 = 3 strategies (extremely rare and very strong)</p>
</div>

<!-- S1 -->
<div style='padding:20px;background:#f0f4ff'>
  <h3 style='color:#1565c0;margin-top:0'>🔵 Strategy 1 — RSI Mean Reversion ({counts['s1']} signals)</h3>
  <p style='font-size:12px;color:#555;margin:0 0 8px'>RSI &lt; 30 (daily + weekly) · D/E &lt; 0.5 · PE &lt; 35 · Best horizon: 2–6 weeks</p>
  <table width='100%' border='1' cellpadding='7' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
    <tr style='{HDR}'><th>Ticker</th><th>Price</th><th>PE</th><th>D/E</th><th>RSI Daily</th><th>RSI Weekly</th><th>1W Chg</th><th>Signal</th></tr>
    {_tbl_rows_s1(results['s1'])}
  </table>
</div>

<!-- S2 -->
<div style='padding:20px'>
  <h3 style='color:#2e7d32;margin-top:0'>🟢 Strategy 2 — Golden Cross ({counts['s2']} signals)</h3>
  <p style='font-size:12px;color:#555;margin:0 0 8px'>50MA crosses above 200MA + volume surge · Best horizon: 3–12 months</p>
  <table width='100%' border='1' cellpadding='7' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
    <tr style='{HDR}'><th>Ticker</th><th>Price</th><th>MA50</th><th>MA200</th><th>Vol Ratio</th><th>RSI Daily</th></tr>
    {_tbl_rows_s2(results['s2'])}
  </table>
</div>

<!-- S3 -->
<div style='padding:20px;background:#f0faf6'>
  <h3 style='color:#00695c;margin-top:0'>🟣 Strategy 3 — MACD Crossover ({counts['s3']} signals)</h3>
  <p style='font-size:12px;color:#555;margin:0 0 8px'>MACD line crosses signal, histogram turns positive · Best horizon: 2–8 weeks</p>
  <table width='100%' border='1' cellpadding='7' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
    <tr style='{HDR}'><th>Ticker</th><th>Price</th><th>MACD</th><th>Histogram</th><th>RSI</th><th>PE</th></tr>
    {_tbl_rows_s3(results['s3'])}
  </table>
</div>

<!-- S4 -->
<div style='padding:20px'>
  <h3 style='color:#e65100;margin-top:0'>🟡 Strategy 4 — 52-Week Low Reversal ({counts['s4']} signals)</h3>
  <p style='font-size:12px;color:#555;margin:0 0 8px'>Near 52W low + ROE &gt; 12% + +ve cash flow + bouncing · Best horizon: 1–6 months</p>
  <table width='100%' border='1' cellpadding='7' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
    <tr style='{HDR}'><th>Ticker</th><th>Price</th><th>52W Low</th><th>% Above Low</th><th>ROE</th><th>RSI</th><th>PE</th></tr>
    {_tbl_rows_s4(results['s4'])}
  </table>
</div>

<!-- S5 -->
<div style='padding:20px;background:#f1f8e9'>
  <h3 style='color:#558b2f;margin-top:0'>⭐ Strategy 5 — Quality Momentum ({counts['s5']} signals)</h3>
  <p style='font-size:12px;color:#555;margin:0 0 8px'>ROE &gt; 18% · Price near 52W high · RSI 50–70 · MA50 &gt; MA200 · Best horizon: 3–9 months</p>
  <table width='100%' border='1' cellpadding='7' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
    <tr style='{HDR}'><th>Ticker</th><th>Price</th><th>52W High</th><th>% From High</th><th>ROE</th><th>RSI</th><th>PE</th></tr>
    {_tbl_rows_s5(results['s5'])}
  </table>
</div>

<!-- FOLLOW-UP -->
<div style='padding:20px;background:#fafafa;border-top:2px solid #ddd'>
  <h3 style='color:#1a237e;margin-top:0'>📊 Follow-up: Previously Flagged Stocks</h3>
  <table width='100%' border='1' cellpadding='7' cellspacing='0' style='border-collapse:collapse;font-size:13px'>
    <tr style='{HDR}'><th>Ticker</th><th>Flagged</th><th>Strategy</th><th>Entry</th><th>Now</th><th>Return</th></tr>
    {_followup_rows(followup)}
  </table>
</div>

<!-- FOOTER -->
<div style='background:#1a237e;color:white;padding:12px;border-radius:0 0 8px 8px;font-size:11px;text-align:center'>
  ⚠️ For informational purposes only. Not financial advice. Always do your own research and consult a SEBI-registered advisor.
</div>
</body></html>"""

# ── SEND ──────────────────────────────────────────────────────────────────────
def send_email(subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())
    print("✅ Email sent!")

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    today   = datetime.date.today().isoformat()
    weekday = datetime.date.today().weekday()
    print(f"\n{'='*55}\n5-Strategy Nifty 100 Scanner — {today}\n{'='*55}\n")

    results    = scan_all()
    confluence = find_confluence(results)
    history    = load_history()
    history    = update_history(results, history)
    followup   = build_followup(history)
    save_history(history)

    total = sum(len(v) for v in results.values())
    print(f"\n{'='*55}")
    print(f"Total signals: {total}")
    for k,label in [("s1","RSI"),("s2","Golden Cross"),("s3","MACD"),("s4","52W Low"),("s5","Quality")]:
        print(f"  {label}: {len(results[k])}")
    print(f"  Confluence (2+): {len(confluence)}")
    print(f"{'='*55}\n")

    print("Generating AI analysis …")
    ai_text = ai_analysis(results, confluence, followup)

    weekly_prefix = "📅 WEEKLY SUMMARY + " if weekday == 0 else ""
    subject = f"{weekly_prefix}📈 5-Strategy Scanner {today} — {total} signals · {len(confluence)} confluence"
    html    = build_html(results, confluence, followup, ai_text, today)
    send_email(subject, html)
