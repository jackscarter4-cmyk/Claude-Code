#!/usr/bin/env python3
"""
Local web UI for the offline Stock Quant Engine.

Runs a tiny web server on localhost using only the Python standard library
(http.server) — no Flask, no internet, no API keys. Paste your stock CSV in
the browser and get the 5-factor scoring + signals + risk report back.

USAGE
-----
  python serve.py                 # serve on http://127.0.0.1:8000
  python serve.py --port 9000     # custom port

Then open the printed URL in your browser. The server binds to 127.0.0.1
only, so it is not reachable from other machines.
"""

import argparse
import csv
import html
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from layer2_market_making.signal_scanner import SignalScanner
from layer3_ai_agent.quant_scorer import QuantScorer
from layer5_risk.risk_calculator import RiskCalculator
from daily_check import load_csv_text, TEMPLATE_CSV
from main import _portfolio_from_stocks, _market_data_from_stocks


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

PAGE_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0;
       background: #0f1115; color: #e6e6e6; }
.wrap { max-width: 1100px; margin: 0 auto; padding: 24px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: #8b94a3; margin: 0 0 20px; font-size: 14px; }
textarea { width: 100%; height: 220px; font-family: ui-monospace, Menlo, monospace;
           font-size: 12px; background: #161922; color: #d6dbe4;
           border: 1px solid #2a2f3a; border-radius: 8px; padding: 12px; }
button { background: #2f6feb; color: #fff; border: 0; border-radius: 8px;
         padding: 10px 18px; font-size: 14px; cursor: pointer; margin-top: 10px; }
button:hover { background: #2257c4; }
table { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }
th, td { padding: 7px 9px; text-align: right; border-bottom: 1px solid #232833; }
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align: left; }
th { color: #8b94a3; font-weight: 600; }
.verdict { font-weight: 700; padding: 2px 8px; border-radius: 5px; font-size: 12px; }
.BUY { background: #123d1f; color: #4ade80; }
.SELL, .AVOID { background: #3d1212; color: #f87171; }
.HOLD { background: #2a2f3a; color: #9aa3b2; }
.sec { margin-top: 28px; }
.sec h2 { font-size: 16px; border-bottom: 1px solid #2a2f3a; padding-bottom: 6px; }
.sig, .rec { background: #161922; border: 1px solid #232833; border-radius: 8px;
             padding: 10px 14px; margin: 8px 0; font-size: 13px; }
.sig b, .rec b { color: #cdd5e0; }
.muted { color: #8b94a3; }
.err { background: #3d1212; color: #f87171; padding: 12px; border-radius: 8px; }
.pnl-pos { color: #4ade80; } .pnl-neg { color: #f87171; }
"""


def _score_color(s):
    if s is None:
        return "#8b94a3"
    if s >= 6.5:
        return "#4ade80"
    if s >= 4.0:
        return "#facc15"
    return "#f87171"


def _cell(score):
    if score is None:
        return '<td class="muted">–</td>'
    return f'<td style="color:{_score_color(score)}">{score:.1f}</td>'


def render_form(csv_text: str) -> str:
    return f"""
    <form method="POST" action="/analyze">
      <p class="sub">Paste your stock CSV below (or edit the example). Only
      <code>symbol</code> and <code>price</code> are required — fill in what you have.
      Add <code>annual_volatility</code> (e.g. 0.25 = 25%) and <code>atr_14</code> to
      unlock fractional Kelly sizing + VaR/CVaR. Add <code>bollinger_upper</code>,
      <code>bollinger_lower</code>, <code>vwap</code> to unlock additional signals.
      Get all values from Yahoo Finance or TradingView.</p>
      <textarea name="csv" spellcheck="false">{html.escape(csv_text)}</textarea>
      <br><button type="submit">Run analysis</button>
    </form>
    """


def render_results(stocks) -> str:
    scorer = QuantScorer()
    results = scorer.score(stocks)

    portfolio_value, position_data, sector_by_symbol = _portfolio_from_stocks(stocks)
    scanner = SignalScanner()
    signals = scanner.scan(
        [s.symbol for s in stocks],
        {s.symbol: s.price for s in stocks},
        position_data,
        _market_data_from_stocks(stocks),
    )
    risk = RiskCalculator()
    decisions = [
        risk.evaluate(r, portfolio_value,
                      {k: v["market_value"] for k, v in position_data.items()},
                      sector_by_symbol)
        for r in results
    ]

    # --- scoring table ---
    rows = []
    for r in results:
        comp = f"{r.composite:.1f}" if r.composite is not None else "–"
        pnl = ""
        if r.cost_basis and r.price:
            p = (r.price - r.cost_basis) / r.cost_basis * 100
            cls = "pnl-pos" if p >= 0 else "pnl-neg"
            pnl = f'<span class="{cls}">{p:+.1f}%</span>'
        rows.append(f"""
          <tr>
            <td>{html.escape(r.symbol)}</td>
            <td class="muted">{html.escape(r.sector or '')}</td>
            <td>${r.price:,.2f}</td>
            <td><b>{comp}</b></td>
            {_cell(r.score_value)}{_cell(r.score_growth)}{_cell(r.score_profitability)}
            {_cell(r.score_eps_revisions)}{_cell(r.score_momentum)}
            <td><span class="verdict {r.verdict}">{r.verdict}</span> {r.confidence}</td>
            <td>{pnl}</td>
          </tr>""")

    table = f"""
      <table>
        <tr><th>Symbol</th><th>Sector</th><th>Price</th><th>Score</th>
            <th>Val</th><th>Grw</th><th>Prof</th><th>EPS</th><th>Mom</th>
            <th>Verdict</th><th>P&amp;L</th></tr>
        {''.join(rows)}
      </table>
    """

    # --- signals ---
    if signals:
        sig_html = "".join(
            f'<div class="sig"><b>[{s.signal_type}]</b> {html.escape(s.symbol)} '
            f'{s.direction} · strength {s.strength:.2f} · ~${s.suggested_size_usd:,.0f}'
            f'<br><span class="muted">{html.escape(s.reasoning)}</span></div>'
            for s in signals
        )
    else:
        sig_html = ('<p class="muted">No signals fired. Add <code>high_52w</code>, '
                    '<code>avg_volume</code>, <code>current_volume</code> columns to enable.</p>')

    # --- risk recs ---
    recs = [d for d in decisions if d.approved]
    if recs:
        rec_html = ""
        for d in recs:
            if d.side == "BUY":
                head = (f"<b>BUY {html.escape(d.symbol)}</b> · ${d.size_usd:,.0f} "
                        f"({d.shares:g} sh) · stop ${d.stop_loss_price:.2f}")
            else:
                head = f"<b>{d.side} {html.escape(d.symbol)}</b> · {d.shares:g} sh (${d.size_usd:,.0f})"
            reasons = "; ".join(html.escape(x) for x in d.reasons)
            risk_tags = []
            if d.kelly_fraction is not None:
                risk_tags.append(f"½-Kelly {d.kelly_fraction*100:.1f}%")
            if d.var_95_usd is not None:
                risk_tags.append(f"VaR₉₅ ${d.var_95_usd:,.0f}/day")
            if d.cvar_95_usd is not None:
                risk_tags.append(f"CVaR₉₅ ${d.cvar_95_usd:,.0f}/day")
            risk_line = (' <span class="muted">· ' + " · ".join(risk_tags) + '</span>'
                         if risk_tags else "")
            rec_html += (f'<div class="rec">{head}{risk_line}'
                         f'<br><span class="muted">{reasons}</span></div>')
    else:
        rec_html = '<p class="muted">No actions pass risk checks right now.</p>'

    return f"""
      <div class="sec"><h2>Layer 3 — Quant Scores</h2>{table}</div>
      <div class="sec"><h2>Layer 2 — Signals</h2>{sig_html}</div>
      <div class="sec"><h2>Layer 5 — Risk-Sized Recommendations</h2>{rec_html}</div>
      <p style="margin-top:24px"><a href="/" style="color:#2f6feb">&larr; Back / edit input</a></p>
    """


def page(body: str) -> bytes:
    return f"""<!doctype html><html><head><meta charset="utf-8">
    <title>Stock Quant Engine</title><style>{PAGE_CSS}</style></head>
    <body><div class="wrap">
      <h1>Offline Stock Quant Engine</h1>
      <p class="sub">Runs locally · no API keys · 5-factor model</p>
      {body}
    </div></body></html>""".encode("utf-8")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(page(render_form(TEMPLATE_CSV.strip())))
        else:
            self._send(page('<p class="err">Not found.</p>'), status=404)

    def do_POST(self):
        if self.path != "/analyze":
            self._send(page('<p class="err">Not found.</p>'), status=404)
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        csv_text = parse_qs(raw).get("csv", [""])[0]
        try:
            stocks = load_csv_text(csv_text)
            if not stocks:
                raise ValueError("No valid rows found. Need at least 'symbol,price'.")
            body = render_results(stocks)
        except Exception as exc:  # show parse/format errors in the page
            body = (f'<p class="err">Could not analyze input: {html.escape(str(exc))}</p>'
                    + render_form(csv_text))
        self._send(page(body))

    def log_message(self, fmt, *args):
        sys.stderr.write("  " + (fmt % args) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Local web UI for the offline quant engine.")
    parser.add_argument("--port", "-p", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default localhost only)")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Stock Quant Engine running at {url}")
    print("Open that URL in your browser. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
