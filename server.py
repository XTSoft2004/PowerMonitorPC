import os
import json
import csv
import io
import zipfile
import datetime
import threading
import subprocess
from flask import Flask, jsonify, render_template, request, Response, send_file
from power_monitor import PowerMonitor, load_config, CONFIG_FILE

app = Flask(__name__)
monitor = PowerMonitor()

# Start background monitoring thread
monitor.start_background()

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/analytics")
def analytics():
    return render_template("analytics.html")

@app.route("/manager-pc")
def manager_pc():
    return render_template("manager_pc.html")

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

@app.route("/api/stats/hourly")
def api_stats_hourly():
    date_str = request.args.get("date", default="", type=str)
    data = monitor.get_hourly_stats(date_str=date_str if date_str else None)
    return jsonify(data)

@app.route("/api/stats/daily")
def api_stats_daily():
    days = request.args.get("days", default=30, type=int)
    data = monitor.get_daily_stats(days=days)
    return jsonify(data)

@app.route("/api/stats/monthly")
def api_stats_monthly():
    months = request.args.get("months", default=12, type=int)
    data = monitor.get_monthly_stats(months=months)
    return jsonify(data)

@app.route("/api/stats/comparison")
def api_stats_comparison():
    data = monitor.get_consumption_comparison()
    return jsonify(data)

@app.route("/api/system/disks")
def api_system_disks():
    data = monitor.get_disk_info()
    return jsonify(data)

@app.route("/api/system/audio", methods=["POST"])
def api_system_audio():
    data = request.json or {}
    action = data.get("action", "")
    level = data.get("level")
    
    success, msg = monitor.control_system_audio(action, level)
    if success:
        return jsonify({"status": "success", "message": msg})
    else:
        return jsonify({"status": "error", "message": msg}), 400

@app.route("/api/system/pc_action", methods=["POST"])
def api_system_pc_action():
    data = request.json or {}
    action = data.get("action", "")
    success, msg = monitor.execute_pc_action(action)
    if success:
        return jsonify({"status": "success", "message": msg})
    else:
        return jsonify({"status": "error", "message": msg}), 400

@app.route("/api/system/timer", methods=["GET", "POST"])
def api_system_timer():
    if request.method == "POST":
        data = request.json or {}
        action = data.get("action", "start")
        if action == "cancel":
            success, msg = monitor.cancel_countdown_timer()
            return jsonify({"status": "success" if success else "error", "message": msg})
        
        minutes = data.get("minutes", 30)
        mode = data.get("mode", "shutdown")
        success, msg = monitor.set_countdown_timer(minutes, mode)
        return jsonify({"status": "success" if success else "error", "message": msg})
    else:
        status = monitor.get_countdown_timer_status()
        return jsonify(status)

@app.route("/api/files/drives")
def api_files_drives():
    import psutil
    drives = []
    for part in psutil.disk_partitions(all=False):
        if 'cdrom' in part.opts or not part.device:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            total_gb = round(usage.total / (1024**3), 1)
            free_gb = round(usage.free / (1024**3), 1)
            drives.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": total_gb,
                "free_gb": free_gb,
                "percent": usage.percent
            })
        except Exception:
            pass
    return jsonify(drives)

@app.route("/api/files/list")
def api_files_list():
    path = request.args.get("path", "").strip()
    if not path:
        path = os.path.expanduser("~")
        if not os.path.exists(path):
            path = "C:\\"

    if not os.path.exists(path):
        return jsonify({"status": "error", "message": f"Đường dẫn không tồn tại: {path}"}), 404

    if os.path.isfile(path):
        path = os.path.dirname(path)

    try:
        items = []
        with os.scandir(path) as scanner:
            for entry in scanner:
                try:
                    stat = entry.stat(follow_symlinks=False)
                    is_dir = entry.is_dir(follow_symlinks=False)
                    size_bytes = stat.st_size if not is_dir else 0
                    
                    if is_dir:
                        size_str = "THƯ MỤC"
                    else:
                        if size_bytes < 1024:
                            size_str = f"{size_bytes} B"
                        elif size_bytes < 1024 * 1024:
                            size_str = f"{round(size_bytes / 1024, 1)} KB"
                        elif size_bytes < 1024 * 1024 * 1024:
                            size_str = f"{round(size_bytes / (1024 * 1024), 1)} MB"
                        else:
                            size_str = f"{round(size_bytes / (1024 * 1024 * 1024), 2)} GB"

                    mod_time = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                    
                    items.append({
                        "name": entry.name,
                        "path": entry.path,
                        "is_dir": is_dir,
                        "size_bytes": size_bytes,
                        "size_str": size_str,
                        "modified": mod_time
                    })
                except Exception:
                    pass

        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        
        parent_path = os.path.dirname(os.path.abspath(path))
        if parent_path == os.path.abspath(path):
            parent_path = None

        return jsonify({
            "status": "success",
            "current_path": os.path.abspath(path),
            "parent_path": parent_path,
            "items": items
        })
    except PermissionError:
        return jsonify({"status": "error", "message": f"Không có quyền truy cập thư mục: {path}"}), 403
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/files/download")
def api_files_download():
    path = request.args.get("path", "").strip()
    if not path or not os.path.isfile(path):
        return jsonify({"status": "error", "message": "File không tồn tại hoặc đường dẫn là thư mục"}), 404

    try:
        return send_file(path, as_attachment=True)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Lỗi tải file: {e}"}), 500

