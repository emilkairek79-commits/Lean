#!/usr/bin/env python3
"""
Grid Bot Backtest — prawdziwe dane historyczne

Pobiera realne świece godzinowe SOL/USD i uruchamia na nich dokładnie tę samą
logikę siatki co live_grid_bot.py. Raportuje pełny wynik z uwzględnieniem
pozycji mark-to-market, prowizji i benchmarku buy & hold.

UŻYCIE:
    python grid_backtest.py
    python grid_backtest.py --lower 100 --upper 260 --levels 30
    python grid_backtest.py --sweep          # skanuje siatkę parametrów
    python grid_backtest.py --csv dane.csv   # własny plik zamiast pobierania

Wymaga tylko Pythona 3.9+ (bez zewnętrznych bibliotek).
Pobrane dane cache'uje w sol_1h.csv — kolejne uruchomienia są offline.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CACHE = Path("sol_1h.csv")
UA = {"User-Agent": "Mozilla/5.0 (grid-backtest)"}


# ═══════════════════════════════════════════════════════════════════════════
#  POBIERANIE DANYCH
# ═══════════════════════════════════════════════════════════════════════════

def _get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def fetch_binance(start_ms: int, end_ms: int) -> list[dict]:
    """Świece 1h z publicznego API Binance (1000 na żądanie)."""
    out, cur = [], start_ms
    while cur < end_ms:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol=SOLUSDT&interval=1h&startTime={cur}&endTime={end_ms}&limit=1000"
        )
        rows = _get(url)
        if not rows:
            break
        for k in rows:
            out.append({
                "t": int(k[0]) // 1000,
                "o": float(k[1]), "h": float(k[2]),
                "l": float(k[3]), "c": float(k[4]),
            })
        cur = int(rows[-1][0]) + 3_600_000
        print(f"\r  Binance: {len(out)} świec...", end="", flush=True)
        time.sleep(0.25)
    print()
    return out


def fetch_coinbase(start_ms: int, end_ms: int) -> list[dict]:
    """Świece 1h z Coinbase Exchange (300 na żądanie)."""
    out, cur = [], start_ms // 1000
    end_s, span = end_ms // 1000, 300 * 3600
    while cur < end_s:
        hi = min(cur + span, end_s)
        url = (
            "https://api.exchange.coinbase.com/products/SOL-USD/candles"
            f"?granularity=3600"
            f"&start={datetime.fromtimestamp(cur, timezone.utc).isoformat()}"
            f"&end={datetime.fromtimestamp(hi, timezone.utc).isoformat()}"
        )
        rows = _get(url)
        # Coinbase: [time, low, high, open, close, volume], malejąco
        for k in sorted(rows, key=lambda r: r[0]):
            out.append({
                "t": int(k[0]),
                "o": float(k[3]), "h": float(k[2]),
                "l": float(k[1]), "c": float(k[4]),
            })
        cur = hi
        print(f"\r  Coinbase: {len(out)} świec...", end="", flush=True)
        time.sleep(0.35)
    print()
    return out


def fetch_kraken(start_ms: int, end_ms: int) -> list[dict]:
    """Świece 1h z Kraken (ograniczone do ~720 ostatnich — fallback)."""
    url = f"https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval=60&since={start_ms//1000}"
    data = _get(url)
    if data.get("error"):
        raise RuntimeError(f"Kraken: {data['error']}")
    key = next(k for k in data["result"] if k != "last")
    return [
        {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
         "l": float(k[3]), "c": float(k[4])}
        for k in data["result"][key]
        if start_ms // 1000 <= int(k[0]) <= end_ms // 1000
    ]


def load_bars(start: tuple, end: tuple, csv_path: Path | None) -> list[dict]:
    if csv_path:
        print(f"Wczytuję {csv_path}")
        with open(csv_path) as f:
            return [
                {"t": int(r["t"]), "o": float(r["o"]), "h": float(r["h"]),
                 "l": float(r["l"]), "c": float(r["c"])}
                for r in csv.DictReader(f)
            ]

    if CACHE.exists():
        print(f"Używam cache: {CACHE}")
        with open(CACHE) as f:
            bars = [
                {"t": int(r["t"]), "o": float(r["o"]), "h": float(r["h"]),
                 "l": float(r["l"]), "c": float(r["c"])}
                for r in csv.DictReader(f)
            ]
        if bars:
            return bars

    s_ms, e_ms = _ms(*start), _ms(*end)
    for name, fn in (("Binance", fetch_binance),
                     ("Coinbase", fetch_coinbase),
                     ("Kraken", fetch_kraken)):
        try:
            print(f"Pobieram z {name}...")
            bars = fn(s_ms, e_ms)
            if len(bars) > 100:
                with open(CACHE, "w", newline="") as f:
                    w = csv.DictWriter(f, ["t", "o", "h", "l", "c"])
                    w.writeheader()
                    w.writerows(bars)
                print(f"Zapisano {len(bars)} świec do {CACHE}")
                return bars
            print(f"  {name}: za mało danych ({len(bars)})")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                RuntimeError, StopIteration, KeyError, ValueError) as e:
            print(f"  {name} nieosiągalny: {e}")

    sys.exit(
        "\nNie udało się pobrać danych z żadnego źródła.\n"
        "Pobierz świece ręcznie i podaj przez --csv (kolumny: t,o,h,l,c;\n"
        "t = unix seconds, świece godzinowe)."
    )


# ═══════════════════════════════════════════════════════════════════════════
#  SILNIK SIATKI
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Order:
    side: str          # "buy" | "sell"
    price: float
    born: int          # indeks świecy, w której zlecenie powstało


@dataclass
class Result:
    lower: float
    upper: float
    levels: int
    capital: float
    qty: float
    step: float
    round_trips: int = 0
    gross: float = 0.0
    fees: float = 0.0
    cash: float = 0.0
    coins: float = 0.0
    final_price: float = 0.0
    bars: int = 0
    bars_below: int = 0
    bars_above: int = 0
    max_dd: float = 0.0
    equity: list = field(default_factory=list)

    @property
    def net_grid(self) -> float:
        return self.gross - self.fees

    @property
    def final_equity(self) -> float:
        return self.cash + self.coins * self.final_price

    @property
    def total_return(self) -> float:
        return (self.final_equity - self.capital) / self.capital * 100

    @property
    def pct_outside(self) -> float:
        return (self.bars_below + self.bars_above) / max(self.bars, 1) * 100


def backtest(bars: list[dict], lower: float, upper: float, levels: int,
             capital: float, fee_pct: float) -> Result:
    """
    Uruchamia siatkę na świecach OHLC.

    Założenia (konserwatywne):
      • Zlecenie BUY wypełnia się, gdy low świecy <= cena zlecenia.
      • Zlecenie SELL wypełnia się, gdy high świecy >= cena zlecenia.
      • Zlecenie przeciwne postawione po wypełnieniu NIE może wypełnić się
        w tej samej świecy (born > i) — inaczej wynik byłby zawyżony.
      • Prowizja maker naliczana od obu stron każdego cyklu.
    """
    step = (upper - lower) / levels
    qty = (capital / levels) / ((upper + lower) / 2)
    grid = [lower + i * step for i in range(levels + 1)]

    r = Result(lower=lower, upper=upper, levels=levels, capital=capital,
               qty=qty, step=step, bars=len(bars))

    start_px = bars[0]["c"]
    orders: list[Order] = []
    for lvl in grid[:-1]:
        if lvl < start_px:
            orders.append(Order("buy", lvl, -1))
        else:
            orders.append(Order("sell", lvl + step, -1))

    r.cash = capital
    r.coins = 0.0
    peak = capital

    for i, bar in enumerate(bars):
        if bar["c"] < lower:
            r.bars_below += 1
        elif bar["c"] > upper:
            r.bars_above += 1

        # BUY-e od najwyższego w dół (cena spada przez poziomy)
        for o in sorted([o for o in orders if o.side == "buy"],
                        key=lambda x: -x.price):
            if o.born >= i or bar["l"] > o.price:
                continue
            cost = o.price * qty
            fee = cost * fee_pct / 100
            if r.cash < cost + fee:
                continue                      # brak gotówki — poziom pomijany
            r.cash -= cost + fee
            r.coins += qty
            r.fees += fee
            orders.remove(o)
            if o.price + step <= upper:
                orders.append(Order("sell", o.price + step, i))

        # SELL-e od najniższego w górę (cena rośnie przez poziomy)
        for o in sorted([o for o in orders if o.side == "sell"],
                        key=lambda x: x.price):
            if o.born >= i or bar["h"] < o.price:
                continue
            if r.coins < qty:
                continue                      # brak monet — poziom pomijany
            proceeds = o.price * qty
            fee = proceeds * fee_pct / 100
            r.cash += proceeds - fee
            r.coins -= qty
            r.fees += fee
            r.gross += step * qty
            r.round_trips += 1
            orders.remove(o)
            if o.price - step >= lower:
                orders.append(Order("buy", o.price - step, i))

        eq = r.cash + r.coins * bar["c"]
        r.equity.append(eq)
        peak = max(peak, eq)
        r.max_dd = max(r.max_dd, (peak - eq) / peak * 100)

    r.final_price = bars[-1]["c"]
    return r


# ═══════════════════════════════════════════════════════════════════════════
#  RAPORT
# ═══════════════════════════════════════════════════════════════════════════

def report(r: Result, bars: list[dict]):
    d0 = datetime.fromtimestamp(bars[0]["t"], timezone.utc).date()
    d1 = datetime.fromtimestamp(bars[-1]["t"], timezone.utc).date()
    p0, p1 = bars[0]["c"], bars[-1]["c"]
    bh = (p1 - p0) / p0 * 100
    bh_equity = r.capital * (1 + bh / 100)

    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)

    L = "─" * 68
    print(f"\n{L}\n  BACKTEST SIATKI — SOL/USD\n{L}")
    print(f"  Okres          : {d0} → {d1}   ({r.bars} świec 1h)")
    print(f"  Cena start/stop: ${p0:.2f} → ${p1:.2f}")
    print(f"  Zakres rynku   : ${lo:.2f} – ${hi:.2f}")
    print(f"\n  Siatka         : ${r.lower:.0f} – ${r.upper:.0f}, {r.levels} poziomów")
    print(f"  Krok           : ${r.step:.2f}   Qty/poziom: {r.qty:.4f} SOL")
    print(f"  Kapitał        : ${r.capital:,.2f}")

    print(f"\n{L}\n  WYNIK\n{L}")
    print(f"  Round-trips    : {r.round_trips}")
    print(f"  Zysk brutto    : ${r.gross:,.2f}")
    print(f"  Prowizje       : ${r.fees:,.2f}")
    print(f"  Zysk netto     : ${r.net_grid:,.2f}")
    print(f"\n  Gotówka        : ${r.cash:,.2f}")
    print(f"  Pozycja SOL    : {r.coins:.4f} × ${r.final_price:.2f} "
          f"= ${r.coins * r.final_price:,.2f}")
    print(f"  KAPITAŁ KOŃC.  : ${r.final_equity:,.2f}")
    print(f"  ZWROT          : {r.total_return:+.2f}%")
    print(f"  Max drawdown   : {r.max_dd:.2f}%")

    print(f"\n{L}\n  BENCHMARK\n{L}")
    print(f"  Buy & hold     : {bh:+.2f}%   (${bh_equity:,.2f})")
    diff = r.total_return - bh
    verdict = "siatka lepsza" if diff > 0 else "buy & hold lepszy"
    print(f"  Różnica        : {diff:+.2f} p.p.  →  {verdict}")

    print(f"\n{L}\n  ZACHOWANIE ZAKRESU\n{L}")
    print(f"  Poniżej siatki : {r.bars_below} świec ({r.bars_below/r.bars*100:.1f}%)")
    print(f"  Powyżej siatki : {r.bars_above} świec ({r.bars_above/r.bars*100:.1f}%)")
    print(f"  Poza zakresem  : {r.pct_outside:.1f}% czasu")
    if r.pct_outside > 25:
        print(f"  ⚠️  Ponad ćwierć okresu poza siatką — bot stał bezczynnie.")
    if r.bars_below > r.bars * 0.15:
        print(f"  ⚠️  Długi czas pod dolną granicą — pozycja kupowana w trendzie spadkowym.")
    print(L)


def sweep(bars: list[dict], capital: float, fee_pct: float):
    lo_px = min(b["l"] for b in bars)
    hi_px = max(b["h"] for b in bars)
    span = hi_px - lo_px

    configs = []
    for pad in (0.0, 0.10, 0.20):
        lower = round(lo_px + span * pad, -1)
        upper = round(hi_px - span * pad, -1)
        if upper - lower < 20:
            continue
        for levels in (10, 20, 30, 50):
            configs.append((lower, upper, levels))

    print(f"\n{'zakres':>18} {'poz.':>5} {'cykle':>7} {'netto':>12} "
          f"{'zwrot':>9} {'DD':>7} {'poza':>7}")
    print("─" * 72)
    rows = []
    for lower, upper, levels in configs:
        r = backtest(bars, lower, upper, levels, capital, fee_pct)
        rows.append(r)
        print(f"${lower:>6.0f}–${upper:<7.0f} {levels:>5} {r.round_trips:>7} "
              f"${r.net_grid:>10,.0f} {r.total_return:>8.1f}% "
              f"{r.max_dd:>6.1f}% {r.pct_outside:>6.1f}%")

    p0, p1 = bars[0]["c"], bars[-1]["c"]
    bh = (p1 - p0) / p0 * 100
    print("─" * 72)
    print(f"{'buy & hold':>18} {'':>5} {'':>7} {'':>12} {bh:>8.1f}%")

    best = max(rows, key=lambda r: r.total_return)
    print(f"\nNajlepsza konfiguracja: ${best.lower:.0f}–${best.upper:.0f}, "
          f"{best.levels} poziomów → {best.total_return:+.2f}% "
          f"(buy & hold {bh:+.2f}%)")


# ═══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Backtest siatki na prawdziwych danych SOL")
    ap.add_argument("--lower", type=float, default=80.0)
    ap.add_argument("--upper", type=float, default=200.0)
    ap.add_argument("--levels", type=int, default=20)
    ap.add_argument("--capital", type=float, default=21971.0)
    ap.add_argument("--fee", type=float, default=0.16,
                    help="prowizja maker %% za stronę (Kraken 0.16, Coinbase 0.40, Binance 0.10)")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-01-01")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--sweep", action="store_true", help="skanuj parametry siatki")
    a = ap.parse_args()

    s = tuple(int(x) for x in a.start.split("-"))
    e = tuple(int(x) for x in a.end.split("-"))

    bars = load_bars(s, e, a.csv)
    bars = [b for b in bars
            if _ms(*s) // 1000 <= b["t"] < _ms(*e) // 1000]
    bars.sort(key=lambda b: b["t"])
    if len(bars) < 100:
        sys.exit(f"Za mało świec w zadanym okresie: {len(bars)}")

    if a.sweep:
        sweep(bars, a.capital, a.fee)
    else:
        r = backtest(bars, a.lower, a.upper, a.levels, a.capital, a.fee)
        report(r, bars)


if __name__ == "__main__":
    main()
