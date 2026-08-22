#!/usr/bin/env python3
"""
Live Grid Trading Bot — SOL/USD

Standalone bot (nie Lean) do handlu na żywo przez CCXT.
Odpowiednik logiki z SOLGridAlgorithm.py, ale z obsługą realnych zleceń.

URUCHOMIENIE:
    pip install ccxt
    export GRID_API_KEY="..."
    export GRID_API_SECRET="..."
    python live_grid_bot.py

DOMYŚLNIE DRY_RUN=True — nie wysyła żadnych zleceń, tylko loguje co by zrobił.
Ustaw DRY_RUN=False dopiero po przetestowaniu na sandboxie.
"""

import ccxt
import json
import logging
import os
import signal
import sys
import time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════
#  KONFIGURACJA
# ═══════════════════════════════════════════════════════════════════════════

DRY_RUN = True          # ⚠️  False = prawdziwe zlecenia za prawdziwe pieniądze

EXCHANGE_ID = "kraken"  # kraken | coinbaseadvanced | binance
SYMBOL      = "SOL/USD"

UPPER   = 200.0         # górna granica siatki ($)
LOWER   = 80.0          # dolna granica siatki ($)
LEVELS  = 20            # liczba poziomów
CAPITAL = 21971.0       # kapitał alokowany ($)

# ── Zabezpieczenia ─────────────────────────────────────────────────────────
KILL_SWITCH_UPPER = 240.0   # anuluj wszystko i zatrzymaj, gdy cena > tego
KILL_SWITCH_LOWER = 65.0    # anuluj wszystko i zatrzymaj, gdy cena < tego
MAX_DAILY_TRADES  = 200     # bezpiecznik przed pętlą zleceń
POLL_SECONDS      = 15      # co ile sekund sprawdzać wypełnienia

STATE_FILE = Path("grid_state.json")
LOG_FILE   = Path("grid_bot.log")

# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("grid")


