import os
import sys
import webbrowser
import threading
import pystray
from PIL import Image, ImageDraw, ImageFont

def create_tray_image(width=64, height=64):
    """Generate a sleek neon lightning icon for System Tray."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dc = ImageDraw.Draw(image)
    
    # Dark rounded background
    dc.rounded_rectangle([2, 2, width-2, height-2], radius=14, fill=(15, 23, 42, 255), outline=(0, 242, 254, 200), width=2)
    
    # Draw lightning bolt polygon
    bolt_points = [
        (34, 8),   # Top right
        (16, 34),  # Middle left
        (30, 34),  # Inner middle
        (26, 56),  # Bottom tip
        (46, 28),  # Middle right
        (32, 28)   # Inner top
    ]
    dc.polygon(bolt_points, fill=(0, 242, 254, 255), outline=(255, 255, 255, 255))
    
    return image

class SystemTrayApp:
    def __init__(self, monitor_instance, port=38472, on_exit_callback=None):
        self.monitor = monitor_instance
        self.port = port
        self.on_exit_callback = on_exit_callback
        self.icon = None

    def open_dashboard(self, icon=None, item=None):
        webbrowser.open(f"http://localhost:{self.port}")

    def reload_source(self, icon=None, item=None):
        """Force reload LibreHardwareMonitor connection & refresh sensor readings."""
        try:
            self.monitor.try_launch_lhm()
            self.monitor.update_cycle(0.1)
            state = self.monitor.current_state
            status = state.get('status_msg', 'OK')
            msg = f"Đã làm mới dữ liệu cảm biến phần cứng!\nTrạng thái: {status}"
            if self.icon:
                self.icon.notify(msg, title="🔄 Reload Source Success")
        except Exception as e:
            if self.icon:
                self.icon.notify(f"Lỗi khi làm mới: {e}", title="⚠️ Reload Error")

    def show_info(self, icon=None, item=None):
        state = self.monitor.current_state
        kwh = state.get("today_kwh", 0.0)
        cost = state.get("today_cost_vnd", 0.0)
        watts = state.get("total_power_w", 0.0)
        msg = f"Công suất live: {watts:.1f} W\nSố điện hôm nay: {kwh:.3f} kWh\nTiền điện tạm tính: {cost:,.0f} VND"
        if self.icon:
            self.icon.notify(msg, title="⚡ Power Monitor PC")

    def exit_app(self, icon=None, item=None):
        if self.icon:
            self.icon.stop()
        if self.on_exit_callback:
            self.on_exit_callback()
        os._exit(0)

    def update_tooltip(self):
        """Update system tray tooltip periodically."""
        if self.icon:
            state = self.monitor.current_state
            watts = state.get("total_power_w", 0.0)
            kwh = state.get("today_kwh", 0.0)
            self.icon.title = f"Power Monitor: {watts:.1f}W | Hôm nay: {kwh:.3f}kWh"

    def setup_menu(self):
        menu = pystray.Menu(
            pystray.MenuItem("⚡ Xem Dashboard (View Site)", self.open_dashboard, default=True),
            pystray.MenuItem("🔄 Tải lại dữ liệu LHM (Reload Source)", self.reload_source),
            pystray.MenuItem("📊 Thống kê hôm nay", self.show_info),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 Thoát (Exit)", self.exit_app)
        )
        return menu

    def run(self):
        img = create_tray_image()
        menu = self.setup_menu()
        self.icon = pystray.Icon("PowerMonitorPC", img, title="Power Monitor PC", menu=menu)
        
        def tooltip_loop():
            import time
            while True:
                time.sleep(3)
                self.update_tooltip()
                
        t = threading.Thread(target=tooltip_loop, daemon=True)
        t.start()
        
        self.icon.run()

    def run_detached(self):
        t = threading.Thread(target=self.run, daemon=True)
        t.start()
        return t