@app.route("/api/files/zip")
def api_files_zip():
    path = request.args.get("path", "").strip()
    if not path or not os.path.exists(path):
        return jsonify({"status": "error", "message": "Đường dẫn không tồn tại"}), 404

    try:
        target_name = os.path.basename(path.rstrip("\\/")) or "folder"
        memory_file = io.BytesIO()
        
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            if os.path.isfile(path):
                zf.write(path, os.path.basename(path))
            else:
                for root, dirs, files in os.walk(path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, start=path)
                        try:
                            zf.write(file_path, arcname)
                        except Exception:
                            pass

        memory_file.seek(0)
        return send_file(
            memory_file,
            download_name=f"{target_name}.zip",
            as_attachment=True,
            mimetype='application/zip'
        )
    except Exception as e:
        return jsonify({"status": "error", "message": f"Lỗi nén ZIP: {e}"}), 500

@app.route("/api/processes")
def api_processes():
    limit = request.args.get("limit", default=50, type=int)
    sort_by = request.args.get("sort", default="cpu", type=str)
    procs = monitor.get_running_processes(limit=limit, sort_by=sort_by)
    return jsonify(procs)

@app.route("/api/processes/kill", methods=["POST"])
def api_processes_kill():
    data = request.json or {}
    pids = data.get("pids")
    
    if pids and isinstance(pids, list):
        res = monitor.kill_processes_batch(pids)
        return jsonify({
            "status": "success" if res["success_count"] > 0 else "error",
            "message": f"Đã diệt thành công {res['success_count']}/{res['total']} tiến trình.",
            "data": res
        })

    pid = data.get("pid") or request.args.get("pid", type=int)
    if not pid:
        return jsonify({"status": "error", "message": "Thiếu mã tiến trình PID hoặc PIDs"}), 400
    
    success, msg = monitor.kill_process(int(pid))
    if success:
        return jsonify({"status": "success", "message": msg, "pid": int(pid)})
    else:
        return jsonify({"status": "error", "message": msg, "pid": int(pid)}), 400

