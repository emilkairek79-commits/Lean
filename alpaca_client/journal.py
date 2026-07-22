"""Trade and risk-decision journaling to CSV or SQLite."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ORDER_FIELDS = [
    "timestamp", "order_id", "symbol", "side", "qty",
    "order_type", "price", "status", "entry_reason",
]

DECISION_FIELDS = [
    "timestamp", "symbol", "side", "qty", "allowed", "reason", "entry_reason",
]


class TradeJournal:
    def __init__(self, backend: str = "csv", path: str | Path = "trades.csv"):
        if backend not in ("csv", "sqlite"):
            raise ValueError("backend must be 'csv' or 'sqlite'")
        self.backend = backend
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        if self.backend == "sqlite":
            self._init_sqlite()

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS orders (
                    timestamp TEXT, order_id TEXT, symbol TEXT, side TEXT,
                    qty REAL, order_type TEXT, price REAL, status TEXT, entry_reason TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS decisions (
                    timestamp TEXT, symbol TEXT, side TEXT, qty REAL,
                    allowed INTEGER, reason TEXT, entry_reason TEXT
                )"""
            )

    def _write_csv(self, path: Path, fields: list[str], row: dict) -> None:
        file_exists = path.exists()
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

    def log_order(
        self,
        *,
        order_id: Optional[str],
        symbol: str,
        side: str,
        qty: float,
        order_type: str,
        price: float,
        status: str,
        entry_reason: Optional[str] = None,
    ) -> None:
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "order_id": order_id or "",
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "order_type": order_type,
            "price": price,
            "status": status,
            "entry_reason": entry_reason or "",
        }
        if self.backend == "csv":
            self._write_csv(self.path, ORDER_FIELDS, row)
        else:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "INSERT INTO orders VALUES (:timestamp, :order_id, :symbol, :side, "
                    ":qty, :order_type, :price, :status, :entry_reason)",
                    row,
                )

    def log_decision(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        allowed: bool,
        reason: str,
        entry_reason: Optional[str] = None,
    ) -> None:
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "allowed": allowed,
            "reason": reason,
            "entry_reason": entry_reason or "",
        }
        if self.backend == "csv":
            decisions_path = self.path.with_name(self.path.stem + "_decisions" + self.path.suffix)
            self._write_csv(decisions_path, DECISION_FIELDS, row)
        else:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    "INSERT INTO decisions VALUES (:timestamp, :symbol, :side, :qty, "
                    ":allowed, :reason, :entry_reason)",
                    {**row, "allowed": int(row["allowed"])},
                )