class GridBot:
    def __init__(self):
        self.step = (UPPER - LOWER) / LEVELS
        self.levels = [round(LOWER + i * self.step, 8) for i in range(LEVELS + 1)]

        mid = (UPPER + LOWER) / 2
        self.qty = (CAPITAL / LEVELS) / mid

        self.exchange = self._connect()
        self.market = self.exchange.market(SYMBOL)

        # Dopasuj qty do precyzji giełdy
        self.qty = float(self.exchange.amount_to_precision(SYMBOL, self.qty))
        self._check_min_size()

        # order_id -> {"side", "level_price", "counter_price"}
        self.orders: dict[str, dict] = {}
        self.grid_profit = 0.0
        self.round_trips = 0
        self.trades_today = 0
        self.running = True

        log.info("─" * 70)
        log.info(f"Siatka {SYMBOL} na {EXCHANGE_ID}")
        log.info(f"Zakres     : ${LOWER:.2f} – ${UPPER:.2f}")
        log.info(f"Poziomy    : {LEVELS}  (krok ${self.step:.2f})")
        log.info(f"Qty/poziom : {self.qty} SOL")
        log.info(f"Zysk/cykl  : ${self.step * self.qty:.2f} brutto")
        log.info(f"Kill switch: <${KILL_SWITCH_LOWER} lub >${KILL_SWITCH_UPPER}")
        log.info(f"TRYB       : {'DRY RUN (bez zleceń)' if DRY_RUN else '*** LIVE ***'}")
        log.info("─" * 70)

    # ── połączenie ─────────────────────────────────────────────────────────

    def _connect(self):
        key = os.environ.get("GRID_API_KEY")
        secret = os.environ.get("GRID_API_SECRET")
        if not DRY_RUN and not (key and secret):
            sys.exit("Brak GRID_API_KEY / GRID_API_SECRET w zmiennych środowiskowych.")

        cls = getattr(ccxt, EXCHANGE_ID)
        ex = cls({
            "apiKey": key,
            "secret": secret,
            "enableRateLimit": True,      # CCXT sam pilnuje limitów
            "options": {"defaultType": "spot"},
        })
        ex.load_markets()
        if SYMBOL not in ex.markets:
            sys.exit(f"{SYMBOL} niedostępny na {EXCHANGE_ID}")
        return ex

    def _check_min_size(self):
        min_amt = (self.market.get("limits", {}).get("amount", {}) or {}).get("min")
        if min_amt and self.qty < min_amt:
            sys.exit(
                f"Qty {self.qty} < minimum giełdy {min_amt}. "
                f"Zmniejsz LEVELS albo zwiększ CAPITAL."
            )

    # ── stan (odzyskiwanie po restarcie) ───────────────────────────────────

    def save_state(self):
        STATE_FILE.write_text(json.dumps({
            "orders": self.orders,
            "grid_profit": self.grid_profit,
            "round_trips": self.round_trips,
            "trades_today": self.trades_today,
        }, indent=2))

    def load_state(self) -> bool:
        if not STATE_FILE.exists():
            return False
        data = json.loads(STATE_FILE.read_text())
        self.orders = data.get("orders", {})
        self.grid_profit = data.get("grid_profit", 0.0)
        self.round_trips = data.get("round_trips", 0)
        self.trades_today = data.get("trades_today", 0)
        log.info(f"Wczytano stan: {len(self.orders)} zleceń, "
                 f"${self.grid_profit:.2f} zysku, {self.round_trips} cykli")
        return True

    def reconcile(self):
        """Uzgodnij stan lokalny z rzeczywistymi zleceniami na giełdzie."""
        if DRY_RUN:
            return
        live_ids = {o["id"] for o in self.exchange.fetch_open_orders(SYMBOL)}
        stale = [oid for oid in self.orders if oid not in live_ids]
        for oid in stale:
            log.warning(f"Zlecenie {oid} zniknęło z giełdy — sprawdzam status")
            self._handle_vanished(oid)
        orphans = live_ids - set(self.orders)
        if orphans:
            log.warning(f"{len(orphans)} zleceń na giełdzie bez wpisu lokalnego "
                        f"— anuluję je: {orphans}")
            for oid in orphans:
                self._cancel(oid)

    # ── zlecenia ───────────────────────────────────────────────────────────

    def _price(self) -> float:
        return float(self.exchange.fetch_ticker(SYMBOL)["last"])

    def _place(self, side: str, price: float, level_price: float):
        price = float(self.exchange.price_to_precision(SYMBOL, price))
        if DRY_RUN:
            oid = f"dry-{side}-{price}"
            log.info(f"[DRY] {side.upper():4} {self.qty} @ ${price:.2f}")
        else:
            try:
                order = self.exchange.create_limit_order(SYMBOL, side, self.qty, price)
                oid = order["id"]
                log.info(f"{side.upper():4} {self.qty} @ ${price:.2f}  (id {oid})")
            except ccxt.InsufficientFunds:
                log.error(f"Brak środków na {side} @ ${price:.2f} — pomijam poziom")
                return
            except ccxt.BaseError as e:
                log.error(f"Nie udało się złożyć {side} @ ${price:.2f}: {e}")
                return

        counter = price + self.step if side == "buy" else price - self.step
        self.orders[oid] = {
            "side": side,
            "price": price,
            "level_price": level_price,
            "counter_price": counter,
        }

    def _cancel(self, oid: str):
        if not DRY_RUN:
            try:
                self.exchange.cancel_order(oid, SYMBOL)
            except ccxt.OrderNotFound:
                pass
            except ccxt.BaseError as e:
                log.error(f"Nie udało się anulować {oid}: {e}")
        self.orders.pop(oid, None)

    # ── budowa siatki ──────────────────────────────────────────────────────

    def build_grid(self):
        price = self._price()
        log.info(f"Cena startowa: ${price:.2f}")

        if not (LOWER <= price <= UPPER):
            sys.exit(f"Cena ${price:.2f} poza zakresem siatki — nie startuję.")

        buys = sells = 0
        for lvl in self.levels[:-1]:
            if lvl < price:
                self._place("buy", lvl, lvl)
                buys += 1
            else:
                self._place("sell", lvl + self.step, lvl)
                sells += 1

        log.info(f"Siatka gotowa: {buys} BUY + {sells} SELL")
        self.save_state()

    # ── obsługa wypełnień ──────────────────────────────────────────────────

    def _handle_vanished(self, oid: str):
        """Zlecenie zniknęło z open_orders — sprawdź czy wypełnione czy anulowane."""
        meta = self.orders.pop(oid, None)
        if meta is None:
            return
        try:
            order = self.exchange.fetch_order(oid, SYMBOL)
            status = order.get("status")
        except ccxt.BaseError as e:
            log.error(f"Nie mogę sprawdzić {oid}: {e} — przywracam do stanu")
            self.orders[oid] = meta
            return

        if status != "closed":
            log.info(f"Zlecenie {oid} zakończone jako '{status}' — bez akcji")
            return

        self._on_fill(meta, float(order.get("average") or meta["price"]))

    def _on_fill(self, meta: dict, fill_price: float):
        self.trades_today += 1
        side = meta["side"]
        counter = meta["counter_price"]

        if side == "buy":
            log.info(f"✓ BUY  wypełniony @ ${fill_price:.2f} → stawiam SELL @ ${counter:.2f}")
            if counter <= UPPER:
                self._place("sell", counter, meta["level_price"])
        else:
            profit = self.step * self.qty
            self.grid_profit += profit
            self.round_trips += 1
            log.info(
                f"✓ SELL wypełniony @ ${fill_price:.2f}  +${profit:.2f} brutto  "
                f"│ suma ${self.grid_profit:.2f} │ cykl #{self.round_trips} "
                f"→ stawiam BUY @ ${counter:.2f}"
            )
            if counter >= LOWER:
                self._place("buy", counter, counter)

        self.save_state()

    def poll(self):
        if DRY_RUN:
            return
        live_ids = {o["id"] for o in self.exchange.fetch_open_orders(SYMBOL)}
        for oid in [o for o in self.orders if o not in live_ids]:
            self._handle_vanished(oid)

    # ── zabezpieczenia ─────────────────────────────────────────────────────

    def check_limits(self) -> bool:
        price = self._price()

        if price >= KILL_SWITCH_UPPER or price <= KILL_SWITCH_LOWER:
            log.critical(f"KILL SWITCH — cena ${price:.2f} poza granicami bezpieczeństwa")
            return False

        if self.trades_today >= MAX_DAILY_TRADES:
            log.critical(f"KILL SWITCH — limit {MAX_DAILY_TRADES} transakcji osiągnięty")
            return False

        if not (LOWER <= price <= UPPER):
            log.warning(f"Cena ${price:.2f} poza siatką — bot czeka, nie stawia nowych")

        return True

    def shutdown(self, cancel_orders: bool = True):
        self.running = False
        log.info("─" * 70)
        if cancel_orders and self.orders:
            log.info(f"Anuluję {len(self.orders)} otwartych zleceń...")
            for oid in list(self.orders):
                self._cancel(oid)
        log.info(f"Zysk z siatki : ${self.grid_profit:.2f} brutto")
        log.info(f"Round-trips   : {self.round_trips}")
        log.info(f"Transakcje    : {self.trades_today}")
        log.info("─" * 70)
        self.save_state()

    # ── pętla główna ───────────────────────────────────────────────────────

    def run(self):
        if self.load_state() and self.orders:
            log.info("Wznawiam z zapisanego stanu")
            self.reconcile()
        else:
            self.build_grid()

        while self.running:
            try:
                if not self.check_limits():
                    self.shutdown(cancel_orders=True)
                    break
                self.poll()
                time.sleep(POLL_SECONDS)
            except ccxt.NetworkError as e:
                log.warning(f"Błąd sieci: {e} — ponawiam za 30s")
                time.sleep(30)
            except ccxt.BaseError as e:
                log.error(f"Błąd giełdy: {e} — ponawiam za 60s")
                time.sleep(60)


def main():
    bot = GridBot()

    def on_signal(signum, frame):
        log.info("Otrzymano sygnał zatrzymania")
        bot.shutdown(cancel_orders=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    bot.run()


if __name__ == "__main__":
    main()
