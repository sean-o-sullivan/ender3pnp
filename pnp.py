#!/usr/bin/env python3
"""
Sean's PnP Command Station - V13 (Patched for macOS)
- Non-blocking USB port scanning (background thread + thread-safe UI updates)
- Combobox click-to-refresh (safe)
- Improved camera initialization for macOS (AVFoundation)
- Defensive threading and error handling

Save this file and run with: python3 pnp_controller_mac_fixed.py
"""

import tkinter as tk
from tkinter import ttk
import cv2
from PIL import Image, ImageTk
import serial
import serial.tools.list_ports
import threading
import time
import platform


class PnPController:
    def __init__(self, root):
        self.root = root
        self.root.title("Sean's PnP Command Station - V13 - macOS Safe")
        self.root.geometry("1300x850")
        self.root.configure(bg="#111")

        # --- Configuration ---
        self.baud_rate = 115200
        self.serial_conn = None
        self.cam = None
        self.is_connected = False
        self.current_cam_index = 0

        self.xy_speed = 1500
        self.z_speed = 300
        self.step_size = 1.0

        self.offset_x = tk.IntVar(value=0)
        self.offset_y = tk.IntVar(value=0)
        self.show_crosshair = tk.BooleanVar(value=True)

        self.step_btns = {}

        # port scanning state flag
        self.port_refreshing = False

        self._init_ui()
        self.set_step_size(1.0)

        # Initialize Camera safely on macOS (use AVFoundation)
        self.root.after(100, self._init_camera)

        # Auto-scan ports in background
        self.start_port_scan()

        self._bind_keys()

    # --- LOGGING ---
    def log(self, msg):
        print(f"[LOG] {msg}")
        if hasattr(self, 'log_text'):
            try:
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
            except Exception:
                pass

    # --- CUSTOM BUTTON ---
    def create_button(self, parent, text, command, bg="#333", fg="white", width=None, font=("Consolas", 10, "bold")):
        btn = tk.Label(parent, text=text, bg=bg, fg=fg, font=font, cursor="pointinghand")
        if width:
            btn.configure(width=width)
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>", lambda e: btn.config(bg="#444" if btn.cget("bg") != "#00aac0" else "#0088a0"))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg if btn.cget("bg") != "#00aac0" else "#00aac0"))
        return btn

    def _init_ui(self):
        control_frame = tk.Frame(self.root, bg="#1a1a1a", padx=15, pady=15, width=340)
        control_frame.pack(side=tk.LEFT, fill=tk.Y)
        control_frame.pack_propagate(False)

        self.video_frame = tk.Label(self.root, bg="black")
        self.video_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # --- HEADER ---
        tk.Label(control_frame, text="PnP CONTROL", fg="#00ffcc", bg="#1a1a1a", font=("Avenir Next", 30, "bold")).pack(pady=(0,15))

        # --- 1. CONNECTION ---
        conn_frame = tk.LabelFrame(control_frame, text=" HARDWARE ", bg="#1a1a1a", fg="#888", font=("Consolas", 10))
        conn_frame.pack(fill=tk.X, pady=5)

        # Port Frame
        port_box = tk.Frame(conn_frame, bg="#1a1a1a")
        port_box.pack(fill=tk.X, pady=5, padx=5)

        # Create combobox with initial placeholder (tuple values are safer)
        self.port_combo = ttk.Combobox(port_box, values=("Scanning...",), state="readonly")
        self.port_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Bind combobox click to safe refresh (no-op if already scanning)
        self.port_combo.bind("<Button-1>", lambda e: self.start_port_scan())

        # Refresh Button
        self.create_button(port_box, "↻", lambda: self.start_port_scan(), width=3).pack(side=tk.RIGHT, padx=(5,0))

        # Connect Button
        self.btn_connect = self.create_button(conn_frame, "CONNECT PRINTER", self._toggle_connection, bg="#333")
        self.btn_connect.pack(pady=5, padx=5, fill=tk.X)

        # FANS OFF BUTTON (New)
        self.btn_fan = self.create_button(conn_frame, "KILL FANS (M107)", self._kill_fans, bg="#552222", fg="#ffcccc")
        self.btn_fan.pack(pady=5, padx=5, fill=tk.X)

        # Cycle Cam
        self.btn_cam = self.create_button(conn_frame, "CYCLE CAMERA", self._cycle_camera, bg="#222", fg="#aaa")
        self.btn_cam.pack(pady=5, padx=5, fill=tk.X)

        # --- 2. STEP SIZE SELECTOR ---
        step_lbl_frame = tk.LabelFrame(control_frame, text=" STEP SIZE ", bg="#1a1a1a", fg="#fa0", font=("Consolas", 10, "bold"))
        step_lbl_frame.pack(fill=tk.X, pady=20)

        btn_grid = tk.Frame(step_lbl_frame, bg="#1a1a1a")
        btn_grid.pack(fill=tk.X, padx=5, pady=5)

        steps = [
            (0.01, "0.01 (n)"),
            (0.1,  "0.10 (s)"),
            (1.0,  "1.00 (-)"),
            (10.0, "10.0 (\\)")
        ]

        for i, (val, label) in enumerate(steps):
            btn = tk.Label(btn_grid, text=label, font=("Consolas", 11, "bold"), 
                           bg="#333", fg="white", width=8, pady=8, cursor="pointinghand")
            btn.bind("<Button-1>", lambda e, v=val: self.set_step_size(v))
            btn.grid(row=i//2, column=i%2, padx=3, pady=3, sticky="ew")
            self.step_btns[val] = btn

        btn_grid.columnconfigure(0, weight=1)
        btn_grid.columnconfigure(1, weight=1)

        # --- 3. CALIBRATION ---
        cal_frame = tk.LabelFrame(control_frame, text=" OFFSET CALIBRATION ", bg="#1a1a1a", fg="#888", font=("Consolas", 10))
        cal_frame.pack(fill=tk.X, pady=10)

        tk.Checkbutton(cal_frame, text="Show Crosshair", variable=self.show_crosshair, 
                       bg="#1a1a1a", fg="white", selectcolor="#333", activebackground="#1a1a1a").pack(anchor="w", padx=5)

        tk.Scale(cal_frame, variable=self.offset_x, from_=-600, to=600, orient=tk.HORIZONTAL, 
                 bg="#1a1a1a", fg="white", label="X Offset", troughcolor="#333", highlightthickness=0).pack(fill=tk.X, padx=5)

        tk.Scale(cal_frame, variable=self.offset_y, from_=-400, to=400, orient=tk.HORIZONTAL, 
                 bg="#1a1a1a", fg="white", label="Y Offset", troughcolor="#333", highlightthickness=0).pack(fill=tk.X, padx=5)

        self.create_button(cal_frame, "ZERO OFFSETS", lambda: [self.offset_x.set(0), self.offset_y.set(0)]).pack(pady=5, padx=5, fill=tk.X)

        # --- 4. LOGS ---
        tk.Label(control_frame, text="STATUS LOG", fg="#555", bg="#1a1a1a", font=("Consolas", 9)).pack(pady=(20,0), anchor="w")
        self.log_text = tk.Text(control_frame, height=10, bg="#000", fg="#0f0", font=("Consolas", 9), relief=tk.FLAT, highlightthickness=0)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # --- LOGIC ---
    def _kill_fans(self):
        # Send M107 to turn off fan (usually Part Cooling Fan 0)
        self.send_gcode("M107")
        self.log("Sent M107 (Fans Off)")

    def set_step_size(self, val):
        self.step_size = val
        for v, btn in self.step_btns.items():
            if v == val:
                btn.config(bg="#00aac0", fg="black")
            else:
                btn.config(bg="#333", fg="white")
        self.log(f"Step Size: {val}mm")

    # --- Port scanning helpers ---
    def start_port_scan(self):
        """Start a background port scan if one isn't already running."""
        # Avoid launching many threads from repeated clicks
        if getattr(self, "port_refreshing", False):
            return
        # Launch the background refresh
        threading.Thread(target=self._refresh_ports, daemon=True).start()

    def _refresh_ports(self):
        # Prevent re-entrancy
        if getattr(self, "port_refreshing", False):
            return

        self.port_refreshing = True

        # Immediately show scanning UI and disable combobox (from main thread)
        def ui_scanning():
            try:
                self.port_combo.configure(state="disabled")
                self.port_combo.set("Scanning...")
                self.port_combo['values'] = ("Scanning...",)
            except Exception:
                pass
        self.root.after(0, ui_scanning)

        def worker():
            clean_ports = []
            try:
                # Heavy/possibly slow call runs in background
                all_ports = serial.tools.list_ports.comports()
                for p in all_ports:
                    try:
                        name = p.device.lower()
                    except Exception:
                        name = str(p.device).lower()
                    if "bluetooth" not in name and "wireless" not in name:
                        clean_ports.append(p.device)
            except Exception as e:
                clean_ports = []
                self.log(f"Scan error (worker): {e}")

            # Now update UI from main thread
            def update_ui():
                try:
                    if clean_ports:
                        vals = tuple(clean_ports)
                        self.port_combo['values'] = vals
                        try:
                            self.port_combo.current(0)
                        except Exception:
                            pass
                        self.log(f"Found {len(clean_ports)} device(s).")
                    else:
                        self.port_combo['values'] = ("No USB Found",)
                        self.port_combo.set("No USB Found")
                        self.log("No USB ports found.")
                    # restore readonly state
                    self.port_combo.configure(state="readonly")
                except Exception as e:
                    self.log(f"UI update error: {e}")
                finally:
                    self.port_refreshing = False

            self.root.after(0, update_ui)

        # run the worker and return immediately
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _toggle_connection(self):
        if not self.is_connected:
            try:
                port = self.port_combo.get()
                if not port or port == "Scanning..." or port == "No USB Found":
                    self.log("No valid port selected!")
                    return
                self.serial_conn = serial.Serial(port, self.baud_rate, timeout=1)
                time.sleep(2)
                self.send_gcode("G91")
                self.log(f"CONNECTED: {port}")
                self.btn_connect.config(text="DISCONNECT", bg="#00aa00", fg="black")
                self.is_connected = True
                self.root.focus_set()
            except Exception as e:
                self.log(f"Conn Error: {e}")
        else:
            if self.serial_conn:
                try:
                    self.serial_conn.close()
                except Exception:
                    pass
            self.is_connected = False
            self.btn_connect.config(text="CONNECT PRINTER", bg="#333", fg="white")
            self.log("Disconnected")

    def _init_camera(self):
        # Prefer index 0 then 1, but allow cycling later
        self.current_cam_index = 0
        self._open_camera(self.current_cam_index)
        self._update_video_loop()

    def _cycle_camera(self):
        # try next index, this will attempt to open it
        self.current_cam_index = (self.current_cam_index + 1) % 4
        self.log(f"Switching to Camera Index: {self.current_cam_index}")
        self._open_camera(self.current_cam_index)

    def _open_camera(self, index):
        # Release previous camera if any
        try:
            if self.cam:
                try:
                    self.cam.release()
                except Exception:
                    pass
                self.cam = None

            # macOS: prefer AVFoundation
            system_os = platform.system()
            if system_os == 'Darwin':
                backend = cv2.CAP_AVFOUNDATION
            else:
                backend = cv2.CAP_ANY

            self.log(f"Opening camera index {index} with backend {backend}")
            cam = cv2.VideoCapture(index, backend)

            # small delay for device warm-up
            time.sleep(0.2)

            if not cam or not cam.isOpened():
                self.log(f"Cam {index} failed to open.")
                try:
                    if cam:
                        cam.release()
                except Exception:
                    pass
                self.cam = None
            else:
                self.cam = cam
                self.log(f"Cam {index} Active")
                try:
                    self.cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    self.cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                except Exception:
                    pass
        except Exception as e:
            self.log(f"Cam Error: {e}")

    def _update_video_loop(self):
        if self.cam and getattr(self.cam, 'isOpened', lambda: False)():
            try:
                ret, frame = self.cam.read()
                if ret and frame is not None:
                    if self.show_crosshair.get():
                        try:
                            h, w, _ = frame.shape
                            cx = (w // 2) + self.offset_x.get()
                            cy = (h // 2) + self.offset_y.get()
                            color = (0, 255, 255)
                            cv2.line(frame, (cx - 30, cy), (cx + 30, cy), color, 1)
                            cv2.line(frame, (cx, cy - 30), (cx, cy + 30), color, 1)
                            cv2.circle(frame, (cx, cy), 3, color, 1)
                        except Exception:
                            pass

                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame)
                    imgtk = ImageTk.PhotoImage(image=img)
                    # attach to widget to prevent GC
                    self.video_frame.imgtk = imgtk
                    self.video_frame.configure(image=imgtk)
            except Exception as e:
                self.log(f"Video loop error: {e}")

        # schedule next frame
        self.root.after(30, self._update_video_loop)

    def _bind_keys(self):
        self.root.bind("<Up>", lambda e: self.move('Y', -1))
        self.root.bind("<Down>", lambda e: self.move('Y', 1))
        self.root.bind("<Left>", lambda e: self.move('X', -1))
        self.root.bind("<Right>", lambda e: self.move('X', 1))

        self.root.bind("<v>", lambda e: self.move('Z', 1))
        self.root.bind("<V>", lambda e: self.move('Z', 1))
        self.root.bind("<z>", lambda e: self.move('Z', -1))
        self.root.bind("<Z>", lambda e: self.move('Z', -1))
        self.root.bind("<n>", lambda e: self.set_step_size(0.01))
        self.root.bind("<s>", lambda e: self.set_step_size(0.1))
        self.root.bind("<minus>", lambda e: self.set_step_size(1.0))
        self.root.bind("<backslash>", lambda e: self.set_step_size(10.0))
        self.root.bind("<space>", self.emergency_stop)

    def move(self, axis, direction):
        if not self.is_connected:
            return
        dist = self.step_size
        speed = self.z_speed if axis == 'Z' else self.xy_speed
        self.send_gcode(f"G0 {axis}{dist * direction:.3f} F{speed}")

    def send_gcode(self, cmd):
        if self.serial_conn and getattr(self.serial_conn, 'is_open', False):
            try:
                full_cmd = f"{cmd}\n"
                self.serial_conn.write(full_cmd.encode())
                self.log(f"> {cmd}")
            except Exception as e:
                self.log(f"Serial write error: {e}")

    def emergency_stop(self, event=None):
        self.send_gcode("M112")
        self.log("!!! EMERGENCY STOP !!!")
        self.is_connected = False
        try:
            self.btn_connect.config(text="CONNECT (RESET)", bg="red")
        except Exception:
            pass

    def __del__(self):
        try:
            if self.cam:
                self.cam.release()
        except Exception:
            pass
        try:
            if self.serial_conn:
                self.serial_conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = PnPController(root)
    root.mainloop()
