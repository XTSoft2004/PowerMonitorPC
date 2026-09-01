# ⚡ Power Monitor PC - Hệ Thống Đo Công Suất & Tự Động Thống Kê Tiền Điện PC

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/Python-3.9%2B-brightgreen.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg)](https://microsoft.com)

**Power Monitor PC** là phần mềm đo công suất tiêu thụ thực tế (Live Watt Meter) và tự động tính toán tiền điện tiêu thụ của máy tính theo **Biểu Giá 6 Bậc Thang Sinh Hoạt Mới Nhất Của EVN** (Bộ Công Thương / Luật Việt Nam) hoặc Đơn giá cố định tùy chỉnh.

---

## 🌟 Tính Năng Nổi Bật

- 🔌 **Đo Công Suất Thực Tế (Live AC Wall Power)**: Đọc thông số Watts của CPU (Intel Xeon/Core, AMD Ryzen), GPU (AMD Radeon RX, NVIDIA GeForce, Intel Arc) và tính tổn hao bộ nguồn PSU (80 Plus).
- 🏛️ **Tính Tiền Điện Bậc Thang EVN Chuẩn 100%**: Tự động áp dụng công thức lũy tiến 6 Bậc sinh hoạt EVN mới nhất + 8% thuế VAT hoặc chọn Chế độ Đơn giá cố định (VND/kWh).
- 💾 **Bảo Toàn Dữ Liệu Khi Tắt/Mở Máy (Reboot Resilience)**: Lưu trữ số liệu tích lũy kWh, công suất đỉnh, thời gian hoạt động theo ngày vào SQLite (`power_data.db`). Đột ngột tắt máy hay khởi động lại không bao giờ bị mất dữ liệu hôm nay.
- 🎨 **Giao Diện Web Dashboard Hiện Đại**: Thiết kế Dark Mode Glassmorphism mượt mà, tích hợp biểu đồ real-time (Chart.js), thống kê theo ngày và xuất báo cáo CSV.
- 🔕 **Khay Hệ Thống Windows (System Tray Icon)**: Chạy ngầm nhẹ nhàng dưới taskbar, hỗ trợ chuột phải: *Xem Dashboard*, *Tải lại nguồn dữ liệu*, *Thông báo Toast hôm nay*, *Thoát*.
- 📦 **Phiên Bản Portable Độc Lập 100%**: Không cần cài đặt Python hay bất kỳ thư viện nào, copy thư mục sang máy khác là bấm chạy ngay!

---

## 🏛️ Bảng Giá Điện Sinh Hoạt EVN Được Tích Hợp

| Bậc thang | Mức sử dụng trong tháng | Giá bán lẻ EVN | Ghi chú |
| :---: | :---: | :---: | :--- |
| **Bậc 1** | Cho 50 kWh đầu tiên ($0 - 50$ kWh) | **1.984 đ** / kWh | Hộ dùng ít |
| **Bậc 2** | Cho 50 kWh tiếp theo ($51 - 100$ kWh) | **2.050 đ** / kWh | Hộ trung bình |
| **Bậc 3** | Cho 100 kWh tiếp theo ($101 - 200$ kWh) | **2.380 đ** / kWh | Hộ phổ biến |
| **Bậc 4** | Cho 100 kWh tiếp theo ($201 - 300$ kWh) | **2.998 đ** / kWh | Hộ tiêu thụ cao |
| **Bậc 5** | Cho 100 kWh tiếp theo ($301 - 400$ kWh) | **3.350 đ** / kWh | Hộ tiêu thụ rất cao |
| **Bậc 6** | Từ kWh thứ $401$ trở lên | **3.460 đ** / kWh | Bậc cao nhất |

*(Mặc định cộng **8% thuế VAT** vào tiền điện tạm tính).*

---

## 📁 Cấu Trúc Thư Mục Dự Án

```
Tinh_dien_pc/
├── PowerMonitorPC.exe            # Tập tin chạy Portable độc lập (Không cần Python)
├── Chay_Ngay.bat                 # Kịch bản khởi động nhanh 1-Click
├── install_autostart.bat         # Kịch bản cài đặt tự chạy cùng Windows (Không bị UAC)
├── config.json                   # Tập tin cấu hình đơn giá & công suất nền
├── power_monitor.py              # Động cơ thu thập dữ liệu & tính toán điện năng
├── server.py                     # Flask Web Server & REST API endpoints
├── tray_icon.py                  # Khay hệ thống Windows (pystray)
├── register_tasks.ps1            # Kịch bản đăng ký Task Scheduler
├── templates/
│   └── index.html                # Giao diện Web Dashboard Dark Mode
├── static/
│   └── style.css                 # CSS Glassmorphism
└── LibreHardwareMonitor.NET.10/  # Bộ đọc cảm biến phần cứng LHM Portable
```

---

## 🚀 Hướng Dẫn Sử Dụng & Cài Đặt

### 📍 Cách 1: Sử dụng Bản Portable (Khuyên dùng - Không cần cài đặt)

1. Tải hoặc sao chép thư mục `Tinh_dien_pc` về máy tính.
2. Nhấp đôi tập tin **`PowerMonitorPC.exe`** (hoặc `Chay_Ngay.bat`).
3. Phần mềm sẽ tự động mở Khay hệ thống và bật màn hình Web Dashboard tại địa chỉ:
   👉 **[http://localhost:38472](http://localhost:38472)**

---

### 📍 Cách 2: Chạy Từ Mã Nguồn Python

#### Yêu cầu hệ thống:
- Python 3.9 trở lên
- Windows 10 / 11

#### Các bước thực hiện:
```bash
# 1. Cài đặt các thư viện phụ thuộc
pip install flask pystray pillow psutil

# 2. Khởi chạy ứng dụng
python server.py
```

---

### 📍 Cách 3: Cấu Hình Tự Khởi Động Cùng Windows (Không Hỏi Quyền Admin UAC)

Muốn phần mềm tự động chạy ngầm mỗi khi bật máy mà **không bao giờ bị hỏi hộp thoại Yes/No của Admin (UAC)**:

1. Nhấp chuột phải vào file **`install_autostart.bat`**.
2. Chọn **`Run as administrator`** (Chỉ cần làm 1 lần duy nhất).
3. Hệ thống sẽ đăng ký dịch vụ vào **Windows Task Scheduler** với quyền cao nhất.

---

## ⚙️ Cấu Hình Tùy Chỉnh (`config.json`)

Bạn có thể chỉnh sửa file `config.json` hoặc bấm vào nút **⚙️ Cấu hình** trên Web Dashboard:

```json
{
  "server_port": 38472,
  "pricing_mode": "evn_tiered",
  "electricity_rate_vnd_kwh": 2500,
  "lhm_url": "http://localhost:8085/data.json",
  "poll_interval_seconds": 3,
  "system_base_power_w": 30.0,
  "psu_efficiency_factor": 1.12
}
```

### Giải thích thông số:
- `pricing_mode`: `"evn_tiered"` (Tính 6 bậc EVN) hoặc `"fixed"` (Giá cố định).
- `electricity_rate_vnd_kwh`: Đơn giá cố định khi dùng chế độ `"fixed"`.
- `system_base_power_w`: Công suất tiêu thụ nền của Mainboard, RAM, SSD, Fan (Mặc định `30.0 W`).
- `psu_efficiency_factor`: Hệ số tổn hao của Bộ nguồn PSU (Mặc định `1.12` cho nguồn 80 Plus Gold/Bronze).

---

## 🤝 Đóng Góp & Phát Triển (Contributing)

Mọi đóng góp báo lỗi (Issues) hoặc yêu cầu tính năng mới (Pull Requests) đều được hoan nghênh!

---

## 📜 Giấy Phép (License)

Dự án được phát hành theo giấy phép **MIT License**.
