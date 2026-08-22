# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

from AlgorithmImports import *
from collections import deque

### <summary>
### Smart Money Concepts (SMC) – XAUUSD H1
###
### Logika:
###   1. Wykryj pivot High / Low (SWING barów z każdej strony)
###   2. Śledź strukturę rynkową:
###        BOS bycze  (HH) → kontynuacja trendu wzrostowego
###        BOS niedźw (LL) → kontynuacja trendu spadkowego
###        CHoCH bycze → ostatnia świeca niedźwiedzia przed ruchem = bullish OB
###        CHoCH niedźw→ ostatnia świeca bycza   przed ruchem = bearish OB
###   3. Gdy cena wraca do OB → wejście rynkowe
###   4. Stop za krawędzią OB, target = ostatni swing (lub RR × dist)
### </summary>
### <meta name="tag" content="gold" />
### <meta name="tag" content="smart money" />
### <meta name="tag" content="order block" />
class SMCGoldAlgorithm(QCAlgorithm):

    # ── parametry ──────────────────────────────────────────────────────────
    SWING   = 5      # liczba barów po każdej stronie pivota
    RR      = 2.0    # reward : risk
    RISK    = 1.0    # % portfela ryzykowany na transakcję
    OB_AGE  = 40     # max wiek OB (bary); po tym czasie wygasa
    BUF_PCT = 0.05   # % buffer wokół granicy OB (zapobiega false-entries)
    # ───────────────────────────────────────────────────────────────────────

    def initialize(self):
        self.set_start_date(2022, 1, 1)
        self.set_end_date(2024, 6, 30)
        self.set_cash(50_000)

        # XAUUSD CFD (dane z OANDA dostępne w QuantConnect)
        cfd = self.add_cfd("XAUUSD", Resolution.HOUR, Market.OANDA)
        self._sym = cfd.symbol

        # Okno: 2*SWING + kilka zapasowych barów
        self._bars = RollingWindow[TradeBar](self.SWING * 2 + 8)

        # Ostatnie potwierdzone swing highs / lows
        self._last_sh: float | None = None
        self._last_sl: float | None = None

        # Trend: 1=byczy, -1=niedźwiedzi, 0=nieokreślony
        self._trend = 0

        # Lista aktywnych Order Blocków
        # Każdy OB: {'side': ±1, 'top': float, 'bot': float, 'age': int, 'target': float}
        self._obs: list[dict] = []

        # Otwarta pozycja
        self._stop_ticket   = None
        self._target_ticket = None
        self._bar_n         = 0

        self.set_warm_up(300, Resolution.HOUR)

    # ── główna pętla danych ────────────────────────────────────────────────

    def on_data(self, data):
        if not data.bars.contains_key(self._sym): return
        bar = data.bars[self._sym]
        self._bars.add(bar)
        self._bar_n += 1

        if self.is_warming_up: return
        if not self._bars.is_ready: return
        if self._stop_ticket is not None: return   # trzymamy pozycję

        # 1. Aktualizacja struktury rynkowej
        self._update_structure()

        # 2. Starzenie i wygasanie OB
        for ob in self._obs:
            ob['age'] += 1
        self._obs = [ob for ob in self._obs if ob['age'] <= self.OB_AGE]

        # 3. Sprawdzenie powrotu do OB (entry)
        price = float(bar.close)
        hi    = float(bar.high)
        lo    = float(bar.low)
        self._check_entry(price, hi, lo)

    # ── detekcja struktury ─────────────────────────────────────────────────

    def _update_structure(self):
        """Wykryj pivot w centrum okna (SWING barów temu) i zaktualizuj strukturę."""
        S = self.SWING
        center_hi = float(self._bars[S].high)
        center_lo = float(self._bars[S].low)

        # Pivot High: centrum najwyższe z 2S+1 barów
        # self._bars[S-j] → j barów nowszych od centrum (prawa strona)
        # self._bars[S+j] → j barów starszych od centrum (lewa strona)
        is_ph = (
            all(float(self._bars[S - j].high) < center_hi for j in range(1, S + 1)) and
            all(float(self._bars[S + j].high) < center_hi for j in range(1, S + 1))
        )
        is_pl = (
            all(float(self._bars[S - j].low)  > center_lo for j in range(1, S + 1)) and
            all(float(self._bars[S + j].low)   > center_lo for j in range(1, S + 1))
        )

        if is_ph:
            self._on_swing_high(center_hi)
        if is_pl:
            self._on_swing_low(center_lo)

    def _on_swing_high(self, price: float):
        if self._last_sh is not None and price > self._last_sh:
            # Higher High
            if self._trend == -1:
                self._trend = 1
                self.log(f"CHoCH BYCZY @ {price:.2f}")
                self._create_ob(bullish=True)
            elif self._trend == 1:
                # BOS byczy – kontynuacja, nowy OB
                self._create_ob(bullish=True)
        elif self._last_sh is None and self._last_sl is not None:
            self._trend = 1

        self._last_sh = price

    def _on_swing_low(self, price: float):
        if self._last_sl is not None and price < self._last_sl:
            # Lower Low
            if self._trend == 1:
                self._trend = -1
                self.log(f"CHoCH NIEDŹWIEDZI @ {price:.2f}")
                self._create_ob(bullish=False)
            elif self._trend == -1:
                # BOS niedźwiedzi – kontynuacja
                self._create_ob(bullish=False)
        elif self._last_sl is not None and price > self._last_sl and self._trend == 0:
            self._trend = 1

        self._last_sl = price

    # ── tworzenie Order Block ─────────────────────────────────────────────

    def _create_ob(self, bullish: bool):
        """
        Znajdź ostatnią świecę przeciwną przed ruchem impulsowym i utwórz OB.
        Szukamy w barach S+1 … S+16 wstecz (strefa przed pivitem).
        """
        S   = self.SWING
        end = min(S + 17, self._bars.count)

        for j in range(S + 1, end):
            b   = self._bars[j]
            o   = float(b.open)
            c   = float(b.close)
            hi  = float(b.high)
            lo  = float(b.low)
            rng = hi - lo
            buf = rng * self.BUF_PCT / 100

            if bullish and c < o:       # niedźwiedzia świeca → bullish OB
                tgt = self._last_sh if self._last_sh else hi + 50
                self._obs.append({
                    'side': 1, 'top': hi + buf, 'bot': lo - buf,
                    'age': 0, 'target': tgt
                })
                self.log(f"OB BYCZY  {lo:.2f}–{hi:.2f}  (target={tgt:.2f})")
                return

            if not bullish and c > o:   # bycza świeca → bearish OB
                tgt = self._last_sl if self._last_sl else lo - 50
                self._obs.append({
                    'side': -1, 'top': hi + buf, 'bot': lo - buf,
                    'age': 0, 'target': tgt
                })
                self.log(f"OB NIEDŹWIEDZI  {lo:.2f}–{hi:.2f}  (target={tgt:.2f})")
                return

    # ── sprawdzenie wejścia ────────────────────────────────────────────────

    def _check_entry(self, price: float, bar_hi: float, bar_lo: float):
        for ob in self._obs[:]:
            # Bar musi nakładać się na strefę OB
            if bar_lo > ob['top'] or bar_hi < ob['bot']:
                continue

            side = ob['side']

            # Nie wchodzimy, jeśli cena przebiła przez OB w złym kierunku
            if side == 1  and price < ob['bot']: continue
            if side == -1 and price > ob['top']: continue

            stop = ob['bot'] if side == 1 else ob['top']
            dist = abs(price - stop)
            if dist < 0.10: continue   # strefa za ciasna

            target = price + dist * self.RR if side == 1 else price - dist * self.RR

            risk = float(self.portfolio.total_portfolio_value) * self.RISK / 100
            qty  = max(1, int(risk / dist))

            self.market_order(self._sym, side * qty)
            self._stop_ticket   = self.stop_market_order(self._sym, -side * qty, stop)
            self._target_ticket = self.limit_order(self._sym, -side * qty, target)

            self.log(
                f"WEJŚCIE {'LONG' if side==1 else 'SHORT'} | "
                f"cena={price:.2f}  SL={stop:.2f}  TP={target:.2f}  "
                f"qty={qty}  dist={dist:.2f}  R={dist*qty:.0f}$"
            )
            self._obs.remove(ob)
            break

    # ── zarządzanie zleceniami ─────────────────────────────────────────────

    def on_order_event(self, order_event):
        if order_event.status != OrderStatus.FILLED: return
        if not (self._stop_ticket or self._target_ticket): return

        sid = self._stop_ticket.order_id   if self._stop_ticket   else -1
        tid = self._target_ticket.order_id if self._target_ticket else -1

        if order_event.order_id == sid:
            if self._target_ticket:
                self._target_ticket.cancel("stop hit")
            self.log(f"STOP @ {order_event.fill_price:.2f}")
            self._stop_ticket = self._target_ticket = None

        elif order_event.order_id == tid:
            if self._stop_ticket:
                self._stop_ticket.cancel("target hit")
            self.log(f"TARGET @ {order_event.fill_price:.2f}")
            self._stop_ticket = self._target_ticket = None

    def on_end_of_algorithm(self):
        if self._stop_ticket:   self._stop_ticket.cancel("end")
        if self._target_ticket: self._target_ticket.cancel("end")
        final = float(self.portfolio.total_portfolio_value)
        ret   = (final - 50_000) / 50_000 * 100
        self.log(f"Wynik końcowy: ${final:,.2f}  ({ret:+.1f}%)")
