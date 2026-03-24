from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager

app = FastAPI(title="Ambient Signal API")

DATABASE = "signals.db"


# Database connection manager
@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# Initialize database
def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                device_id TEXT NOT NULL,
                signal_type TEXT,
                value REAL,
                unit TEXT,
                raw_data TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_device_id ON signals(device_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON signals(timestamp)")
        conn.commit()


# Pydantic models
class SignalReading(BaseModel):
    signal_type: str          # e.g., "temperature", "humidity", "light", "noise"
    value: float              # numeric value
    unit: Optional[str] = None  # e.g., "celsius", "percent", "lux", "db"
    timestamp: Optional[str] = None  # optional client timestamp


class SignalBatch(BaseModel):
    device_id: str
    readings: list[SignalReading]


class SingleSignal(BaseModel):
    device_id: str
    signal_type: str
    value: float
    unit: Optional[str] = None


# Initialize DB on startup
@app.on_event("startup")
def startup():
    init_db()


# Health check
@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# Single signal endpoint (for simple testing)
@app.post("/signal")
def receive_single_signal(signal: SingleSignal):
    timestamp = datetime.utcnow().isoformat()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO signals (timestamp, device_id, signal_type, value, unit, raw_data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (timestamp, signal.device_id, signal.signal_type, signal.value, signal.unit, None)
        )
        conn.commit()

    return {"status": "ok", "received": 1, "timestamp": timestamp}


# Batch signal endpoint (recommended for mobile)
@app.post("/signals/batch")
def receive_batch_signals(batch: SignalBatch):
    server_timestamp = datetime.utcnow().isoformat()

    with get_db() as conn:
        inserted = 0
        for reading in batch.readings:
            # Use client timestamp if provided, otherwise server timestamp
            ts = reading.timestamp or server_timestamp
            conn.execute(
                """INSERT INTO signals (timestamp, device_id, signal_type, value, unit, raw_data)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ts, batch.device_id, reading.signal_type, reading.value, reading.unit, None)
            )
            inserted += 1
        conn.commit()

    return {
        "status": "ok",
        "received": inserted,
        "device_id": batch.device_id,
        "server_timestamp": server_timestamp
    }


# Get recent signals
@app.get("/signals")
def get_signals(device_id: Optional[str] = None, limit: int = 100):
    with get_db() as conn:
        if device_id:
            rows = conn.execute(
                "SELECT * FROM signals WHERE device_id = ? ORDER BY id DESC LIMIT ?",
                (device_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()

    return {
        "count": len(rows),
        "signals": [dict(row) for row in rows]
    }


# Get signal stats (for dashboard)
@app.get("/signals/stats")
def get_signal_stats(device_id: Optional[str] = None):
    with get_db() as conn:
        if device_id:
            stats = conn.execute("""
                SELECT
                    signal_type,
                    COUNT(*) as count,
                    AVG(value) as avg_value,
                    MIN(value) as min_value,
                    MAX(value) as max_value
                FROM signals
                WHERE device_id = ?
                GROUP BY signal_type
            """, (device_id,)).fetchall()
        else:
            stats = conn.execute("""
                SELECT
                    signal_type,
                    COUNT(*) as count,
                    AVG(value) as avg_value,
                    MIN(value) as min_value,
                    MAX(value) as max_value
                FROM signals
                GROUP BY signal_type
            """).fetchall()

    return {"stats": [dict(row) for row in stats]}


# Clear all signals (for testing)
@app.delete("/signals")
def clear_signals():
    with get_db() as conn:
        conn.execute("DELETE FROM signals")
        conn.commit()
    return {"status": "cleared"}
