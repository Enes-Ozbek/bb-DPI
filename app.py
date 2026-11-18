"""
bb-DPI — A Cloudflare WARP–inspired toggle for GoodbyeDPI.
PyQt6 · System-tray · Borderless · Fluent Dark Mode
"""

import sys, os, re, subprocess, time, logging, configparser, threading, shlex, tempfile, ctypes
from collections import deque
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel,
    QSystemTrayIcon, QMenu, QGraphicsDropShadowEffect,
    QHBoxLayout, QFrame, QMessageBox,
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QPoint, QSize, QRectF,
    QPropertyAnimation, pyqtProperty, QEasingCurve, QLockFile, QUrl,
)
from PyQt6.QtGui import (
    QIcon, QPainter, QColor, QFont, QFontDatabase,
    QPen, QBrush, QRadialGradient, QLinearGradient,
    QAction, QPixmap, QPainterPath, QCursor, QDesktopServices,
)

IS_WINDOWS = os.name == "nt"

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "bb-DPI")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "app.log")
logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logging.info("--- Application Started ---")

# ── Paths ────────────────────────────────────────────────────────────────────
GOODBYEDPI_EXE = "goodbyedpi.exe"

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

# ── Config ───────────────────────────────────────────────────────────────────
CONFIG_PATH = os.path.join(get_base_dir(), "config.ini")
DEFAULT_ARGS = "-5"
DEFAULT_MODE_TEXT = "Mode 5  ·  Alternative 2"

# interpolation=None so a literal "%" in the arguments can't break parsing.
config = configparser.ConfigParser(interpolation=None)

if not os.path.exists(CONFIG_PATH):
    config["Settings"] = {
        "Arguments": DEFAULT_ARGS,
        "ModeText": DEFAULT_MODE_TEXT,
    }
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            config.write(f)
        logging.info("Created default config.ini")
    except Exception as e:
        logging.error(f"Could not create config.ini: {e}")

try:
    config.read(CONFIG_PATH, encoding="utf-8")
except Exception as e:
    logging.error(f"Error reading config: {e}")

# Arguments are kept as one string and handed to goodbyedpi as-is (like a .cmd
# file would), so quoted paths such as --blacklist "C:\My Lists\x.txt" work.
GDPI_ARGS = config.get("Settings", "Arguments", fallback=DEFAULT_ARGS).strip()
MODE_TEXT = config.get("Settings", "ModeText", fallback=DEFAULT_MODE_TEXT)

# Auto-detect: looks next to this script, in ./x86_64, ./goodbyedpi, etc.
def find_gdpi():
    import glob
    base = get_base_dir()
    candidates = [
        base,
        os.path.join(base, "x86_64"),
        os.path.join(base, "goodbyedpi"),
        os.path.join(base, "goodbyedpi", "x86_64"),
    ]
    for d in glob.glob(os.path.join(base, "goodbyedpi*")):
        if os.path.isdir(d):
            candidates.append(d)
            candidates.append(os.path.join(d, "x86_64"))

    for d in candidates:
        p = os.path.join(d, GOODBYEDPI_EXE)
        if os.path.isfile(p):
            return d, p
    return base, os.path.join(base, GOODBYEDPI_EXE)

