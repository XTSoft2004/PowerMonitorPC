import os
import json
import csv
import io
import threading
from flask import Flask, jsonify, render_template, request, Response
from power_monitor import PowerMonitor, load_config, CONFIG_FILE

app = Flask(__name__)
monitor = PowerMonitor()

# Start background monitoring thread
monitor.start_background()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/live")
def api_live():
    with monitor.lock:
        state = dict(monitor.current_state)
    return jsonify(state)

@app.route("/api/recent")
def api_recent():
    limit = request.args.get("limit", default=60, type=int)
    logs = monitor.get_recent_logs(limit=limit)
    return jsonify(logs)

@app.route("/api/history")
def api_history():
    days = request.args.get("days", default=30, type=int)
    history = monitor.get_history(days=days)
    return jsonify(history)

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.json or {}
        cfg = load_config()
        if "electricity_rate_vnd_kwh" in data:
            cfg["electricity_rate_vnd_kwh"] = float(data["electricity_rate_vnd_kwh"])
        if "poll_interval_seconds" in data:
            cfg["poll_interval_seconds"] = int(data["poll_interval_seconds"])
        if "system_base_power_w" in data:
            cfg["system_base_power_w"] = float(data["system_base_power_w"])
        if "psu_efficiency_factor" in data:
            cfg["psu_efficiency_factor"] = float(data["psu_efficiency_factor"])
        if "pricing_mode" in data:
            cfg["pricing_mode"] = str(data["pricing_mode"])
            
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
            
        # Update running monitor settings
        monitor.config = cfg
        monitor.interval = cfg["poll_interval_seconds"]
        monitor.base_power = cfg["system_base_power_w"]
        
        return jsonify({"status": "success", "config": cfg})
    else:
        return jsonify(load_config())

@app.route("/api/export")
def api_export():
    history = monitor.get_history(days=365)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Ngay", "Tong_So_Dien_kWh", "Tien_Dien_VND", "Cong_Suat_Trung_Binh_W", "Cong_Suat_Max_W", "Thoi_Gian_Chay_Giay"])
    
    for row in history:
        writer.writerow([
            row["date"],
            row["total_kwh"],
            row["estimated_cost_vnd"],
            row["avg_power_w"],
            row["peak_power_w"],
            row["total_active_seconds"]
        ])
        
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=Thong_Ke_Dien_PC.csv"}
    )

def run_flask(port):
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    cfg = load_config()
    port = cfg.get("server_port", 38472)
    
    # Run Flask Web Server in background thread
    t = threading.Thread(target=run_flask, args=(port,), daemon=True)
    t.start()
    
    # Auto open browser
    try:
        import webbrowser
        webbrowser.open(f"http://localhost:{port}")
    except Exception:
        pass
    
    # Run Windows System Tray Icon on main thread
    try:
        from tray_icon import SystemTrayApp
        tray = SystemTrayApp(monitor, port=port)
        tray.run()
    except Exception as e:
        print(f"[Tray Icon Warning] Could not start System Tray Icon: {e}")
        # Keep main thread alive if tray icon fails
        t.join()