@app.route("/api/terminal/run", methods=["POST"])
def api_terminal_run():
    data = request.json or {}
    command = (data.get("command") or "").strip()
    cwd = (data.get("cwd") or "").strip()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    current_cwd = cwd if (cwd and os.path.isdir(cwd)) else base_dir

    if not command:
        return jsonify({"status": "error", "message": "Chưa nhập câu lệnh Terminal", "cwd": current_cwd}), 400

    # Seamless 'cd' directory navigation handling
    cmd_lower = command.lower()
    if cmd_lower == "cd" or cmd_lower.startswith("cd ") or cmd_lower.startswith("cd/"):
        target_path = command[2:].strip()
        if target_path.lower().startswith("/d "):
            target_path = target_path[3:].strip()
            
        if not target_path or target_path == "~":
            new_cwd = os.path.expanduser("~")
        else:
            target_path = target_path.strip('"\'')
            new_cwd = os.path.abspath(os.path.join(current_cwd, target_path))

        if os.path.isdir(new_cwd):
            return jsonify({
                "status": "success",
                "command": command,
                "cwd": new_cwd,
                "returncode": 0,
                "stdout": f"Đã chuyển thư mục làm việc sang: {new_cwd}",
                "stderr": ""
            })
        else:
            return jsonify({
                "status": "error",
                "command": command,
                "cwd": current_cwd,
                "returncode": 1,
                "stdout": "",
                "stderr": f"Thư mục không tồn tại: {target_path}"
            })

    try:
        proc = subprocess.run(
            command,
            cwd=current_cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            encoding='utf-8',
            errors='replace'
        )
        return jsonify({
            "status": "success" if proc.returncode == 0 else "error",
            "command": command,
            "cwd": current_cwd,
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or ""
        })
    except subprocess.TimeoutExpired:
        return jsonify({
            "status": "error",
            "command": command,
            "cwd": current_cwd,
            "returncode": -1,
            "stdout": "",
            "stderr": "Lỗi: Thao tác vượt quá thời gian cho phép (Timeout 30s)"
        }), 408
    except Exception as e:
        return jsonify({
            "status": "error",
            "command": command,
            "cwd": current_cwd,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Lỗi thực thi terminal: {e}"
        }), 500

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
    export_type = request.args.get("type", default="daily", type=str)
    date_str = request.args.get("date", default="", type=str)
    output = io.StringIO()
    writer = csv.writer(output)

    if export_type == "hourly":
        data = monitor.get_hourly_stats(date_str=date_str if date_str else None)
        writer.writerow(["Gio", "Ngay", "Tong_So_Dien_kWh", "Tien_Dien_VND", "Cong_Suat_Trung_Binh_W", "Cong_Suat_Max_W", "Thoi_Gian_Chay_Giay"])
        for row in data:
            writer.writerow([
                row["hour"],
                row["date"],
                row["total_kwh"],
                row["estimated_cost_vnd"],
                row["avg_power_w"],
                row["peak_power_w"],
                row["total_active_seconds"]
            ])
        filename = f"Thong_Ke_Gio_{date_str or 'Hom_Nay'}.csv"
    elif export_type == "monthly":
        data = monitor.get_monthly_stats(months=36)
        writer.writerow(["Thang", "Tong_So_Dien_kWh", "Tien_Dien_Uoc_Tính_VND", "Cong_Suat_Trung_Binh_W", "Cong_Suat_Max_W", "Thoi_Gian_Chay_Giay", "So_Ngay_Ghi_Nhan"])
        for row in data:
            writer.writerow([
                row["month"],
                row["total_kwh"],
                row["estimated_cost_vnd"],
                row["avg_power_w"],
                row["peak_power_w"],
                row["total_active_seconds"],
                row["days_count"]
            ])
        filename = "Thong_Ke_Thang_PC.csv"
    else:
        data = monitor.get_daily_stats(days=365)
        writer.writerow(["Ngay", "Tong_So_Dien_kWh", "Tien_Dien_VND", "Cong_Suat_Trung_Binh_W", "Cong_Suat_Max_W", "Thoi_Gian_Chay_Giay"])
        for row in data:
            writer.writerow([
                row["date"],
                row["total_kwh"],
                row["estimated_cost_vnd"],
                row["avg_power_w"],
                row["peak_power_w"],
                row["total_active_seconds"]
            ])
        filename = "Thong_Ke_Ngay_PC.csv"
        
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

def kill_existing_server_instances(port=38472):
    current_pid = os.getpid()
    try:
        import psutil
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr and conn.laddr.port == port and conn.pid and conn.pid != current_pid:
                try:
                    p = psutil.Process(conn.pid)
                    print(f"[Self-Clean] Terminating process PID {conn.pid} holding port {port}...")
                    p.kill()
                except Exception:
                    pass
    except Exception as e:
        print(f"[Port Kill Error] {e}")

    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['pid'] == current_pid:
                    continue
                name = (proc.info['name'] or "").lower()
                cmdline = " ".join(proc.info['cmdline'] or []).lower()
                if "powermonitorpc" in name or ("python" in name and "server.py" in cmdline):
                    print(f"[Self-Clean] Terminating duplicate process PID {proc.info['pid']} ({name})...")
                    proc.kill()
            except Exception:
                pass
    except Exception as e:
        print(f"[Process Kill Error] {e}")

def run_flask(port):
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    cfg = load_config()
    port = cfg.get("server_port", 38472)
    
    # Auto kill existing duplicate server/client instances before starting
    kill_existing_server_instances(port)
    
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
