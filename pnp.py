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
        self.root.title("PnP Command Station")
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

        self._init_ui()
        self.set_step_size(1.0) 
        self.root.after(100, self._init_camera)
        self._bind_keys()

    # --- LOGGING ---
    def log(self, msg):
        print(f"[LOG] {msg}")
        if hasattr(self, 'log_text'):
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)

    # --- CUSTOM BUTTON ---
    def create_button(self, parent, text, command, bg="#333", fg="white", width=None):
        btn = tk.Label(parent, text=text, bg=bg, fg=fg, font=("Consolas", 10, "bold"), cursor="pointinghand")
        if width: btn.configure(width=width)
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
        # Used 'Avenir Next' for that clean Sci-Fi look on macOS
        tk.Label(control_frame, text="PnP CONTROL", fg="#00ffcc", bg="#1a1a1a", font=("Avenir Next", 30, "bold")).pack(pady=(0,15))

        # --- 1. CONNECTION ---
        conn_frame = tk.LabelFrame(control_frame, text=" HARDWARE ", bg="#1a1a1a", fg="#888", font=("Consolas", 10))
        conn_frame.pack(fill=tk.X, pady=5)
        
        self.port_combo = ttk.Combobox(conn_frame, values=self._get_ports())
        self.port_combo.pack(pady=5, padx=5, fill=tk.X)
        if self.port_combo['values']: self.port_combo.current(0)
        
        self.btn_connect = self.create_button(conn_frame, "CONNECT PRINTER", self._toggle_connection, bg="#333")
        self.btn_connect.pack(pady=5, padx=5, fill=tk.X)

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
            (10.0, "10.0 (\)")
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
    def set_step_size(self, val):
        self.step_size = val
        for v, btn in self.step_btns.items():
            if v == val:
                btn.config(bg="#00aac0", fg="black")
            else:
                btn.config(bg="#333", fg="white")
        self.log(f"Step Size: {val}mm")

    def _get_ports(self):
        return [comport.device for comport in serial.tools.list_ports.comports()]

    def _toggle_connection(self):
        if not self.is_connected:
            try:
                port = self.port_combo.get()
                if not port:
                    self.log("No port selected!")
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
            if self.serial_conn: self.serial_conn.close()
            self.is_connected = False
            self.btn_connect.config(text="CONNECT PRINTER", bg="#333", fg="white")
            self.log("Disconnected")

    def _init_camera(self):
        self.current_cam_index = 1
        self._open_camera(self.current_cam_index)
        self._update_video_loop()

    def _cycle_camera(self):
        self.current_cam_index = (self.current_cam_index + 1) % 3
        self.log(f"Switching to Camera Index: {self.current_cam_index}")
        self._open_camera(self.current_cam_index)

    def _open_camera(self, index):
        if self.cam: self.cam.release()
        system_os = platform.system()
        backend = cv2.CAP_AVFOUNDATION if system_os == 'Darwin' else cv2.CAP_ANY
        try:
            self.cam = cv2.VideoCapture(index, backend)
            if not self.cam.isOpened():
                self.log(f"Cam {index} failed. Trying next...")
            else:
                self.log(f"Cam {index} Active")
                self.cam.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                self.cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        except Exception as e:
            self.log(f"Cam Error: {e}")

    def _update_video_loop(self):
        if self.cam and self.cam.isOpened():
            ret, frame = self.cam.read()
            if ret:
                if self.show_crosshair.get():
                    h, w, _ = frame.shape
                    cx = (w // 2) + self.offset_x.get()
                    cy = (h // 2) + self.offset_y.get()
                    color = (0, 255, 255) 
                    cv2.line(frame, (cx - 30, cy), (cx + 30, cy), color, 1)
                    cv2.line(frame, (cx, cy - 30), (cx, cy + 30), color, 1)
                    cv2.circle(frame, (cx, cy), 3, color, 1)

                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)
                imgtk = ImageTk.PhotoImage(image=img)
                self.video_frame.imgtk = imgtk
                self.video_frame.configure(image=imgtk)
        
        self.root.after(30, self._update_video_loop)

    def _bind_keys(self):
        self.root.bind("<Up>", lambda e: self.move('Y', 1))
        self.root.bind("<Down>", lambda e: self.move('Y', -1))
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
        if not self.is_connected: return
        dist = self.step_size
        speed = self.z_speed if axis == 'Z' else self.xy_speed
        self.send_gcode(f"G0 {axis}{dist * direction:.3f} F{speed}")

    def send_gcode(self, cmd):
        if self.serial_conn and self.serial_conn.is_open:
            full_cmd = f"{cmd}\n"
            self.serial_conn.write(full_cmd.encode())
            self.log(f"> {cmd}")

    def emergency_stop(self, event=None):
        self.send_gcode("M112")
        self.log("!!! EMERGENCY STOP !!!")
        self.is_connected = False
        self.btn_connect.config(text="CONNECT (RESET)", bg="red")

    def __del__(self):
        if self.cam: self.cam.release()
        if self.serial_conn: self.serial_conn.close()

if __name__ == "__main__":
    root = tk.Tk()
    app = PnPController(root)
    root.mainloop()