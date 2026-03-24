from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import httpx
import os
import json
from datetime import datetime

app = FastAPI(title="Ambient Signal API")

# Turso database configuration (remove any whitespace/newlines from env vars)
TURSO_URL = "".join(os.getenv("TURSO_URL", "").split())
TURSO_AUTH_TOKEN = "".join(os.getenv("TURSO_AUTH_TOKEN", "").split())

# Convert to HTTPS URL for HTTP API (Pipeline endpoint)
BASE_URL = TURSO_URL.replace("libsql://", "https://") if TURSO_URL else ""
PIPELINE_URL = f"{BASE_URL}/v2/pipeline" if BASE_URL else ""


def execute_sql(sql, args=None):
    """Execute SQL via Turso HTTP Pipeline API"""
    if not PIPELINE_URL:
        raise Exception("TURSO_URL not configured")

    # Build the pipeline request
    if args:
        # Convert args to Turso format
        turso_args = []
        for arg in args:
            if arg is None:
                turso_args.append({"type": "null"})
            elif isinstance(arg, int):
                turso_args.append({"type": "integer", "value": str(arg)})
            elif isinstance(arg, float):
                turso_args.append({"type": "float", "value": arg})
            else:
                turso_args.append({"type": "text", "value": str(arg)})

        request_body = {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": turso_args}},
                {"type": "close"}
            ]
        }
    else:
        request_body = {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql}},
                {"type": "close"}
            ]
        }

    response = httpx.post(
        PIPELINE_URL,
        headers={
            "Authorization": f"Bearer {TURSO_AUTH_TOKEN}",
            "Content-Type": "application/json"
        },
        json=request_body,
        timeout=30.0
    )

    if response.status_code != 200:
        raise Exception(f"Turso API error: {response.status_code} - {response.text}")

    data = response.json()

    # Parse pipeline response
    if "results" in data and len(data["results"]) > 0:
        result = data["results"][0]
        if "response" in result and "result" in result["response"]:
            res = result["response"]["result"]
            cols = [c["name"] for c in res.get("cols", [])]
            rows = []
            for row in res.get("rows", []):
                rows.append([cell.get("value") for cell in row])
            return {"columns": cols, "rows": rows}

    return {"columns": [], "rows": []}


