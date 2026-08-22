# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# Lean Algorithmic Trading Engine v2.0. Copyright 2014 QuantConnect Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

from AlgorithmImports import *

### <summary>
### Grid Trading Bot — SOL/USD H1
###
### Logika:
###   1. Zdefiniuj siatkę N poziomów między LOWER a UPPER
###   2. Przy starcie: BUY limit poniżej ceny, SELL limit powyżej
###   3. BUY wypełniony → natychmiast postaw SELL o jeden poziom wyżej
###   4. SELL wypełniony → natychmiast postaw BUY o jeden poziom niżej
###   5. Zysk z każdego round-trip = STEP * qty (minus prowizje)
###
### Najlepiej działa w range-boundmarketach z wysoką zmiennością.
### </summary>
### <meta name="tag" content="grid" />
### <meta name="tag" content="solana" />
### <meta name="tag" content="crypto" />
class SOLGridAlgorithm(QCAlgorithm):

    # ── parametry siatki ───────────────────────────────────────────────────────
    UPPER  = 200.0   # górna granica siatki ($)
    LOWER  =  80.0   # dolna granica siatki ($)
    LEVELS =  20     # liczba równych poziomów
    INVEST = 50_000  # kapitał alokowany na siatkę ($)
    # ──────────────────────────────────────────────────────────────────────────

    def initialize(self):
        self.set_start_date(2024, 1, 1)
        self.set_end_date(2024, 12, 31)
        self.set_cash(self.INVEST)

        crypto    = self.add_crypto("SOLUSD", Resolution.HOUR, Market.COINBASE)
        self._sym = crypto.symbol

        # Parametry siatki
        self._step = (self.UPPER - self.LOWER) / self.LEVELS
        self._grid = [round(self.LOWER + i * self._step, 4)
                      for i in range(self.LEVELS + 1)]

        # Ilość SOL na poziom: równy podział kapitału po średniej cenie siatki
        mid_price         = (self.UPPER + self.LOWER) / 2
        self._qty         = round((self.INVEST / self.LEVELS) / mid_price, 4)

        # Mapa order_id → metadata
        self._tickets: dict[int, dict] = {}

        # Statystyki
        self._grid_profit   = 0.0
        self._round_trips   = 0
        self._ready         = False

        self.log(
            f"Siatka SOL/USD | {self.LOWER}–{self.UPPER}$ | "
            f"{self.LEVELS} poziomów | krok={self._step:.2f}$ | qty={self._qty}"
        )
        self.set_warm_up(2, Resolution.HOUR)

    # ── inicjalizacja siatki ───────────────────────────────────────────────────

    def on_data(self, data):
        if self.is_warming_up: return
        if not data.bars.contains_key(self._sym): return
        if self._ready: return

        price = float(data.bars[self._sym].close)
        self._place_initial_grid(price)
        self._ready = True

    def _place_initial_grid(self, current_price: float):
        buys = sells = 0
        for i, level in enumerate(self._grid[:-1]):
            if level < current_price:
                t = self.limit_order(self._sym, self._qty, level)
                self._tickets[t.order_id] = {'type': 'buy', 'level': level}
                buys += 1
            else:
                sell_price = level + self._step
                t = self.limit_order(self._sym, -self._qty, sell_price)
                self._tickets[t.order_id] = {'type': 'sell', 'level': level}
                sells += 1

        self.log(
            f"Siatka aktywna @ {current_price:.2f}$ | "
            f"{buys} BUY + {sells} SELL = {buys+sells} zleceń"
        )

    # ── obsługa wypełnień ──────────────────────────────────────────────────────

    def on_order_event(self, order_event):
        if order_event.status != OrderStatus.FILLED: return

        meta = self._tickets.pop(order_event.order_id, None)
        if meta is None: return

        lvl   = meta['level']
        otype = meta['type']
        fill  = float(order_event.fill_price)

        if otype == 'buy':
            sell_price = lvl + self._step
            if sell_price <= self.UPPER:
                t = self.limit_order(self._sym, -self._qty, sell_price)
                self._tickets[t.order_id] = {'type': 'sell', 'level': lvl}
            self.log(f"BUY  @{fill:.2f}$ → SELL @{sell_price:.2f}$")

        elif otype == 'sell':
            profit = self._step * self._qty
            self._grid_profit  += profit
            self._round_trips  += 1

            buy_price = lvl
            if buy_price >= self.LOWER:
                t = self.limit_order(self._sym, self._qty, buy_price)
                self._tickets[t.order_id] = {'type': 'buy', 'level': buy_price}

            self.log(
                f"SELL @{fill:.2f}$  zysk={profit:.2f}$  "
                f"łącznie={self._grid_profit:.2f}$  trip #{self._round_trips}"
            )

    # ── koniec ────────────────────────────────────────────────────────────────

    def on_end_of_algorithm(self):
        for oid in list(self._tickets.keys()):
            t = self.transactions.get_order_ticket(oid)
            if t: t.cancel("koniec algorytmu")

        final = float(self.portfolio.total_portfolio_value)
        ret   = (final - self.INVEST) / self.INVEST * 100
        fee_est = self._round_trips * self._qty * (self.LOWER + self.UPPER) / 2 * 0.006

        self.log("=" * 60)
        self.log(f"Kapitał końcowy : ${final:,.2f}")
        self.log(f"Zwrot całkowity : {ret:+.1f}%")
        self.log(f"Round-trips     : {self._round_trips}")
        self.log(f"Zysk z siatki   : ${self._grid_profit:.2f}")
        self.log(f"Szac. prowizje  : ${fee_est:.2f}  (0.6% taker)")
        self.log(f"Zysk netto est. : ${self._grid_profit - fee_est:.2f}")
        self.log("=" * 60)