# ── Palette ──────────────────────────────────────────────────────────────────
C = dict(
    bg="#020617",       card="#0f172a",      surface="#1e293b",
    border="#1e293b",    text="#f8fafc",      muted="#94a3b8",
    dim="#64748b",       accent="#3b82f6",    green="#10b981",
    red="#ef4444",       ring_off="#334155",  glow="#3b82f6",
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Windows helpers – admin rights, pre-flight checks, kill-on-exit job
# ═══════════════════════════════════════════════════════════════════════════════
def is_admin():
    if not IS_WINDOWS:
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Ask UAC to start an elevated copy of bb-DPI. True if it was launched."""
    if getattr(sys, "frozen", False):
        params = sys.argv[1:]
    else:
        params = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    params = subprocess.list2cmdline(params + ["--elevated"])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, get_base_dir(), 1)
    except Exception as e:
        logging.error(f"Could not relaunch as Administrator: {e}")
        return False
    return rc > 32


def preflight(gdpi_dir, gdpi_path):
    """Catch the usual reasons goodbyedpi fails, before starting it.

    This matters because when goodbyedpi can't load its WinDivert driver it
    prints an error and then sleeps 20 s before exiting, so these failures
    would otherwise look like a working connection for a while.
    Returns an error message, or None if everything looks fine.
    """
    if not os.path.isfile(gdpi_path):
        return (f"goodbyedpi.exe not found. Put the GoodbyeDPI files next to "
                f"bb-DPI in: {get_base_dir()}")
    if IS_WINDOWS:
        if not os.path.isfile(os.path.join(gdpi_dir, "WinDivert.dll")):
            return f"WinDivert.dll is missing next to goodbyedpi.exe in: {gdpi_dir}"
        if not any(os.path.isfile(os.path.join(gdpi_dir, n))
                   for n in ("WinDivert64.sys", "WinDivert32.sys")):
            return f"WinDivert64.sys is missing next to goodbyedpi.exe in: {gdpi_dir}"
        if not is_admin():
            return "Administrator rights needed. Restart bb-DPI as Administrator."
    return None


class KillOnCloseJob:
    """Windows job object that takes goodbyedpi down together with bb-DPI.

    If bb-DPI crashes or is ended from Task Manager, Windows closes our handle
    to the job and terminates everything in it, so goodbyedpi can't be left
    running with no button to switch it off.
    """

    def __init__(self):
        self._handle = None
        if not IS_WINDOWS:
            return
        try:
            from ctypes import wintypes

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.SetInformationJobObject.restype = wintypes.BOOL
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.AssignProcessToJobObject.restype = wintypes.BOOL

            job = k32.CreateJobObjectW(None, None)
            if not job:
                raise ctypes.WinError(ctypes.get_last_error())
            info = EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            JobObjectExtendedLimitInformation = 9
            if not k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                               ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._k32, self._handle = k32, job
        except Exception as e:
            logging.warning(f"Kill-on-exit job unavailable: {e}")

    def assign(self, proc):
        if not self._handle:
            return
        try:
            if not self._k32.AssignProcessToJobObject(self._handle, int(proc._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
        except Exception as e:
            logging.warning(f"Could not attach goodbyedpi to kill-on-exit job: {e}")


def _hidden_window_kwargs():
    if not IS_WINDOWS:
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}


_ERROR_HINT = re.compile(r"error|can't|cannot|could not|should be used|not found|denied", re.I)

def summarize_output(lines, code):
    """Pick the most useful line out of goodbyedpi's console output."""
    if any(line.startswith("Usage:") for line in lines):
        return "goodbyedpi didn't accept the Arguments in config.ini"
    for i, line in enumerate(lines):
        if _ERROR_HINT.search(line):
            # e.g. "Error opening filter: 2 ..." + "The driver files ... were not found."
            if line.startswith("Error opening filter") and i + 1 < len(lines):
                return f"{line} {lines[i + 1]}"
            return line
    if lines:
        return lines[-1]
    return f"goodbyedpi.exe exited with code {code}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Worker thread – runs goodbyedpi.exe
# ═══════════════════════════════════════════════════════════════════════════════
STARTUP_GRACE = 1.5    # seconds goodbyedpi has to stay alive to count as connected
FAILURE_WINDOW = 30.0  # an exit within this many seconds means it never really worked


class DPIWorker(QThread):
    connected = pyqtSignal()
    failed    = pyqtSignal(str, str)   # (title, detail)

    def __init__(self, gdpi_dir, gdpi_path, args, job=None, parent=None):
        super().__init__(parent)
        self._dir, self._path, self._args, self._job = gdpi_dir, gdpi_path, args, job
        self._proc = None
        self._lock = threading.Lock()
        self._stopping = False
        self._output = deque(maxlen=60)

    def _command(self):
        if IS_WINDOWS:
            return f"{subprocess.list2cmdline([self._path])} {self._args}".strip()
        return [self._path] + shlex.split(self._args)

    def run(self):
        try:
            with self._lock:
                if self._stopping:
                    return
                self._proc = subprocess.Popen(
                    self._command(),
                    cwd=self._dir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    **_hidden_window_kwargs(),
                )
                proc = self._proc
        except OSError as e:
            if getattr(e, "winerror", None) == 740:  # ERROR_ELEVATION_REQUIRED
                detail = "Administrator rights needed. Restart bb-DPI as Administrator."
            else:
                detail = f"Could not start goodbyedpi.exe: {e.strerror or e}"
            logging.error(detail)
            self.failed.emit("Failed", detail)
            return

        logging.info(f"Started goodbyedpi.exe (PID {proc.pid}) with: {self._args}")
        if self._job:
            self._job.assign(proc)
        # Drain its output continuously so a full pipe can never block it.
        reader = threading.Thread(target=self._pump, args=(proc.stdout,), daemon=True)
        reader.start()
        started_at = time.monotonic()

        try:
            code = proc.wait(timeout=STARTUP_GRACE)
        except subprocess.TimeoutExpired:
            if not self._stopping:
                self.connected.emit()
            code = proc.wait()
        reader.join(timeout=2)

        if self._stopping:
            return
        lived = time.monotonic() - started_at
        lines = list(self._output)
        logging.error(f"goodbyedpi.exe exited by itself (code {code}) after {lived:.1f}s")
        title = "Failed" if lived < FAILURE_WINDOW else "Stopped"
        self.failed.emit(title, summarize_output(lines, code))

    def _pump(self, stream):
        encoding = "mbcs" if IS_WINDOWS else "utf-8"
        try:
            for raw in iter(stream.readline, b""):
                line = raw.decode(encoding, errors="replace").strip()
                if line:
                    self._output.append(line)
                    logging.info(f"[goodbyedpi] {line}")
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def stop(self, timeout=5.0):
        """Kill goodbyedpi. Returns False only if it is somehow still running."""
        with self._lock:
            self._stopping = True
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return True
        logging.info(f"Stopping goodbyedpi.exe (PID {proc.pid})...")
        try:
            proc.kill()
        except OSError as e:
            logging.error(f"Error terminating PID {proc.pid}: {e}")
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logging.error(f"goodbyedpi.exe (PID {proc.pid}) did not exit.")
            return False
        logging.info(f"Stopped PID {proc.pid}.")
        return True


# ═══════════════════════════════════════════════════════════════════════════════
#  Animated toggle switch widget
# ═══════════════════════════════════════════════════════════════════════════════
class ToggleSwitch(QWidget):
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._on = False
        self._connecting = False
        self._hover = False
        self._spin_angle = 0
        self._pulse = 0.0
        self._knob_pos = 0.0  # 0=off, 1=on for internal anim

        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(16)
        self._spin_timer.timeout.connect(self._tick_spin)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(30)
        self._pulse_timer.timeout.connect(self._tick_pulse)

    # ── state setters ──
    def set_connecting(self):
        self._connecting = True
        self._on = False
        self._spin_timer.start()
        self._pulse_timer.stop()
        self.update()

    def set_on(self):
        self._connecting = False
        self._on = True
        self._spin_timer.stop()
        self._pulse_timer.start()
        self.update()

    def set_off(self):
        self._connecting = False
        self._on = False
        self._spin_timer.stop()
        self._pulse_timer.stop()
        self._pulse = 0.0
        self.update()

    # ── timers ──
    def _tick_spin(self):
        self._spin_angle = (self._spin_angle + 4) % 360
        self.update()

    def _tick_pulse(self):
        self._pulse += 0.04
        if self._pulse > 6.2832:
            self._pulse -= 6.2832
        self.update()

    # ── events ──
    def enterEvent(self, ev):
        self._hover = True
        self.update()

    def leaveEvent(self, ev):
        self._hover = False
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    # ── paint ──
    def paintEvent(self, ev):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2

        # Active accent colour
        if self._on:
            accent = QColor(C["green"])
        elif self._connecting:
            accent = QColor(C["accent"])
        else:
            accent = QColor(C["ring_off"])

        # ════════════════════════════════════════════
        #  Outer glow (only when on / connecting)
        # ════════════════════════════════════════════
        if self._on or self._connecting:
            pulse_s = 1.0 + 0.05 * math.sin(self._pulse) if self._on else 1.0
            for i in range(6):
                # Largest ring × pulse must stay inside the 200px widget,
                # otherwise the glow gets clipped into a square.
                r = 80 + i * 3
                gc = QColor(accent)
                gc.setAlpha(max(0, 45 - i * 8))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(gc))
                p.drawEllipse(QRectF(cx - r * pulse_s, cy - r * pulse_s,
                                     r * 2 * pulse_s, r * 2 * pulse_s))

        # ════════════════════════════════════════════
        #  Outer ring
        # ════════════════════════════════════════════
        ring_r = 78
        if self._connecting:
            # Background ring
            p.setPen(QPen(QColor(C["surface"]), 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))
            # Spinning accent arc
            arc_pen = QPen(accent, 5)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(arc_pen)
            p.drawArc(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2),
                       int(self._spin_angle * 16), 100 * 16)
        else:
            thickness = 5 if self._on else 4
            ring_color = QColor(accent)
            if not self._on and self._hover:
                ring_color = QColor("#475569")
            p.setPen(QPen(ring_color, thickness))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2))

        # ════════════════════════════════════════════
        #  Inner face — radial gradient for 3D depth
        # ════════════════════════════════════════════
        face_r = 64
        grad = QRadialGradient(cx, cy - face_r * 0.3, face_r * 1.4)
        if self._on:
            grad.setColorAt(0, QColor("#064e3b"))
            grad.setColorAt(1, QColor("#022c22"))
        else:
            center = QColor("#1e293b") if not self._hover else QColor("#334155")
            edge   = QColor("#0f172a") if not self._hover else QColor("#1e293b")
            grad.setColorAt(0, center)
            grad.setColorAt(1, edge)

        # Face border — subtle ring inside the main ring
        fb = QColor(accent)
        fb.setAlpha(70 if self._on else 50)
        p.setPen(QPen(fb, 2))
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - face_r, cy - face_r, face_r * 2, face_r * 2))

        # ════════════════════════════════════════════
        #  Power icon — large & bold
        # ════════════════════════════════════════════
        if self._on:
            ic = QColor(C["green"])
        elif self._connecting:
            ic = QColor(C["accent"])
        else:
            ic = QColor("#94a3b8") if not self._hover else QColor("#cbd5e1")

        icon_pen = QPen(ic, 4.0)
        icon_pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        # --- Shape 1: Arc (open circle with gap at top) ---
        # Centered exactly at (cx, cy), radius 22px
        # Gap = 80° at top (from 50° to 130°), so arc starts at 130° spans 280°
        arc_r = 22
        arc_rect = QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2)
        p.setPen(icon_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(arc_rect, 130 * 16, 280 * 16)

        # --- Shape 2: Vertical bar (separate from arc) ---
        # Originates slightly above center and extends upward past the arc
        p.setPen(icon_pen)
        p.drawLine(int(cx), int(cy - 6), int(cx), int(cy - arc_r - 4))

        p.end()


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════════════════
class MainWindow(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("bb-DPI")
        self.setWindowIcon(QIcon(get_resource_path("icon.ico")))
        self.setFixedSize(276, 416)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Remove Windows 11 1px border
        try:
            hwnd = int(self.winId())
            DWMWA_BORDER_COLOR = 34
            value = ctypes.c_int(0xFFFFFFFE) # DWMWA_COLOR_NONE
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_BORDER_COLOR, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            pass

        # "off" | "connecting" | "on" | "error"   ("error" behaves like "off")
        self._state = "off"
        self._worker = None
        self._uptime_start = 0
        self._job = KillOnCloseJob()

        self._build_ui()
        self._build_tray()

        self._uptime_timer = QTimer(self)
        self._uptime_timer.setInterval(1000)
        self._uptime_timer.timeout.connect(self._update_uptime)

        # For window dragging
        self._drag_pos = QPoint()

        self._set_state("off")

        # Startup validation (re-checked on every click, so dropping the
        # files in later works without restarting bb-DPI)
        problem = preflight(*find_gdpi())
        if problem:
            logging.error(problem)
            self._set_state("error", "Error", problem)

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Root layout
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # Card container with rounded corners
        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setStyleSheet(f"""
            #card {{
                background-color: {C['card']};
                border-radius: 18px;
                border: none;
            }}
        """)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # ── Title bar ──
        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(20, 14, 14, 0)

        title_lbl = QLabel("bb-DPI")
        title_lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {C['text']}; background: transparent;")
        title_bar.addWidget(title_lbl)

        title_bar.addStretch()

        close_btn = QLabel("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        close_btn.setFont(QFont("Segoe UI", 11))
        close_btn.setStyleSheet(f"""
            color: {C['muted']};
            background: transparent;
            border-radius: 14px;
        """)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.mousePressEvent = lambda e: self.hide()
        title_bar.addWidget(close_btn)

        card_layout.addLayout(title_bar)

        # ── Toggle ──
        self.toggle = ToggleSwitch()
        self.toggle.clicked.connect(self._on_toggle)
        toggle_wrapper = QHBoxLayout()
        toggle_wrapper.addStretch()
        toggle_wrapper.addWidget(self.toggle)
        toggle_wrapper.addStretch()
        card_layout.addSpacing(18)
        card_layout.addLayout(toggle_wrapper)

        # ── Status ──
        self.status_lbl = QLabel("Disconnected")
        self.status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_lbl.setFont(QFont("Segoe UI", 17, QFont.Weight.DemiBold))
        self.status_lbl.setStyleSheet(f"color: {C['dim']}; background: transparent;")
        card_layout.addSpacing(14)
        card_layout.addWidget(self.status_lbl)

        # ── Subtitle ── (wraps to two lines for error messages)
        self.sub_lbl = QLabel("Tap the button to connect")
        self.sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_lbl.setWordWrap(True)
        self.sub_lbl.setContentsMargins(18, 0, 18, 0)
        self.sub_lbl.setFont(QFont("Segoe UI", 10))
        self.sub_lbl.setStyleSheet(f"color: {C['muted']}; background: transparent;")
        card_layout.addSpacing(2)
        card_layout.addWidget(self.sub_lbl)

        # ── Uptime ──
        self.uptime_lbl = QLabel("")
        self.uptime_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.uptime_lbl.setFont(QFont("Consolas", 11))
        self.uptime_lbl.setStyleSheet(f"color: {C['muted']}; background: transparent;")
        card_layout.addSpacing(6)
        card_layout.addWidget(self.uptime_lbl)

        # ── Bottom pill ──
        pill = QFrame()
        pill.setFixedHeight(38)
        pill.setStyleSheet(f"""
            background-color: {C['bg']};
            border-radius: 19px;
        """)
        pill_lay = QHBoxLayout(pill)
        pill_lay.setContentsMargins(16, 0, 16, 0)
        shield = QLabel("🛡")
        shield.setFont(QFont("Segoe UI Emoji", 12))
        shield.setStyleSheet("background: transparent;")
        pill_lay.addWidget(shield)
        self.mode_lbl = QLabel(MODE_TEXT)
        self.mode_lbl.setFont(QFont("Segoe UI", 9))
        self.mode_lbl.setStyleSheet(f"color: {C['muted']}; background: transparent;")
        self.mode_lbl.setToolTip(f"goodbyedpi.exe {GDPI_ARGS}")
        pill_lay.addWidget(self.mode_lbl)
        pill_lay.addStretch()

        card_layout.addStretch()
        card_layout.addWidget(pill, 0, Qt.AlignmentFlag.AlignCenter)
        card_layout.addSpacing(16)

        # Put card in root
        root.addWidget(self.card)

    # ── Tray ────────────────────────────────────────────────────────────
    def _build_tray(self):
        # Build a simple coloured icon in-memory
        self.tray_icon = QSystemTrayIcon(self)
        self._set_tray_icon("off")

        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {C['card']};
                color: {C['text']};
                border: 1px solid {C['border']};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item:selected {{
                background-color: {C['surface']};
            }}
        """)
        self._toggle_action = QAction("Connect", self)
        self._toggle_action.triggered.connect(self._on_toggle)
        menu.addAction(self._toggle_action)

        show_action = QAction("Show", self)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        log_action = QAction("Open log", self)
        log_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(LOG_PATH)))
        menu.addAction(log_action)

        menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(quit_action)

        self._tray_menu = menu
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _set_tray_icon(self, state):
        """Create a tray icon using the logo with a status indicator."""
        size = 64
        base_pm = QPixmap(get_resource_path(os.path.join("assets", "logo_square.png")))
        if base_pm.isNull():
            pm = QPixmap(size, size)
            pm.fill(Qt.GlobalColor.transparent)
        else:
            pm = base_pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if state == "on":
            colour = QColor(C["green"])
        elif state == "connecting":
            colour = QColor(C["accent"])
        elif state == "error":
            colour = QColor(C["red"])
        else:
            colour = QColor(C["dim"])

        # Draw status indicator (small circle bottom right)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(QBrush(colour))
        p.drawEllipse(44, 44, 16, 16)
        p.end()

        self.tray_icon.setIcon(QIcon(pm))

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _show_window(self):
        # Position near tray (bottom-right of screen)
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.width() - 16
        y = screen.bottom() - self.height() - 16
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    # ── State / UI updates ──────────────────────────────────────────────
    def _set_status(self, title, colour, sub):
        self.status_lbl.setText(title)
        self.status_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        # Two lines fit under the button; the full text is in the tooltip/log.
        # (1.75 × the line width leaves slack for where word-wrap breaks.)
        line_width = self.width() - 36  # minus the label's side margins
        short = self.sub_lbl.fontMetrics().elidedText(
            sub, Qt.TextElideMode.ElideRight, int(line_width * 1.75))
        self.sub_lbl.setText(short)
        self.sub_lbl.setToolTip(sub if short != sub else "")
        self.tray_icon.setToolTip(f"bb-DPI · {title}")

    def _set_state(self, state, title="Failed", message=""):
        self._state = state
        if state == "connecting":
            self.toggle.set_connecting()
            self._set_status("Connecting…", C["accent"], "Starting GoodbyeDPI · tap to cancel")
        elif state == "on":
            self.toggle.set_on()
            self._set_status("Connected", C["green"], "DPI bypass is active")
            self._uptime_start = time.time()
            self._uptime_timer.start()
            self._update_uptime()
        elif state == "error":
            self.toggle.set_off()
            self._set_status(title, C["red"], message)
        else:
            self.toggle.set_off()
            self._set_status("Disconnected", C["dim"], "Tap the button to connect")

        if state != "on":
            self._uptime_timer.stop()
            self.uptime_lbl.setText("")
        # An empty uptime line still takes space; hide it to make room for
        # two-line error messages. The layout doesn't grow a word-wrapped
        # label by itself, so reserve the second line explicitly.
        self.uptime_lbl.setVisible(state == "on")
        two_lines = self.sub_lbl.fontMetrics().lineSpacing() * 2 + 2
        self.sub_lbl.setMinimumHeight(two_lines if state == "error" else 0)
        self._set_tray_icon(state)
        self._toggle_action.setText("Disconnect" if state in ("connecting", "on") else "Connect")

    def _show_error(self, title, message):
        logging.error(f"{title}: {message}")
        self._set_state("error", title, message)
        if not self.isVisible():
            self.tray_icon.showMessage(f"bb-DPI · {title}", message,
                                       QSystemTrayIcon.MessageIcon.Warning, 8000)

    # ── Toggle logic ────────────────────────────────────────────────────
    def _on_toggle(self):
        if self._state in ("connecting", "on"):
            self._stop()   # also cancels a start that's still in progress
        else:
            self._start()

    def _start(self):
        if self._worker is not None:
            # The previous goodbyedpi is still shutting down; never run two.
            return
        gdpi_dir, gdpi_path = find_gdpi()
        problem = preflight(gdpi_dir, gdpi_path)
        if problem:
            self._show_error("Failed", problem)
            return

        self._set_state("connecting")
        worker = DPIWorker(gdpi_dir, gdpi_path, GDPI_ARGS, self._job, parent=self)
        worker.connected.connect(self._on_connected)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def _on_connected(self):
        if self._state == "connecting":
            self._set_state("on")

    def _on_failed(self, title, message):
        # Ignore late reports after the user already switched it off.
        if self._state in ("connecting", "on"):
            self._show_error(title, message)

    def _on_worker_finished(self):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        if self._state in ("connecting", "on"):
            self._set_state("off")

    def _stop(self):
        worker = self._worker
        self._set_state("off")
        if worker is not None and not worker.stop():
            self._show_error("Error", "goodbyedpi.exe did not stop. End it in Task Manager.")

    def _update_uptime(self):
        if self._state != "on":
            return
        elapsed = int(time.time() - self._uptime_start)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        self.uptime_lbl.setText(f"{h:02d}:{m:02d}:{s:02d}")

    # ── Custom Shadow Painting ──────────────────────────────────────────
    def paintEvent(self, ev):
        pass

    # ── Dragging ────────────────────────────────────────────────────────
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = ev.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(ev.globalPosition().toPoint() - self._drag_pos)

    # ── Close hides, Quit kills ─────────────────────────────────────────
    def closeEvent(self, ev):
        ev.ignore()
        self.hide()

    def shutdown(self):
        """Stop goodbyedpi before bb-DPI exits (Quit, logoff, shutdown)."""
        worker = self._worker
        if worker is not None:
            worker.stop()
            worker.wait(3000)

    def _quit_app(self):
        self.shutdown()
        self.tray_icon.hide()
        QApplication.quit()


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    # goodbyedpi needs admin rights to load its driver. The built .exe asks for
    # them through its manifest; this covers running `python app.py`.
    if IS_WINDOWS and not is_admin() and "--elevated" not in sys.argv:
        if relaunch_as_admin():
            return
        logging.warning("Not running as Administrator (UAC prompt declined or failed).")

    # Fix High DPI scaling artifacts (like 1px gaps) on Windows 11
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    app.setFont(QFont("Segoe UI", 10))

    # Only one bb-DPI at a time; a second copy would start a second goodbyedpi.
    lock = QLockFile(os.path.join(tempfile.gettempdir(), "bb-DPI.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "bb-DPI",
                                "bb-DPI is already running. Look for its icon in the system tray.")
        return

    win = MainWindow()
    app.aboutToQuit.connect(win.shutdown)
    win._show_window()
    code = app.exec()
    lock.unlock()
    sys.exit(code)

if __name__ == "__main__":
    main()
