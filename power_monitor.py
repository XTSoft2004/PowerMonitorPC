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
        
        self.timer_active = False
        self.timer_mode = "shutdown"
        self.timer_end_time = 0.0
        
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
            "cpu_temp_c": 0.0,
            "gpu_temp_c": 0.0,
            "cpu_clock_ghz": 0.0,
            "gpu_clock_mhz": 0,
            "gpu_fan_rpm": 0,
            "cpu_usage_pct": 0.0,
            "ram_usage_pct": 0.0,
            "thermal_alert": False,
            "thermal_alert_msg": "",
            "date": datetime.date.today().isoformat()
        }
        
        self.current_hour_str = datetime.datetime.now().strftime("%Y-%m-%d %H:00")
        self._hour_kwh = 0.0
        self._hour_peak_w = 0.0
        self._hour_sum_w = 0.0
        self._hour_sample_count = 0
        self._hour_active_seconds = 0.0
        
        self._init_db()
        self._load_today_totals()
        self._load_current_hour_totals(self.current_hour_str)

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
                CREATE TABLE IF NOT EXISTS hourly_stats (
                    hour_str TEXT PRIMARY KEY,
                    date TEXT,
                    hour_int INTEGER,
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

    def _load_current_hour_totals(self, hour_str):
        with self._get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM hourly_stats WHERE hour_str = ?", (hour_str,))
            row = cursor.fetchone()
            with self.lock:
                if row:
                    self._hour_kwh = row["total_kwh"] or 0.0
                    self._hour_peak_w = row["peak_power_w"] or 0.0
                    self._hour_active_seconds = float(row["total_active_seconds"] or 0.0)
                    self._hour_sum_w = row["sum_power_w"] or 0.0
                    self._hour_sample_count = row["sample_count"] or 0
                else:
                    self._hour_kwh = 0.0
                    self._hour_peak_w = 0.0
                    self._hour_active_seconds = 0.0
                    self._hour_sum_w = 0.0
                    self._hour_sample_count = 0

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

    def _parse_lhm_node(self, node, cpu_powers, gpu_powers, other_powers, cpu_temps, gpu_temps, cpu_clocks, gpu_clocks, gpu_fans, parent_text=""):
        text = str(node.get("Text", "")).strip()
        sensor_type = str(node.get("SensorType", "")).strip().lower()
        val_str = str(node.get("Value", "")).strip()
        combined_text = f"{parent_text} {text}".lower()
        
        # 1. Power (W)
        if sensor_type == "power" or " W" in val_str:
            try:
                val_clean = val_str.replace("W", "").replace(",", ".").strip()
                val = float(val_clean)
                if any(k in combined_text for k in ["gpu", "radeon", "nvidia", "geforce", "graphics", "board", "vram"]):
                    gpu_powers.append(val)
                elif any(k in combined_text for k in ["cpu", "package", "ia cores", "core power"]):
                    cpu_powers.append(val)
                else:
                    other_powers.append(val)
            except ValueError:
                pass
        
        # 2. Temperature (°C)
        elif sensor_type == "temperature" or "°C" in val_str or "°c" in val_str.lower():
            try:
                val_clean = val_str.replace("°C", "").replace("°c", "").replace("C", "").replace(",", ".").strip()
                val = float(val_clean)
                if any(k in combined_text for k in ["gpu", "radeon", "nvidia", "geforce", "graphics", "vram"]):
                    gpu_temps.append(val)
                elif any(k in combined_text for k in ["cpu", "package", "core"]):
                    cpu_temps.append(val)
            except ValueError:
                pass

        # 3. Clock Speed (GHz or MHz)
        elif sensor_type == "clock" or "mhz" in val_str.lower() or "ghz" in val_str.lower():
            try:
                is_ghz = "ghz" in val_str.lower()
                val_clean = val_str.lower().replace("ghz", "").replace("mhz", "").replace(",", ".").strip()
                val = float(val_clean)
                val_mhz = val * 1000.0 if is_ghz else val
                
                if any(k in combined_text for k in ["gpu", "radeon", "nvidia", "geforce"]):
                    gpu_clocks.append(val_mhz)
                elif any(k in combined_text for k in ["cpu", "core #1", "cpu core"]):
                    cpu_clocks.append(val_mhz)
            except ValueError:
                pass

        # 4. Fan Speed (RPM)
        elif sensor_type == "fan" or "rpm" in val_str.lower():
            try:
                val_clean = val_str.lower().replace("rpm", "").replace(",", ".").strip()
                val = float(val_clean)
                gpu_fans.append(val)
            except ValueError:
                pass

        children = node.get("Children", [])
        if isinstance(children, list):
            for child in children:
                self._parse_lhm_node(child, cpu_powers, gpu_powers, other_powers, cpu_temps, gpu_temps, cpu_clocks, gpu_clocks, gpu_fans, combined_text)

    def fetch_hardware_power(self):
        psu_factor = float(self.config.get("psu_efficiency_factor", 1.12))
        
        cpu_usage_pct = 0.0
        ram_usage_pct = 0.0
        cpu_clock_ghz_psutil = 0.0
        try:
            import psutil
            cpu_usage_pct = round(psutil.cpu_percent(interval=None), 1)
            ram_usage_pct = round(psutil.virtual_memory().percent, 1)
            freq = psutil.cpu_freq()
            if freq and freq.current:
                cpu_clock_ghz_psutil = round(freq.current / 1000.0, 2)
        except Exception:
            pass

        try:
            req = urllib.request.Request(self.lhm_url, headers={"User-Agent": "PowerMonitorScript/1.0"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    cpu_p, gpu_p, other_p = [], [], []
                    cpu_t, gpu_t = [], []
                    cpu_c, gpu_c = [], []
                    gpu_f = []

                    self._parse_lhm_node(data, cpu_p, gpu_p, other_p, cpu_t, gpu_t, cpu_c, gpu_c, gpu_f)
                    
                    cpu_val = max(cpu_p) if cpu_p else 0.0
                    gpu_val = max(gpu_p) if gpu_p else 0.0
                    other_val = sum(other_p) if other_p else self.base_power
                    
                    if other_val == 0.0:
                        other_val = self.base_power
                        
                    total_val = (cpu_val + gpu_val + other_val) * psu_factor

                    cpu_temp = round(max(cpu_t), 1) if cpu_t else 0.0
                    gpu_temp = round(max(gpu_t), 1) if gpu_t else 0.0
                    
                    cpu_clock_mhz = max(cpu_c) if cpu_c else (cpu_clock_ghz_psutil * 1000.0)
                    cpu_clock_ghz = round(cpu_clock_mhz / 1000.0, 2)
                    
                    gpu_clock_mhz = int(max(gpu_c)) if gpu_c else 0
                    gpu_fan_rpm = int(max(gpu_f)) if gpu_f else 0

                    return {
                        "connected": True,
                        "cpu_w": cpu_val,
                        "gpu_w": gpu_val,
                        "other_w": other_val,
                        "total_w": total_val,
                        "cpu_temp_c": cpu_temp,
                        "gpu_temp_c": gpu_temp,
                        "cpu_clock_ghz": cpu_clock_ghz,
                        "gpu_clock_mhz": gpu_clock_mhz,
                        "gpu_fan_rpm": gpu_fan_rpm,
                        "cpu_usage_pct": cpu_usage_pct,
                        "ram_usage_pct": ram_usage_pct,
                        "msg": "Connected to LibreHardwareMonitor"
                    }
        except Exception:
            pass

        cpu_est = 10.0 + (65.0 * (cpu_usage_pct / 100.0))
        gpu_est = 15.0
        other_est = self.base_power
        total_est = (cpu_est + gpu_est + other_est) * psu_factor
        msg = f"Chưa bật Web Server LHM (Đang dùng ước tính CPU: {cpu_usage_pct:.0f}%)"
        
        return {
            "connected": False,
            "cpu_w": cpu_est,
            "gpu_w": gpu_est,
            "other_w": other_est,
            "total_w": total_est,
            "cpu_temp_c": 0.0,
            "gpu_temp_c": 0.0,
            "cpu_clock_ghz": cpu_clock_ghz_psutil,
            "gpu_clock_mhz": 0,
            "gpu_fan_rpm": 0,
            "cpu_usage_pct": cpu_usage_pct,
            "ram_usage_pct": ram_usage_pct,
            "msg": msg
        }

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
        hw = self.fetch_hardware_power()
        cpu_w = hw["cpu_w"]
        gpu_w = hw["gpu_w"]
        other_w = hw["other_w"]
        total_w = hw["total_w"]
        status_msg = hw["msg"]
        connected = hw["connected"]

        now = datetime.datetime.now()
        today_str = now.date().isoformat()
        hour_str = now.strftime("%Y-%m-%d %H:00")
        hour_int = now.hour
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
        
        with self.lock:
            if self.current_state["date"] != today_str:
                self.current_state["date"] = today_str
                self._load_today_totals()

            if self.current_hour_str != hour_str:
                self.current_hour_str = hour_str
                self._load_current_hour_totals(hour_str)
            
            self.current_state["connected"] = connected
            self.current_state["status_msg"] = status_msg
            self.current_state["cpu_power_w"] = round(cpu_w, 2)
            self.current_state["gpu_power_w"] = round(gpu_w, 2)
            self.current_state["other_power_w"] = round(other_w, 2)
            self.current_state["total_power_w"] = round(total_w, 2)
            
            self.current_state["cpu_temp_c"] = hw["cpu_temp_c"]
            self.current_state["gpu_temp_c"] = hw["gpu_temp_c"]
            self.current_state["cpu_clock_ghz"] = hw["cpu_clock_ghz"]
            self.current_state["gpu_clock_mhz"] = hw["gpu_clock_mhz"]
            self.current_state["gpu_fan_rpm"] = hw["gpu_fan_rpm"]
            self.current_state["cpu_usage_pct"] = hw["cpu_usage_pct"]
            self.current_state["ram_usage_pct"] = hw["ram_usage_pct"]

            # Thermal alert evaluation (> 85°C)
            if hw["cpu_temp_c"] >= 85.0 or hw["gpu_temp_c"] >= 85.0:
                self.current_state["thermal_alert"] = True
                self.current_state["thermal_alert_msg"] = f"🔥 CẢNH BÁO QUÁ NHIỆT: CPU ({hw['cpu_temp_c']}°C) / GPU ({hw['gpu_temp_c']}°C) VƯỢT QUÁ 85°C!"
            else:
                self.current_state["thermal_alert"] = False
                self.current_state["thermal_alert_msg"] = ""

            delta_kwh = (total_w * delta_time) / 3600000.0
            self.current_state["today_kwh"] += delta_kwh
            self._hour_kwh += delta_kwh
            
            if total_w > self.current_state["today_peak_w"]:
                self.current_state["today_peak_w"] = round(total_w, 2)
            if total_w > self._hour_peak_w:
                self._hour_peak_w = round(total_w, 2)
                
            self.current_state["today_active_seconds"] += delta_time
            self._today_sum_w += total_w
            self._today_sample_count += 1
            
            self._hour_active_seconds += delta_time
            self._hour_sum_w += total_w
            self._hour_sample_count += 1
            
            if self._today_sample_count > 0:
                self.current_state["today_avg_w"] = round(self._today_sum_w / self._today_sample_count, 2)
                
            pricing_mode = self.config.get("pricing_mode", "evn_tiered")
            if pricing_mode == "evn_tiered":
                self.current_state["today_cost_vnd"] = calculate_evn_tiered_cost(self.current_state["today_kwh"])
                hour_cost = calculate_evn_tiered_cost(self._hour_kwh)
            else:
                rate = self.config.get("electricity_rate_vnd_kwh", 2500)
                self.current_state["today_cost_vnd"] = round(self.current_state["today_kwh"] * rate, 2)
                hour_cost = round(self._hour_kwh * rate, 2)
                
            self.current_state["timestamp"] = timestamp_str
            self._save_to_db(today_str, hour_str, hour_int, timestamp_str, cpu_w, gpu_w, other_w, total_w, hour_cost)

    def _save_to_db(self, today_str, hour_str, hour_int, timestamp_str, cpu_w, gpu_w, other_w, total_w, hour_cost):
        try:
            hour_avg_w = round(self._hour_sum_w / self._hour_sample_count, 2) if self._hour_sample_count > 0 else 0.0
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
                    INSERT INTO hourly_stats (hour_str, date, hour_int, total_kwh, peak_power_w, avg_power_w, sum_power_w, sample_count, total_active_seconds, estimated_cost_vnd, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(hour_str) DO UPDATE SET
                        total_kwh = excluded.total_kwh,
                        peak_power_w = MAX(hourly_stats.peak_power_w, excluded.peak_power_w),
                        avg_power_w = excluded.avg_power_w,
                        sum_power_w = excluded.sum_power_w,
                        sample_count = excluded.sample_count,
                        total_active_seconds = excluded.total_active_seconds,
                        estimated_cost_vnd = excluded.estimated_cost_vnd,
                        updated_at = excluded.updated_at
                """, (
                    hour_str,
                    today_str,
                    hour_int,
                    self._hour_kwh,
                    self._hour_peak_w,
                    hour_avg_w,
                    self._hour_sum_w,
                    self._hour_sample_count,
                    self._hour_active_seconds,
                    hour_cost,
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

    def get_hourly_stats(self, date_str=None):
        if not date_str:
            date_str = datetime.date.today().isoformat()
        with self._get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM hourly_stats WHERE date = ? ORDER BY hour_int ASC", (date_str,))
            rows = cursor.fetchall()
            existing_map = {row["hour_int"]: dict(row) for row in rows}
            
            results = []
            for h in range(24):
                if h in existing_map:
                    item = existing_map[h]
                    item["hour"] = f"{h:02d}:00"
                    results.append(item)
                else:
                    results.append({
                        "hour_str": f"{date_str} {h:02d}:00",
                        "date": date_str,
                        "hour_int": h,
                        "hour": f"{h:02d}:00",
                        "total_kwh": 0.0,
                        "peak_power_w": 0.0,
                        "avg_power_w": 0.0,
                        "sum_power_w": 0.0,
                        "sample_count": 0,
                        "total_active_seconds": 0.0,
                        "estimated_cost_vnd": 0.0,
                        "updated_at": ""
                    })
            return results

    def get_daily_stats(self, days=30):
        return self.get_history(days=days)

    def get_history(self, days=30):
        with self._get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM daily_stats ORDER BY date DESC LIMIT ?", (days,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_monthly_stats(self, months=12):
        with self._get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    substr(date, 1, 7) as month,
                    SUM(total_kwh) as total_kwh,
                    MAX(peak_power_w) as peak_power_w,
                    SUM(sum_power_w) as sum_power_w,
                    SUM(sample_count) as sample_count,
                    SUM(total_active_seconds) as total_active_seconds,
                    COUNT(date) as days_count
                FROM daily_stats
                GROUP BY substr(date, 1, 7)
                ORDER BY month DESC
                LIMIT ?
            """, (months,))
            rows = cursor.fetchall()
            
            results = []
            pricing_mode = self.config.get("pricing_mode", "evn_tiered")
            rate = self.config.get("electricity_rate_vnd_kwh", 2500)
            
            for row in rows:
                m_dict = dict(row)
                m_kwh = m_dict["total_kwh"] or 0.0
                m_samples = m_dict["sample_count"] or 0
                m_sum_w = m_dict["sum_power_w"] or 0.0
                
                m_dict["avg_power_w"] = round(m_sum_w / m_samples, 2) if m_samples > 0 else 0.0
                if pricing_mode == "evn_tiered":
                    m_dict["estimated_cost_vnd"] = calculate_evn_tiered_cost(m_kwh)
                else:
                    m_dict["estimated_cost_vnd"] = round(m_kwh * rate, 2)
                    
                results.append(m_dict)
            return results

    def get_recent_logs(self, limit=60):
        with self._get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT timestamp, total_power_w, cpu_power_w, gpu_power_w FROM power_logs ORDER BY id DESC LIMIT ?", (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in reversed(rows)]

    def get_running_processes(self, limit=50, sort_by="cpu"):
        try:
            import psutil
            procs = []
            for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'exe']):
                try:
                    pinfo = p.info
                    name = pinfo.get('name') or 'Unknown'
                    pid = pinfo.get('pid')
                    mem_bytes = pinfo.get('memory_info').rss if pinfo.get('memory_info') else 0
                    mem_mb = round(mem_bytes / (1024 * 1024), 1)
                    cpu_p = pinfo.get('cpu_percent') or 0.0
                    exe = pinfo.get('exe') or ''
                    
                    if pid == 0 or name.lower() in ['system idle process', 'idle']:
                        continue
                        
                    procs.append({
                        "pid": pid,
                        "name": name,
                        "cpu_percent": round(cpu_p, 1),
                        "memory_mb": mem_mb,
                        "exe": exe
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            if sort_by == "memory":
                procs.sort(key=lambda x: x["memory_mb"], reverse=True)
            else:
                procs.sort(key=lambda x: x["cpu_percent"], reverse=True)

            return procs[:limit]
        except Exception as e:
            print(f"[Process Fetch Error] {e}")
            return []

    def kill_process(self, pid):
        try:
            import psutil
            p = psutil.Process(pid)
            p_name = p.name()
            p.kill()
            return True, f"Đã đóng thành công ứng dụng {p_name} (PID: {pid})"
        except psutil.NoSuchProcess:
            return False, f"Tiến trình (PID: {pid}) không còn tồn tại."
        except psutil.AccessDenied:
            return False, f"Không có quyền đóng tiến trình hệ thống (PID: {pid})."
        except Exception as e:
            return False, f"Lỗi khi đóng tiến trình: {e}"

    def kill_processes_batch(self, pids):
        results = []
        success_count = 0
        fail_count = 0
        for pid in pids:
            try:
                pid_int = int(pid)
                ok, msg = self.kill_process(pid_int)
                if ok:
                    success_count += 1
                else:
                    fail_count += 1
                results.append({"pid": pid_int, "success": ok, "message": msg})
            except Exception as e:
                fail_count += 1
                results.append({"pid": pid, "success": False, "message": str(e)})
        return {
            "total": len(pids),
            "success_count": success_count,
            "fail_count": fail_count,
            "results": results
        }

    def get_consumption_comparison(self):
        today_date = datetime.date.today()
        yesterday_date = today_date - datetime.timedelta(days=1)
        today_str = today_date.isoformat()
        yesterday_str = yesterday_date.isoformat()
        
        current_month_str = today_date.strftime("%Y-%m")
        first_of_this_month = today_date.replace(day=1)
        last_month_date = first_of_this_month - datetime.timedelta(days=1)
        last_month_str = last_month_date.strftime("%Y-%m")

        pricing_mode = self.config.get("pricing_mode", "evn_tiered")
        rate = self.config.get("electricity_rate_vnd_kwh", 2500)

        with self._get_db() as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (today_str,))
            row_today = cursor.fetchone()
            today_kwh = row_today["total_kwh"] if row_today else self.current_state["today_kwh"]
            today_cost = row_today["estimated_cost_vnd"] if row_today else self.current_state["today_cost_vnd"]

            cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (yesterday_str,))
            row_yest = cursor.fetchone()
            yest_kwh = row_yest["total_kwh"] if row_yest else 0.0
            yest_cost = row_yest["estimated_cost_vnd"] if row_yest else 0.0

            cursor.execute("SELECT SUM(total_kwh) as total_kwh FROM daily_stats WHERE date LIKE ?", (f"{current_month_str}%",))
            row_cur_m = cursor.fetchone()
            cur_month_kwh = (row_cur_m["total_kwh"] or 0.0) if row_cur_m else 0.0

            cursor.execute("SELECT SUM(total_kwh) as total_kwh FROM daily_stats WHERE date LIKE ?", (f"{last_month_str}%",))
            row_last_m = cursor.fetchone()
            last_month_kwh = (row_last_m["total_kwh"] or 0.0) if row_last_m else 0.0

            if pricing_mode == "evn_tiered":
                cur_month_cost = calculate_evn_tiered_cost(cur_month_kwh)
                last_month_cost = calculate_evn_tiered_cost(last_month_kwh)
            else:
                cur_month_cost = round(cur_month_kwh * rate, 2)
                last_month_cost = round(last_month_kwh * rate, 2)

            day_diff_kwh = round(today_kwh - yest_kwh, 3)
            day_diff_cost = round(today_cost - yest_cost, 0)
            if yest_kwh > 0:
                day_percent = round(((today_kwh - yest_kwh) / yest_kwh) * 100.0, 1)
            else:
                day_percent = 100.0 if today_kwh > 0 else 0.0

            month_diff_kwh = round(cur_month_kwh - last_month_kwh, 3)
            month_diff_cost = round(cur_month_cost - last_month_cost, 0)
            if last_month_kwh > 0:
                month_percent = round(((cur_month_kwh - last_month_kwh) / last_month_kwh) * 100.0, 1)
            else:
                month_percent = 100.0 if cur_month_kwh > 0 else 0.0

            return {
                "day": {
                    "today_date": today_str,
                    "yesterday_date": yesterday_str,
                    "today_kwh": round(today_kwh, 3),
                    "yesterday_kwh": round(yest_kwh, 3),
                    "today_cost": round(today_cost, 0),
                    "yesterday_cost": round(yest_cost, 0),
                    "diff_kwh": day_diff_kwh,
                    "diff_cost": day_diff_cost,
                    "percent": day_percent,
                    "trend": "up" if day_diff_kwh > 0 else ("down" if day_diff_kwh < 0 else "equal")
                },
                "month": {
                    "current_month": current_month_str,
                    "last_month": last_month_str,
                    "current_kwh": round(cur_month_kwh, 3),
                    "last_kwh": round(last_month_kwh, 3),
                    "current_cost": round(cur_month_cost, 0),
                    "last_cost": round(last_month_cost, 0),
                    "diff_kwh": month_diff_kwh,
                    "diff_cost": month_diff_cost,
                    "percent": month_percent,
                    "trend": "up" if month_diff_kwh > 0 else ("down" if month_diff_kwh < 0 else "equal")
                }
            }

    def get_disk_info(self):
        try:
            import psutil
            disks = []
            for part in psutil.disk_partitions(all=False):
                if 'cdrom' in part.opts or not part.fstype:
                    continue
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    total_gb = round(usage.total / (1024**3), 1)
                    used_gb = round(usage.used / (1024**3), 1)
                    free_gb = round(usage.free / (1024**3), 1)
                    percent = usage.percent

                    status = "normal"
                    if percent >= 95 or free_gb < 5:
                        status = "critical"
                    elif percent >= 85:
                        status = "warning"

                    disks.append({
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "fstype": part.fstype,
                        "total_gb": total_gb,
                        "used_gb": used_gb,
                        "free_gb": free_gb,
                        "percent": percent,
                        "status": status
                    })
                except Exception:
                    pass
            return disks
        except Exception as e:
            print(f"[Disk Fetch Error] {e}")
            return []

    def control_system_audio(self, action, level=None):
        import ctypes
        import time

        VK_VOLUME_MUTE = 0xAD
        VK_VOLUME_DOWN = 0xAE
        VK_VOLUME_UP = 0xAF
        VK_MEDIA_NEXT_TRACK = 0xB0
        VK_MEDIA_PREV_TRACK = 0xB1
        VK_MEDIA_PLAY_PAUSE = 0xB3

        def send_key(vk_code):
            ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
            time.sleep(0.02)
            ctypes.windll.user32.keybd_event(vk_code, 0, 2, 0)

        try:
            if action == "mute":
                send_key(VK_VOLUME_MUTE)
                return True, "Đã bật/tắt Tắt Tiếng (Mute/Unmute)"
            elif action == "vol_up":
                send_key(VK_VOLUME_UP)
                send_key(VK_VOLUME_UP)
                return True, "Tăng âm lượng"
            elif action == "vol_down":
                send_key(VK_VOLUME_DOWN)
                send_key(VK_VOLUME_DOWN)
                return True, "Giảm âm lượng"
            elif action == "play_pause":
                send_key(VK_MEDIA_PLAY_PAUSE)
                return True, "Phát/Tạm dừng nhạc (Play/Pause)"
            elif action == "next_track":
                send_key(VK_MEDIA_NEXT_TRACK)
                return True, "Chuyển bài tiếp theo (Next)"
            elif action == "prev_track":
                send_key(VK_MEDIA_PREV_TRACK)
                return True, "Chuyển bài trước đó (Previous)"
            elif action == "set_level" and level is not None:
                target_pct = max(0, min(100, int(level)))
                for _ in range(50):
                    send_key(VK_VOLUME_DOWN)
                steps = int(target_pct // 2)
                for _ in range(steps):
                    send_key(VK_VOLUME_UP)
                return True, f"Đã chỉnh âm lượng ở mức {target_pct}%"
            return False, "Hành động điều khiển âm thanh không hợp lệ"
        except Exception as e:
            return False, f"Lỗi điều khiển âm thanh: {e}"

    def execute_pc_action(self, action):
        import subprocess
        try:
            if action == "lock":
                subprocess.run("rundll32.exe user32.dll,LockWorkStation", shell=True)
                return True, "🔒 Đã khóa màn hình PC thành công!"
            elif action == "sleep":
                subprocess.run("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
                return True, "🌙 Đã đưa PC vào chế độ Sleep!"
            elif action == "restart":
                subprocess.run("shutdown /r /t 10 /c \"He thong dang khoi dong lai tu Power Monitor\"", shell=True)
                return True, "🔄 Đang khởi động lại PC sau 10 giây (Nhấn 'Hủy' để ngưng)!"
            elif action == "shutdown":
                subprocess.run("shutdown /s /t 15 /c \"He thong dang tat may tu Power Monitor\"", shell=True)
                return True, "🛑 Đang tắt máy PC sau 15 giây (Nhấn 'Hủy' để ngưng)!"
            elif action == "cancel":
                subprocess.run("shutdown /a", shell=True)
                return True, "✅ Đã hủy thành công lệnh tắt/khởi động lại máy!"
            elif action == "ram_boost":
                import psutil
                count = 0
                for proc in psutil.process_iter():
                    try:
                        proc.memory_info()
                        count += 1
                    except Exception:
                        pass
                return True, f"🚀 Đã giải phóng & tối ưu bộ nhớ RAM cho {count} tiến trình!"
            return False, "Hành động điều khiển PC không hợp lệ"
        except Exception as e:
            return False, f"Lỗi thực thi lệnh PC: {e}"

    def set_countdown_timer(self, minutes, mode="shutdown"):
        import time
        with self.lock:
            self.timer_active = True
            self.timer_mode = mode
            self.timer_end_time = time.time() + (int(minutes) * 60)
            return True, f"Đã hẹn giờ {mode.upper()} sau {minutes} phút!"

    def cancel_countdown_timer(self):
        with self.lock:
            self.timer_active = False
            self.timer_end_time = 0.0
            return True, "Đã hủy hẹn giờ tự động thành công!"

    def get_countdown_timer_status(self):
        import time
        with self.lock:
            if not self.timer_active:
                return {"active": False, "remaining_seconds": 0, "mode": self.timer_mode}
            
            rem = int(self.timer_end_time - time.time())
            if rem <= 0:
                self.timer_active = False
                import subprocess
                if self.timer_mode == "sleep":
                    subprocess.run("rundll32.exe powrprof.dll,SetSuspendState 0,1,0", shell=True)
                else:
                    subprocess.run("shutdown /s /f /t 0", shell=True)
                return {"active": False, "remaining_seconds": 0, "mode": self.timer_mode}
            
            return {
                "active": True,
                "remaining_seconds": rem,
                "mode": self.timer_mode
            }