# Initialize database
def init_db():
    execute_sql("""
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
    execute_sql("CREATE INDEX IF NOT EXISTS idx_device_id ON signals(device_id)")
    execute_sql("CREATE INDEX IF NOT EXISTS idx_timestamp ON signals(timestamp)")


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
    execute_sql(
        "INSERT INTO signals (timestamp, device_id, signal_type, value, unit, raw_data) VALUES (?, ?, ?, ?, ?, ?)",
        [timestamp, signal.device_id, signal.signal_type, signal.value, signal.unit, None]
    )
    return {"status": "ok", "received": 1, "timestamp": timestamp}


# Batch signal endpoint (recommended for mobile)
@app.post("/signals/batch")
def receive_batch_signals(batch: SignalBatch):
    server_timestamp = datetime.utcnow().isoformat()
    inserted = 0
    for reading in batch.readings:
        ts = reading.timestamp or server_timestamp
        execute_sql(
            "INSERT INTO signals (timestamp, device_id, signal_type, value, unit, raw_data) VALUES (?, ?, ?, ?, ?, ?)",
            [ts, batch.device_id, reading.signal_type, reading.value, reading.unit, None]
        )
        inserted += 1
    return {
        "status": "ok",
        "received": inserted,
        "device_id": batch.device_id,
        "server_timestamp": server_timestamp
    }


# Helper to convert result set rows to dicts
def result_to_dicts(result):
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    return [dict(zip(columns, row)) for row in rows]


# Get recent signals
@app.get("/signals")
def get_signals(device_id: Optional[str] = None, limit: int = 100):
    if device_id:
        result = execute_sql(
            "SELECT * FROM signals WHERE device_id = ? ORDER BY id DESC LIMIT ?",
            [device_id, limit]
        )
    else:
        result = execute_sql(
            "SELECT * FROM signals ORDER BY id DESC LIMIT ?",
            [limit]
        )
    signals = result_to_dicts(result)
    return {"count": len(signals), "signals": signals}


# Get signal stats (for dashboard)
@app.get("/signals/stats")
def get_signal_stats(device_id: Optional[str] = None):
    if device_id:
        result = execute_sql("""
            SELECT
                signal_type,
                COUNT(*) as count,
                AVG(value) as avg_value,
                MIN(value) as min_value,
                MAX(value) as max_value
            FROM signals
            WHERE device_id = ?
            GROUP BY signal_type
        """, [device_id])
    else:
        result = execute_sql("""
            SELECT
                signal_type,
                COUNT(*) as count,
                AVG(value) as avg_value,
                MIN(value) as min_value,
                MAX(value) as max_value
            FROM signals
            GROUP BY signal_type
        """)
    return {"stats": result_to_dicts(result)}


# Clear all signals (for testing)
@app.delete("/signals")
def clear_signals():
    execute_sql("DELETE FROM signals")
    return {"status": "cleared"}


# Dashboard HTML
@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ambient Signal Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }
        h1 { text-align: center; margin-bottom: 20px; color: #38bdf8; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: #1e293b; padding: 20px; border-radius: 10px; text-align: center; }
        .stat-card h3 { color: #94a3b8; font-size: 14px; text-transform: uppercase; }
        .stat-card .value { font-size: 32px; font-weight: bold; color: #38bdf8; margin: 10px 0; }
        .stat-card .meta { font-size: 12px; color: #64748b; }
        .chart-container { background: #1e293b; padding: 20px; border-radius: 10px; margin-bottom: 20px; height: 300px; }
        table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 10px; overflow: hidden; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #334155; }
        th { background: #334155; color: #38bdf8; font-weight: 600; }
        tr:hover { background: #334155; }
        .status { padding: 10px; text-align: center; color: #64748b; font-size: 14px; }
        .refresh-info { text-align: center; margin-bottom: 15px; color: #64748b; font-size: 12px; }
    </style>
</head>
<body>
    <h1>Ambient Signal Dashboard</h1>
    <p class="refresh-info">Live updates every 500ms</p>

    <div class="stats-grid" id="stats-grid"></div>

    <div class="chart-container">
        <canvas id="chart"></canvas>
    </div>

    <table>
        <thead>
            <tr>
                <th>ID</th>
                <th>Timestamp</th>
                <th>Device</th>
                <th>Signal Type</th>
                <th>Value</th>
                <th>Unit</th>
            </tr>
        </thead>
        <tbody id="table-body">
            <tr><td colspan="6" class="status">Loading...</td></tr>
        </tbody>
    </table>

    <script>
        let chart = null;
        const colors = ['#38bdf8', '#34d399', '#fbbf24', '#f87171', '#a78bfa', '#fb7185'];

        async function fetchData() {
            try {
                const [signalsRes, statsRes] = await Promise.all([
                    fetch('/signals?limit=100'),
                    fetch('/signals/stats')
                ]);
                const signals = await signalsRes.json();
                const stats = await statsRes.json();

                updateStats(stats.stats);
                updateTable(signals.signals);
                updateChart(signals.signals);
            } catch (e) {
                console.error('Fetch error:', e);
            }
        }

        function updateStats(stats) {
            const grid = document.getElementById('stats-grid');
            grid.innerHTML = stats.map((s, i) => `
                <div class="stat-card">
                    <h3>${s.signal_type}</h3>
                    <div class="value" style="color: ${colors[i % colors.length]}">${s.avg_value.toFixed(1)}</div>
                    <div class="meta">Min: ${s.min_value.toFixed(1)} | Max: ${s.max_value.toFixed(1)} | Count: ${s.count}</div>
                </div>
            `).join('');
        }

        function updateTable(signals) {
            const tbody = document.getElementById('table-body');
            if (signals.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="status">No data yet. Send some signals!</td></tr>';
                return;
            }
            tbody.innerHTML = signals.map(s => `
                <tr>
                    <td>${s.id}</td>
                    <td>${new Date(s.timestamp).toLocaleString()}</td>
                    <td>${s.device_id}</td>
                    <td>${s.signal_type}</td>
                    <td>${s.value}</td>
                    <td>${s.unit || '-'}</td>
                </tr>
            `).join('');
        }

        function updateChart(signals) {
            const ctx = document.getElementById('chart').getContext('2d');

            // Group by signal type (keep chronological order)
            const grouped = {};
            [...signals].reverse().forEach(s => {
                if (!grouped[s.signal_type]) grouped[s.signal_type] = [];
                grouped[s.signal_type].push({ x: new Date(s.timestamp), y: s.value });
            });

            const datasets = Object.entries(grouped).map(([type, data], i) => ({
                label: type,
                data: data.slice(-50),  // Last 50 points per signal type
                borderColor: colors[i % colors.length],
                backgroundColor: colors[i % colors.length] + '20',
                tension: 0.3,
                fill: false,
                pointRadius: 2
            }));

            if (chart) {
                chart.data.datasets = datasets;
                chart.update('none');
            } else {
                chart = new Chart(ctx, {
                    type: 'line',
                    data: { datasets },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: { duration: 0 },
                        scales: {
                            x: {
                                type: 'timeseries',
                                time: { unit: 'second', displayFormats: { second: 'HH:mm:ss' } },
                                grid: { color: '#334155' },
                                ticks: { color: '#94a3b8', maxTicksLimit: 8 }
                            },
                            y: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } }
                        },
                        plugins: { legend: { labels: { color: '#e2e8f0' } } }
                    }
                });
            }
        }

        // Initial fetch and auto-refresh every 500ms
        fetchData();
        setInterval(fetchData, 500);
    </script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
</body>
</html>
"""
