import os
import sys
import time
import json
import sqlite3
import datetime
import threading
import subprocess
import urllib.request
import urllib.error

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    default_config = {
        "lhm_url": "http://localhost:8085/data.json",
        "poll_interval_seconds": 3,
        "electricity_rate_vnd_kwh": 2500,
        "pricing_mode": "evn_tiered",
        "lhm_exe_path": "LibreHardwareMonitor.NET.10/LibreHardwareMonitor.exe",
        "db_path": "power_data.db",
        "system_base_power_w": 30.0,
        "psu_efficiency_factor": 1.12
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                default_config.update(cfg)
        except Exception as e:
            print(f"[Config Error] {e}")
    return default_config

def calculate_evn_tiered_cost(kwh):
    """
    Tính tiền điện sinh hoạt theo 6 bậc EVN mới nhất (Quyết định 1279/QĐ-BCT / Bộ Công Thương):
    Bậc 1 (0-50 kWh): 1.984 đ/kWh
    Bậc 2 (51-100 kWh): 2.050 đ/kWh
    Bậc 3 (101-200 kWh): 2.380 đ/kWh
    Bậc 4 (201-300 kWh): 2.998 đ/kWh
    Bậc 5 (301-400 kWh): 3.350 đ/kWh
    Bậc 6 (Từ 401 kWh trở lên): 3.460 đ/kWh
    + Thuế VAT 8%
    """
    tiers = [
        (50, 1984),
        (50, 2050),
        (100, 2380),
        (100, 2998),
        (100, 3350),
        (float('inf'), 3460)
    ]
    cost = 0.0
    rem = kwh
    for limit, rate in tiers:
        if rem <= 0:
            break
        used = min(rem, limit)
        cost += used * rate
        rem -= used
    return round(cost * 1.08, 2)

class PowerMonitor:
    def __init__(self, config=None):
        self.config = config or load_config()
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(self.base_dir, self.config.get("db_path", "power_data.db"))
        self.lhm_url = self.config.get("lhm_url", "http://localhost:8085/data.json")
        self.interval = self.config.get("poll_interval_seconds", 3)
        self.base_power = self.config.get("system_base_power_w", 30.0)
        
        self.lock = threading.Lock()
        self.running = False
        
        self.current_state = {
            "timestamp": "",
            "connected": False,
            "status_msg": "Initializing...",
            "cpu_power_w": 0.0,
            "gpu_power_w": 0.0,
            "other_power_w": 0.0,
            "total_power_w": 0.0,
            "today_kwh": 0.0,
            "today_cost_vnd": 0.0,
            "today_avg_w": 0.0,
            "today_peak_w": 0.0,
            "today_active_seconds": 0,
            "date": datetime.date.today().isoformat()
        }
        
        self._init_db()
        self._load_today_totals()

    def _get_db(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    total_kwh REAL DEFAULT 0.0,
                    peak_power_w REAL DEFAULT 0.0,
                    avg_power_w REAL DEFAULT 0.0,
                    sum_power_w REAL DEFAULT 0.0,
                    sample_count INTEGER DEFAULT 0,
                    total_active_seconds REAL DEFAULT 0.0,
                    estimated_cost_vnd REAL DEFAULT 0.0,
                    updated_at TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS power_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    date TEXT,
                    total_power_w REAL,
                    cpu_power_w REAL,
                    gpu_power_w REAL,
                    other_power_w REAL
                )
            """)
            conn.commit()

    def _load_today_totals(self):
        today_str = datetime.date.today().isoformat()
        with self._get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (today_str,))
            row = cursor.fetchone()
            with self.lock:
                if row:
                    self.current_state["today_kwh"] = row["total_kwh"] or 0.0
                    self.current_state["today_peak_w"] = row["peak_power_w"] or 0.0
                    self.current_state["today_avg_w"] = row["avg_power_w"] or 0.0
                    self.current_state["today_active_seconds"] = int(row["total_active_seconds"] or 0)
                    
                    pricing_mode = self.config.get("pricing_mode", "evn_tiered")
                    if pricing_mode == "evn_tiered":
                        self.current_state["today_cost_vnd"] = calculate_evn_tiered_cost(self.current_state["today_kwh"])
                    else:
                        rate = self.config.get("electricity_rate_vnd_kwh", 2500)
                        self.current_state["today_cost_vnd"] = round(self.current_state["today_kwh"] * rate, 2)
                        
                    self._today_sum_w = row["sum_power_w"] or 0.0
                    self._today_sample_count = row["sample_count"] or 0
                else:
                    self.current_state["today_kwh"] = 0.0
                    self.current_state["today_peak_w"] = 0.0
                    self.current_state["today_avg_w"] = 0.0
                    self.current_state["today_active_seconds"] = 0
                    self.current_state["today_cost_vnd"] = 0.0
                    self._today_sum_w = 0.0
                    self._today_sample_count = 0

    def _parse_lhm_node(self, node, cpu_powers, gpu_powers, other_powers, parent_text=""):
        text = str(node.get("Text", "")).strip()
        sensor_type = str(node.get("SensorType", "")).strip().lower()
        val_str = str(node.get("Value", "")).strip()
        combined_text = f"{parent_text} {text}".lower()
        
        if sensor_type == "power" or " W" in val_str:
            try:
                val_clean = val_str.replace("W", "").replace(",", ".").strip()
                val = float(val_clean)
                
                # GPU sensors have priority so "GPU Package" is not misidentified as CPU Package
                if any(k in combined_text for k in ["gpu", "radeon", "nvidia", "geforce", "graphics", "board", "vram"]):
                    gpu_powers.append(val)
                elif any(k in combined_text for k in ["cpu", "package", "ia cores", "core power"]):
                    cpu_powers.append(val)
                else:
                    other_powers.append(val)
            except ValueError:
                pass
        
        children = node.get("Children", [])
        if isinstance(children, list):
            for child in children:
                self._parse_lhm_node(child, cpu_powers, gpu_powers, other_powers, combined_text)

    def fetch_hardware_power(self):
        psu_factor = float(self.config.get("psu_efficiency_factor", 1.12))
        try:
            req = urllib.request.Request(self.lhm_url, headers={"User-Agent": "PowerMonitorScript/1.0"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    cpu_p, gpu_p, other_p = [], [], []
                    self._parse_lhm_node(data, cpu_p, gpu_p, other_p)
                    
                    cpu_val = max(cpu_p) if cpu_p else 0.0
                    gpu_val = max(gpu_p) if gpu_p else 0.0
                    other_val = sum(other_p) if other_p else self.base_power
                    
                    if other_val == 0.0:
                        other_val = self.base_power
                        
                    total_val = (cpu_val + gpu_val + other_val) * psu_factor
                    return True, cpu_val, gpu_val, other_val, total_val, "Connected to LibreHardwareMonitor"
        except Exception:
            pass

        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=None)
            cpu_est = 10.0 + (65.0 * (cpu_percent / 100.0))
            gpu_est = 15.0
            other_est = self.base_power
            total_est = (cpu_est + gpu_est + other_est) * psu_factor
            msg = f"Chưa bật Remote Web Server trên LHM (Đang dùng ước tính CPU: {cpu_percent:.0f}%)"
            return False, cpu_est, gpu_est, other_est, total_est, msg
        except Exception as e:
            return False, 15.0, 10.0, 25.0, 50.0 * psu_factor, f"Error estimating power: {e}"

    def try_launch_lhm(self):
        exe_rel = self.config.get("lhm_exe_path", "LibreHardwareMonitor.NET.10/LibreHardwareMonitor.exe")
        exe_abs = os.path.join(self.base_dir, exe_rel)
        if os.path.exists(exe_abs):
            try:
                import psutil
                for p in psutil.process_iter(['name']):
                    if p.info['name'] and 'librehardwaremonitor' in p.info['name'].lower():
                        return True
                subprocess.Popen([exe_abs], cwd=os.path.dirname(exe_abs))
                return True
            except Exception as e:
                print(f"[LHM Launch Error] {e}")
        return False

    def update_cycle(self, delta_time):
        success, cpu_w, gpu_w, other_w, total_w, status_msg = self.fetch_hardware_power()
        now = datetime.datetime.now()
        today_str = now.date().isoformat()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        with self.lock:
            if self.current_state["date"] != today_str:
                self.current_state["date"] = today_str
                self._load_today_totals()
            
            if success:
                self.current_state["connected"] = True
                self.current_state["status_msg"] = status_msg
                self.current_state["cpu_power_w"] = round(cpu_w, 2)
                self.current_state["gpu_power_w"] = round(gpu_w, 2)
                self.current_state["other_power_w"] = round(other_w, 2)
                self.current_state["total_power_w"] = round(total_w, 2)
                
                delta_kwh = (total_w * delta_time) / 3600000.0
                self.current_state["today_kwh"] += delta_kwh
                
                if total_w > self.current_state["today_peak_w"]:
                    self.current_state["today_peak_w"] = round(total_w, 2)
                    
                self.current_state["today_active_seconds"] += delta_time
                self._today_sum_w += total_w
                self._today_sample_count += 1
                
                if self._today_sample_count > 0:
                    self.current_state["today_avg_w"] = round(self._today_sum_w / self._today_sample_count, 2)
                    
                pricing_mode = self.config.get("pricing_mode", "evn_tiered")
                if pricing_mode == "evn_tiered":
                    self.current_state["today_cost_vnd"] = calculate_evn_tiered_cost(self.current_state["today_kwh"])
                else:
                    rate = self.config.get("electricity_rate_vnd_kwh", 2500)
                    self.current_state["today_cost_vnd"] = round(self.current_state["today_kwh"] * rate, 2)
                    
                self.current_state["timestamp"] = timestamp_str
                self._save_to_db(today_str, timestamp_str, cpu_w, gpu_w, other_w, total_w)
            else:
                self.current_state["connected"] = False
                self.current_state["status_msg"] = status_msg
                self.current_state["timestamp"] = timestamp_str

    def _save_to_db(self, today_str, timestamp_str, cpu_w, gpu_w, other_w, total_w):
        try:
            with self._get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO daily_stats (date, total_kwh, peak_power_w, avg_power_w, sum_power_w, sample_count, total_active_seconds, estimated_cost_vnd, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        total_kwh = excluded.total_kwh,
                        peak_power_w = MAX(daily_stats.peak_power_w, excluded.peak_power_w),
                        avg_power_w = excluded.avg_power_w,
                        sum_power_w = excluded.sum_power_w,
                        sample_count = excluded.sample_count,
                        total_active_seconds = excluded.total_active_seconds,
                        estimated_cost_vnd = excluded.estimated_cost_vnd,
                        updated_at = excluded.updated_at
                """, (
                    today_str,
                    self.current_state["today_kwh"],
                    self.current_state["today_peak_w"],
                    self.current_state["today_avg_w"],
                    self._today_sum_w,
                    self._today_sample_count,
                    self.current_state["today_active_seconds"],
                    self.current_state["today_cost_vnd"],
                    timestamp_str
                ))
                cursor.execute("""
                    INSERT INTO power_logs (timestamp, date, total_power_w, cpu_power_w, gpu_power_w, other_power_w)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (timestamp_str, today_str, round(total_w, 2), round(cpu_w, 2), round(gpu_w, 2), round(other_w, 2)))
                cursor.execute("DELETE FROM power_logs WHERE id NOT IN (SELECT id FROM power_logs ORDER BY id DESC LIMIT 5000)")
                conn.commit()
        except Exception as e:
            print(f"[DB Save Error] {e}")

    def run_loop(self):
        self.running = True
        last_time = time.time()
        self.try_launch_lhm()
        while self.running:
            now_time = time.time()
            delta_time = now_time - last_time
            last_time = now_time
            if delta_time > 0:
                self.update_cycle(delta_time)
            time.sleep(self.interval)

    def start_background(self):
        t = threading.Thread(target=self.run_loop, daemon=True)
        t.start()
        return t

    def get_history(self, days=30):
        with self._get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM daily_stats ORDER BY date DESC LIMIT ?", (days,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_recent_logs(self, limit=60):
        with self._get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp, total_power_w, cpu_power_w, gpu_power_w FROM power_logs ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in reversed(rows)]
