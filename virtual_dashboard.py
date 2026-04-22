# =============================================================
# virtual_dashboard.py — Generates a Premium HTML Dashboard
# =============================================================

import os
import json
import time

def generate_dashboard(stats):
    """
    Creates a premium-look HTML file with charts and live-updating logic.
    """
    mode = str(stats.get("mode", "VIRTUAL")).upper()
    filename = f"{mode.lower()}_dashboard.html"
    
    history_points = []
    initial_bal = float(stats.get("initial_balance", 1000))
    history_points.append({"t": 0, "b": initial_bal})
    
    # Process history for the chart
    hist = stats.get("history", [])
    start_ts = hist[0]["timestamp"] if hist else time.time()
    
    for entry in hist:
        if "balance_after" in entry:
            try:
                t_val = round((entry["timestamp"] - start_ts) / 60, 1)
                b_val = entry["balance_after"]
                history_points.append({"t": t_val, "b": b_val})
            except Exception: pass

    labels_list = [p["t"] for p in history_points]
    data_list = [p["b"] for p in history_points]
    
    # Pre-calculate UI helpers
    status_col = "#02c076" if mode == "LIVE" else "#00d4ff"
    status_brd = "rgba(2, 192, 118, 0.2)" if mode == "LIVE" else "rgba(0, 212, 255, 0.2)"
    pnl_val = float(stats.get("pnl", 0))
    pnl_class = "up" if pnl_val >= 0 else "down"
    pnl_sign = "+" if pnl_val >= 0 else ""
    pnl_pc = float(stats.get("pnl_percent", 0))
    curr_bal = float(stats.get("current_balance", initial_bal))
    avg_pnl = float(stats.get("avg_pnl_hour", 0))
    max_gale = int(stats.get("max_gale", 0))
    cap_req = float(stats.get("capital_required", 0))
    elapsed = float(stats.get("elapsed_hours", 0))

    # Build history rows
    rows_html = ""
    for t in reversed(hist[-10:]):
        ts = time.strftime("%H:%M:%S", time.localtime(t.get("timestamp", 0)))
        act = str(t.get("type", "INFO"))
        mkt = str(t.get("market", "---"))
        prc = float(t.get("price", t.get("payout", 0)))
        
        if "size_usdc" in t:
            amt = f"${float(t['size_usdc']):.2f}"
        else:
            amt = f"{float(t.get('shares', 0)):.2f} sh"
        
        b_after = float(t.get("balance_after", 0))
        
        rows_html += f"<tr><td>{ts}</td><td class='type-{act.lower()}'>{act}</td>"
        rows_html += f"<td>{mkt}</td><td>${prc:.3f}</td><td>{amt}</td><td>${b_after:.2f}</td></tr>"

    # HTML Body parts to avoid massive f-string complexity
    head = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Bot {mode}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron&family=Inter&display=swap" rel="stylesheet">
    <script>
        // Auto-refresh every 15 seconds to keep dashboard live
        setTimeout(() => {{ location.reload(); }}, 15000);
    </script>
    <style>
        :root {{ --bg: #0b0e11; --card: rgba(23, 27, 34, 0.8); --pri: #00d4ff; --sec: #9d50bb; --txt: #eaecef; --dim: #848e9c; --gr: #02c076; --rd: #f84960; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: 'Inter', sans-serif; }}
        body {{ background: var(--bg); color: var(--txt); padding: 40px; }}
        header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 40px; }}
        h1 {{ font-family: 'Orbitron'; background: linear-gradient(90deg, var(--pri), var(--sec)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
        .status-badge {{ border: 1px solid {status_brd}; color: {status_col}; padding: 8px 16px; border-radius: 20px; display: flex; align-items: center; gap: 8px; font-size: 0.8rem; }}
        .status-dot {{ width: 8px; height: 8px; background: {status_col}; border-radius: 50%; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: var(--card); border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 20px; }}
        .card-label {{ color: var(--dim); font-size: 0.8rem; }}
        .card-value {{ font-size: 1.4rem; font-family: 'Orbitron'; }}
        .up {{ color: var(--gr); }} .down {{ color: var(--rd); }}
        .chart-container {{ background: var(--card); border-radius: 16px; padding: 20px; height: 400px; margin-bottom: 30px; }}
        .tables {{ display: grid; grid-template-columns: 2fr 1fr; gap: 20px; }}
        .table-card {{ background: var(--card); padding: 20px; border-radius: 16px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        td, th {{ padding: 10px 5px; text-align: left; font-size: 0.85rem; border-bottom: 1px solid rgba(255,255,255,0.05); }}
        th {{ color: var(--dim); text-transform: uppercase; font-size: 0.7rem; }}
        .type-buy {{ color: var(--pri); }} .type-sell {{ color: var(--gr); }} .type-settlement {{ color: var(--sec); }}
    </style></head>"""

    body = f"""<body><div class="container"><header><div><h1>POLYMASTER {mode}</h1><p style='color:var(--dim); font-size:0.7rem;'>Live Activity Feed</p></div>
    <div class="status-badge"><div class="status-dot"></div>{mode} MODE ACTIVE</div></header>
    <div class="grid">
        <div class="card"><div class="card-label">Balance</div><div class="card-value">${curr_bal:.2f}</div></div>
        <div class="card"><div class="card-label">PnL</div><div class="card-value {pnl_class}">{pnl_sign}{pnl_val:.2f} ({pnl_pc:.2f}%)</div></div>
        <div class="card"><div class="card-label">Avg/Hour</div><div class="card-value">${avg_pnl:.2f}</div></div>
        <div class="card"><div class="card-label">Max Gale</div><div class="card-value">lvl {max_gale}</div></div>
    </div>
    <div class="chart-container"><canvas id="chart"></canvas></div>
    <div class="tables"><div class="table-card"><h3>Recent Trades</h3><table><thead><tr><th>Time</th><th>Action</th><th>Market</th><th>Price</th><th>Amount</th><th>Balance</th></tr></thead>
    <tbody>{rows_html}</tbody></table></div>
    <div class="table-card"><h3>Stats</h3><div style='margin-top:15px;'>
    <p>Max Drawdrown: <b style='color:var(--rd)'>${cap_req:.2f}</b></p>
    <p>Elapsed Time: <b>{elapsed:.2f}h</b></p>
    <p>Initial: <b>${initial_bal:.2f}</b></p>
    </div></div></div></div>
    <script>
        const ctx = document.getElementById('chart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {json.dumps(labels_list)},
                datasets: [{{
                    label: 'Evolution',
                    data: {json.dumps(data_list)},
                    borderColor: '#00d4ff', tension: 0.3, fill: false, pointRadius: 0
                }}]
            }},
            options: {{ responsive: true, maintainAspectRatio: false, scales: {{ y: {{ grid: {{ color: 'rgba(255,255,255,0.05)' }} }} }} }}
        }});
    </script></body></html>"""

    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(head + body)
    except Exception: pass
