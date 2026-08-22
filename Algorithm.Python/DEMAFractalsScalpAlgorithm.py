# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from AlgorithmImports import *

### <summary>
### DEMA(20/50/100) + Williams Fractals – pullback scalp.
###
### Trend   long : DEMA20 > DEMA50 > DEMA100
###         short: DEMA100 > DEMA50 > DEMA20
### Pullback: rolling low (long) or rolling high (short) breaks through DEMA20
###           over the last PB_WIN bars
### Entry   : bar confirming a Williams fractal (down-fractal → long,
###           up-fractal → short); market order fills at bar close
### Stop    : if pullback only breached DEMA20  → SL at DEMA50
###           if pullback also breached DEMA50  → SL at DEMA100
### Target  : fixed reward:risk = RR (default 1.5)
### Sizing  : risk RISK_PCT % of portfolio per trade
### </summary>
### <meta name="tag" content="indicators" />
### <meta name="tag" content="strategy example" />
### <meta name="tag" content="futures" />
class DEMAFractalsScalpAlgorithm(QCAlgorithm):

    # ── parametry ──────────────────────────────────────────────────────────
    D_FAST   = 20
    D_MID    = 50
    D_SLOW   = 100
    RR       = 1.5      # reward:risk
    PB_WIN   = 15       # okno wsteczne (bary) dla sprawdzenia pullbacku
    RISK_PCT = 0.5      # % wartości portfela ryzykowany na transakcję
    COST_PTS = 0.25     # poślizg + prowizja (w punktach ceny, na stronę)
    # ───────────────────────────────────────────────────────────────────────

    def initialize(self):
        self.set_start_date(2021, 1, 1)
        self.set_end_date(2023, 6, 30)
        self.set_cash(25_000)

        equity = self.add_equity("SPY", Resolution.MINUTE)
        self._sym = equity.symbol

        # Wskaźniki DEMA (aktualizowane automatycznie co minutę)
        self._d20  = self.dema(self._sym, self.D_FAST,  Resolution.MINUTE)
        self._d50  = self.dema(self._sym, self.D_MID,   Resolution.MINUTE)
        self._d100 = self.dema(self._sym, self.D_SLOW,  Resolution.MINUTE)

        # Okno kroczące dla detekcji fraktali i sprawdzenia pullbacku.
        # Rozmiar = PB_WIN (≥ 5 potrzebnych przez fraktal Williams'a).
        self._bars = RollingWindow[TradeBar](self.PB_WIN)

        # Rozgrzewka: DEMA100 potrzebuje 2*100-1 = 199 barów, dodajemy bufor
        self.set_warm_up(self.D_SLOW * 3, Resolution.MINUTE)

        # Stan pozycji
        self._stop_ticket   = None
        self._target_ticket = None

        # Wykresy
        self.add_chart(Chart("Equity"))
        stock_plot = Chart("Price")
        stock_plot.add_series(Series("DEMA20",  SeriesType.LINE, 0))
        stock_plot.add_series(Series("DEMA50",  SeriesType.LINE, 0))
        stock_plot.add_series(Series("DEMA100", SeriesType.LINE, 0))
        self.add_chart(stock_plot)

    # ── zdarzenia danych ───────────────────────────────────────────────────

    def on_data(self, data):
        if not data[self._sym]: return

        bar = data[self._sym]
        self._bars.add(bar)

        if self.is_warming_up: return
        if not (self._d20.is_ready and self._d50.is_ready and self._d100.is_ready): return
        if not self._bars.is_ready: return  # czekamy na pełne PB_WIN barów

        # Jeśli mamy otwartą pozycję, wyjścia zarządzają zlecenia stop/limit
        if self._stop_ticket is not None: return

        d20  = float(self._d20.current.value)
        d50  = float(self._d50.current.value)
        d100 = float(self._d100.current.value)

        # ── 1. Filtr trendu (stos DEMA) ──────────────────────────────────
        up_trend = d20 > d50 > d100
        dn_trend = d100 > d50 > d20
        if not (up_trend or dn_trend): return

        # ── 2. Fraktale Williamsa (potwierdzenie 2 bary później) ─────────
        dn_frac = self._dn_fractal()   # dół fraktalny → sygnał long
        up_frac = self._up_fractal()   # szczyt fraktalny → sygnał short
        if not (dn_frac or up_frac): return

        # ── 3. Sprawdzenie pullbacku (okno PB_WIN barów) ─────────────────
        win_lo = min(float(self._bars[j].low)  for j in range(self._bars.count))
        win_hi = max(float(self._bars[j].high) for j in range(self._bars.count))

        long_sig  = up_trend and dn_frac and (win_lo < d20)
        short_sig = dn_trend and up_frac and (win_hi > d20)
        if not (long_sig or short_sig): return

        # ── 4. Wyznaczenie stop-lossa i targetu ──────────────────────────
        price = float(bar.close)
        side  = 1 if long_sig else -1

        if side == 1:
            broke_50 = win_lo < d50
            stop = d100 if broke_50 else d50
            # stop musi być poniżej ceny wejścia
            if stop >= price - self.COST_PTS: return
            dist   = (price + self.COST_PTS) - stop   # koszt wejścia uwzględniony
            target = (price + self.COST_PTS) + dist * self.RR
        else:
            broke_50 = win_hi > d50
            stop = d100 if broke_50 else d50
            # stop musi być powyżej ceny wejścia
            if stop <= price + self.COST_PTS: return
            dist   = stop - (price - self.COST_PTS)
            target = (price - self.COST_PTS) - dist * self.RR

        if dist <= 0: return

        # ── 5. Wielkość pozycji: ryzykujemy RISK_PCT % portfela ──────────
        risk = float(self.portfolio.total_portfolio_value) * self.RISK_PCT / 100.0
        qty  = max(1, int(risk / dist))

        # ── 6. Wejście + zlecenia ochronne ───────────────────────────────
        self.market_order(self._sym, side * qty)

        self._stop_ticket   = self.stop_market_order(self._sym, -side * qty, stop)
        self._target_ticket = self.limit_order(       self._sym, -side * qty, target)

        self.log(
            f"WEJŚCIE {'LONG' if side==1 else 'SHORT'} | "
            f"cena={price:.2f} stop={stop:.2f} target={target:.2f} "
            f"qty={qty} dist={dist:.2f} R={dist*qty:.0f}$"
        )

    # ── zarządzanie zleceniami ─────────────────────────────────────────────

    def on_order_event(self, order_event):
        if order_event.status != OrderStatus.FILLED: return
        if self._stop_ticket is None and self._target_ticket is None: return

        stop_id   = self._stop_ticket.order_id   if self._stop_ticket   is not None else -1
        target_id = self._target_ticket.order_id if self._target_ticket is not None else -1

        if order_event.order_id == stop_id:
            if self._target_ticket is not None:
                self._target_ticket.cancel("stop hit – anuluj target")
            self.log(f"STOP trafiony @ {order_event.fill_price:.2f}")
            self._stop_ticket   = None
            self._target_ticket = None

        elif order_event.order_id == target_id:
            if self._stop_ticket is not None:
                self._stop_ticket.cancel("target osiągnięty – anuluj stop")
            self.log(f"TARGET osiągnięty @ {order_event.fill_price:.2f}")
            self._stop_ticket   = None
            self._target_ticket = None

    def on_end_of_algorithm(self):
        self._cancel_exits()
        final = float(self.portfolio.total_portfolio_value)
        ret   = (final - 25_000) / 25_000 * 100
        self.log(f"Wynik końcowy: ${final:,.2f}  ({ret:+.1f}%)")

    # ── pomocnicze ─────────────────────────────────────────────────────────

    def _dn_fractal(self) -> bool:
        """Dół fraktalny (bycze): bar[2] ma najniższe Low w 5-barowym sąsiedztwie."""
        b  = self._bars
        lo = float(b[2].low)
        return (lo < float(b[1].low) and lo < float(b[0].low) and
                lo < float(b[3].low) and lo < float(b[4].low))

    def _up_fractal(self) -> bool:
        """Szczyt fraktalny (niedźwiedzie): bar[2] ma najwyższe High w 5-barowym sąsiedztwie."""
        b  = self._bars
        hi = float(b[2].high)
        return (hi > float(b[1].high) and hi > float(b[0].high) and
                hi > float(b[3].high) and hi > float(b[4].high))

    def _cancel_exits(self):
        if self._stop_ticket is not None:
            self._stop_ticket.cancel("czyszczenie na koniec")
            self._stop_ticket = None
        if self._target_ticket is not None:
            self._target_ticket.cancel("czyszczenie na koniec")
            self._target_ticket = None
