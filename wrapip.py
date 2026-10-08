# -*- coding: utf-8 -*-
"""
WrapIP — Mini Modern Analog Meter (tanpa jarum)
==============================================
- Meter bulat transparan ala widget desktop: arc 270° yang "bar" (isi)
  bergerak seperti aplikasi speedtest Ookla, TANPA jarum.
- Auto cari IP + geo, speedtest otomatis (ping / download / upload).
- Tombol CONNECT / DISCONNECT untuk ganti IP lewat proxy sistem Windows.
- Selalu di atas (always-on-top), bisa di-drag, tutup via tombol X.
- Satu file, CustomTkinter + Tkinter Canvas. Tanpa PIL.
"""

import ctypes
import http.client
import json
import math
import os
import random
import socket
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeout

import tkinter as tk

import customtkinter as ctk

# ---------------------------------------------------------------------------
# Konstanta tampilan
# ---------------------------------------------------------------------------

SIZE = 360
CX = CY = SIZE / 2.0

R_BODY = 180.0
R_EDGE = 174.0
R_GAUGE = 100.0
W_GAUGE = 15.0
A_START = 225.0   # 0 mulai di kiri-bawah, busur di atas (mirror), ke kanan
A_SWEEP = -270.0

# posisi UI (di dalam lingkaran)
Y_TICK_MIN = R_GAUGE + W_GAUGE / 2 + 5
Y_VALUE = 150
Y_UNIT = 181
Y_PHASE = 198
Y_RULE = 212
Y_IP = 230
Y_GEO = 252
Y_LABEL = 132.0

# card tombol
CARD_W = 132.0
CARD_H = 42.0
CARD_Y0 = 292.0
CARD_R = 12.0

SMALL_R = 14.0
SMALL_L = (117.0, 51.0)
SMALL_R_POS = (243.0, 51.0)

K_MIN, K_MAX, K_STEP = 0.55, 1.8, 0.08

# warna
KEY = "#000001"
BODY = "#12151B"
EDGE = "#232B36"
EDGE2 = "#303B49"
TRACK = "#1E242E"
TXT = "#EAF0F8"
DIM = "#7A879B"
FAINT = "#4C5666"
C_DOWN = "#2BD9A0"
C_UP = "#3FB6F5"
C_PING = "#F2A93B"
C_WARN = "#F26D6D"

FONT = "Segoe UI"
FONT_MONO = "Consolas"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    import sys as _sys

    if getattr(_sys, "frozen", False):
        BASE_DIR = os.path.dirname(_sys.executable)
except Exception:
    pass
CONFIG_PATH = os.path.join(BASE_DIR, "wrapip_config.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WrapIP/3.0"}

PROXY_SOURCES = [
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
]

try:
    import winreg

    HAS_WINREG = True
except ImportError:
    HAS_WINREG = False

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


# ---------------------------------------------------------------------------
# Jaringan
# ---------------------------------------------------------------------------

def refresh_urllib():
    """Muat ulang proxy urllib (baca registry Windows) setelah proxy berubah."""
    try:
        urllib.request.install_opener(
            urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies()))
        )
    except Exception:
        pass


def http_get(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


IP_ENDPOINTS = [
    "https://api.ipify.org?format=json",
    "https://ifconfig.me/all.json",
    "https://api.my-ip.io/v2/ip",
]


def get_public_ip(timeout=10):
    last = None
    for url in IP_ENDPOINTS:
        try:
            body = http_get(url, timeout)
            if "my-ip.io" in url:
                return body.strip()
            j = json.loads(body)
            ip = j.get("ip") or j.get("ip_addr")
            if ip:
                return ip.strip()
        except Exception as e:
            last = e
    raise RuntimeError("tidak bisa mendeteksi IP publik (%s)" % last)


def get_geo(ip, timeout=10):
    out = {"negara": "-", "kota": "-", "isp": "-"}
    try:
        j = json.loads(
            http_get(
                "http://ip-api.com/json/%s?fields=status,country,city,isp" % ip, timeout
            )
        )
        if j.get("status") == "success":
            out = {
                "negara": j.get("country", "-"),
                "kota": j.get("city", "-") or "-",
                "isp": (j.get("isp", "-") or "-")[:22],
            }
    except Exception:
        pass
    return out


PING_TARGETS = [("speed.cloudflare.com", 443), ("www.google.com", 443), ("1.1.1.1", 443)]


def ping_ms(timeout=3.0):
    samples = []
    for host, port in PING_TARGETS:
        t0 = time.time()
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            samples.append((time.time() - t0) * 1000.0)
        except Exception:
            continue
    if not samples:
        return -1
    samples.sort()
    return int(samples[len(samples) // 2])


def fetch_proxy_list(timeout=12):
    found, seen = [], set()
    for url in PROXY_SOURCES:
        try:
            raw = http_get(url, timeout).replace("\r", "\n")
            for line in raw.split("\n"):
                line = line.strip().split(" ")[0]
                if ":" not in line or line.startswith("#"):
                    continue
                host, _, port = line.partition(":")
                item = "%s:%s" % (host.strip(), port.strip())
                if item not in seen and len(item) < 28:
                    seen.add(item)
                    found.append(item)
            if len(found) >= 200:
                break
        except Exception:
            continue
    random.shuffle(found)
    return found


def probe_proxy(proxy, timeout=7):
    try:
        handler = urllib.request.ProxyHandler({"http": "http://" + proxy, "https": "http://" + proxy})
        opener = urllib.request.build_opener(handler)
        opener.addheaders = list(UA.items())
        with opener.open("https://api.ipify.org?format=json", timeout=timeout) as r:
            ip = json.loads(r.read().decode("utf-8", "ignore")).get("ip", "")
        return (True, proxy, ip) if ip else (False, proxy, "")
    except Exception:
        return (False, proxy, "")


def find_working_proxy(candidates, workers=24):
    """Uji banyak proxy paralel, kembalikan yang pertama berhasil."""
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = [pool.submit(probe_proxy, p) for p in candidates]
    try:
        for fut in as_completed(futures, timeout=90):
            try:
                ok, proxy, ip = fut.result()
            except Exception:
                continue
            if ok:
                for f in futures:
                    f.cancel()
                return proxy, ip
    except FutureTimeout:
        pass
    finally:
        pool.shutdown(wait=False)
    return None, ""


def _notify(cb, done, total, t0):
    try:
        dt = max(time.time() - t0, 0.05)
        cb(done * 8.0 / dt / 1_000_000.0, done, total)
    except Exception:
        pass


def measure_download(size=15_000_000, timeout=25, on_tick=None):
    urls = [
        "https://speed.cloudflare.com/__down?bytes=%d" % size,
        "https://proof.ovh.net/files/10Mb.dat",
        "http://ipv4.download.thinkbroadband.com/10MB.zip",
    ]
    for url in urls:
        t0, total = time.time(), 0
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                while True:
                    chunk = r.read(512 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    _notify(on_tick, total, size, t0)
                    if time.time() - t0 > timeout:
                        break
            dt = time.time() - t0
            if total > 200_000 and dt > 0.4:
                return total * 8.0 / dt / 1_000_000.0
        except Exception:
            continue
    return -1.0


def measure_upload(size=5_000_000, timeout=15, on_tick=None):
    try:
        conn = http.client.HTTPSConnection("speed.cloudflare.com", timeout=timeout)
        conn.putrequest("POST", "/__up")
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(size))
        conn.putheader("User-Agent", UA["User-Agent"])
        conn.endheaders()
        block = os.urandom(512 * 1024)
        sent, t0 = 0, time.time()
        while sent < size:
            n = min(len(block), size - sent)
            conn.send(block[:n])
            sent += n
            _notify(on_tick, sent, size, t0)
            if time.time() - t0 > timeout:
                break
        try:
            conn.getresponse().read()
            conn.close()
        except Exception:
            pass
        dt = time.time() - t0
        if dt > 0.4:
            return sent * 8.0 / dt / 1_000_000.0
    except Exception:
        pass
    return -1.0


def notify_system_proxy_change():
    try:
        ctypes.windll.wininet.InternetSetOptionW(None, 39, None, 0)
        ctypes.windll.wininet.InternetSetOptionW(None, 37, None, 0)
    except Exception:
        pass


def set_system_proxy(proxy):
    if not HAS_WINREG:
        return False, "hanya bisa di Windows"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, proxy)
        winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "localhost;127.*;10.*;192.168.*;*.local")
        winreg.CloseKey(key)
        notify_system_proxy_change()
        return True, proxy
    except Exception as e:
        return False, str(e)


def clear_system_proxy():
    if not HAS_WINREG:
        return False, "hanya bisa di Windows"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        winreg.CloseKey(key)
        notify_system_proxy_change()
        return True, ""
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------

def nice_max(v):
    """Skala meter: kelipatan 50 di atas 50 (mis. 101 -> 150, 151 -> 200)."""
    try:
        v = float(v)
    except Exception:
        return 100
    if v <= 0:
        return 100
    for c in (5, 10, 25, 50):
        if v <= c:
            return c
    return int(math.ceil(v / 50.0) * 50)


def polar(deg, r):
    """Tk canvas: 0 derajat di kanan, positif berlawanan arah jam di layar."""
    rad = math.radians(deg)
    return CX + r * math.cos(rad), CY - r * math.sin(rad)


# ---------------------------------------------------------------------------
# Aplikasi
# ---------------------------------------------------------------------------

class WrapIP(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("WrapIP")
        self.overrideredirect(True)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        try:
            self.attributes("-transparentcolor", KEY)
        except Exception:
            pass
        try:
            self.configure(fg_color=KEY)
        except Exception:
            pass
        self.geometry("%dx%d+40+40" % (SIZE, SIZE))

        # state
        self.phase = "boot"          # boot | ping | down | up | ready | proxy | err
        self.value = 0.0            # nilai yang ditampilkan (smooth)
        self.target = 0.0           # nilai live dari pengukuran
        self.vmax = 100.0
        self.ping = -1
        self.down = None
        self.up = None
        self.ip = "-"
        self.geo = {"negara": "-", "kota": "-", "isp": "-"}
        self.connected = False
        self.proxy = ""
        self.proxies = []
        self.busy = False
        self.notice = ""
        self.hover = None
        self._drag = None
        self.k = 1.0                # skala ukuran (scroll mouse)
        self.k_target = 1.0
        self._rgn_after = None
        self._zoom_dirty = False
        self._prev = time.time()

        self.canvas = tk.Canvas(
            self, width=SIZE, height=SIZE, bd=0, highlightthickness=0, bg=KEY, cursor="hand2"
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<Button-3>", self.on_menu)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.on_wheel(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self.on_wheel(e, -1))
        self.bind("<Escape>", lambda e: self.quit())

        self._load_pos()
        self.update_idletasks()
        self.after(60, self._round_region)
        self.after(40, self._tick)
        self.after(250, self._auto_start)

    # -- skala ukuran ------------------------------------------------
    def _fs(self, size):
        """Ukuran font mengikuti skala (canvas.scale tidak mengubah font)."""
        return max(1, int(round(size * self.k)))

    def _lw(self, px):
        """Lebar garis mengikuti skala."""
        return max(1, int(round(px * self.k)))

    def _win_size(self):
        return int(round(SIZE * self.k))

    def on_wheel(self, e, direction=None):
        # mouse resolusi tinggi mengirim banyak delta kecil: akumulasi dulu
        # 120 unit = 1 langkah (standar Windows)
        if direction is not None:
            steps = direction
        else:
            self._wheel_acc = getattr(self, "_wheel_acc", 0) + getattr(e, "delta", 0)
            steps = int(self._wheel_acc / 120)
            if steps == 0:
                return
            self._wheel_acc -= steps * 120
        kt = round(self.k_target + steps * K_STEP, 3)
        self.k_target = max(K_MIN, min(K_MAX, kt))

    def _zoom_tick(self, dt):
        # ukuran mengejar target dengan halus (tidak ada geometry thrash).
        # langkah dibatasi agar konten vs jendela tidak pernah selisih jauh.
        if abs(self.k_target - self.k) < 0.0008:
            if self._zoom_dirty:
                self._zoom_dirty = False
                self._save_pos()
            return
        step = (self.k_target - self.k) * min(1.0, dt * 9.0)
        if step > 0.045:
            step = 0.045
        elif step < -0.045:
            step = -0.045
        self.k += step
        if abs(self.k_target - self.k) < 0.0025:
            self.k = self.k_target
        self._zoom_dirty = True
        self._apply_size(self._win_size())

    def _apply_size(self, n=None):
        n = self._win_size() if n is None else n
        x, y = self.winfo_x(), self.winfo_y()
        if x < 0:
            x = 0
        if y < 0:
            y = 0
        # jangan sampai keluar layar saat membesar
        try:
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            x = min(x, max(0, sw - n))
            y = min(y, max(0, sh - n))
        except Exception:
            pass
        self.geometry("%dx%d+%d+%d" % (n, n, x, y))
        # region bulat mengikuti ukuran yang DIMINTA (bukan winfo yang lag),
        # di-debounce agar tidak balapan saat scroll deras
        try:
            if self._rgn_after is not None:
                self.after_cancel(self._rgn_after)
        except Exception:
            pass
        try:
            self._rgn_after = self.after(150, lambda nn=n: self._round_region(nn))
        except Exception:
            pass

    # -- config posisi ----------------------------------------------
    def _load_pos(self):
        self.k = self.k_target = 1.0
        try:
            if os.path.exists(CONFIG_PATH):
                cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
                try:
                    self.k = max(K_MIN, min(K_MAX, float(cfg.get("k", 1.0))))
                except Exception:
                    pass
                self.k_target = self.k
                x, y = int(cfg.get("x", -1)), int(cfg.get("y", -1))
                n = self._win_size()
                sw = ctypes.windll.user32.GetSystemMetrics(0)
                sh = ctypes.windll.user32.GetSystemMetrics(1)
                if x >= 0 and y >= 0 and x + n <= sw and y + n <= sh:
                    self.geometry("%dx%d+%d+%d" % (n, n, x, y))
                    return
            n = self._win_size()
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            self.geometry("%dx%d+%d+%d" % (n, n, sw - n - 40, sh - n - 60))
        except Exception:
            pass

    def _save_pos(self):
        try:
            json.dump(
                {"x": self.winfo_x(), "y": self.winfo_y(), "k": round(self.k, 3)},
                open(CONFIG_PATH, "w", encoding="utf-8"),
                indent=2,
            )
        except Exception:
            pass

    def _round_region(self, n=None):
        try:
            self._rgn_after = None
            if n is None:
                n = max(self.winfo_width(), self.winfo_height())
            rgn = ctypes.windll.gdi32.CreateEllipticRgn(0, 0, n + 1, n + 1)
            ctypes.windll.user32.SetWindowRgn(self.winfo_id(), rgn, True)
        except Exception:
            pass

    # -- warna status -----------------------------------------------
    def _accent(self):
        if self.phase == "down":
            return C_DOWN
        if self.phase == "up":
            return C_UP
        if self.phase in ("ping", "boot"):
            return C_PING
        if self.phase in ("proxy", "err"):
            return C_WARN
        if self.connected:
            return C_DOWN
        return C_DOWN if (self.down or 0) > 0 else FAINT

    def _phase_text(self):
        if self.notice:
            return self.notice
        return {
            "boot": "MENCARI IP",
            "ping": "MENGUKUR PING",
            "down": "DOWNLOAD",
            "up": "UPLOAD",
            "proxy": "MENCARI PROXY",
            "err": "GAGAL",
        }.get(self.phase, "SIAP")

    # -- menggambar -------------------------------------------------
    def _round_rect(self, x0, y0, x1, y1, r, fill, border=None, bw=1.5):
        c = self.canvas
        if border:
            b = self._lw(bw)
            self._round_rect(x0 - b, y0 - b, x1 + b, y1 + b, r + b, fill=border)
        c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline="")
        c.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, outline="")
        for ox, oy in ((x0, y0), (x1 - 2 * r, y0), (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
            c.create_oval(ox, oy, ox + 2 * r, oy + 2 * r, fill=fill, outline="")

    def _band(self, deg0, deg1, color, r=None, w=None):
        """Pita busur sebagai polygon isi (ikut skala penuh, ujung bisa rounded).
        span bertanda: negatif = searah jarum jam (mengikuti arah bar)."""
        r = R_GAUGE if r is None else r
        w = W_GAUGE if w is None else w
        ro, ri = r + w / 2.0, r - w / 2.0
        span = deg1 - deg0
        n = max(6, int(abs(span) / 4.0))
        pts = []
        for i in range(n + 1):
            x, y = polar(deg0 + span * i / n, ro)
            pts.extend((x, y))
        for i in range(n, -1, -1):
            x, y = polar(deg0 + span * i / n, ri)
            pts.extend((x, y))
        self.canvas.create_polygon(pts, fill=color, outline="")

    def _cap(self, deg, color, r=None, w=None):
        r = R_GAUGE if r is None else r
        w = W_GAUGE if w is None else w
        x, y = polar(deg, r)
        self.canvas.create_oval(x - w / 2.0, y - w / 2.0, x + w / 2.0, y + w / 2.0,
                                fill=color, outline="")

    def render(self):
        c = self.canvas
        c.delete("all")
        acc = self._accent()

        # badan lingkaran
        c.create_oval(
            CX - R_BODY, CY - R_BODY, CX + R_BODY, CY + R_BODY,
            fill=BODY, outline=EDGE, width=self._lw(1)
        )
        c.create_oval(
            CX - R_EDGE, CY - R_EDGE, CX + R_EDGE, CY + R_EDGE, outline=EDGE2, width=self._lw(2)
        )
        c.create_oval(
            CX - R_EDGE + 6, CY - R_EDGE + 6, CX + R_EDGE - 6, CY + R_EDGE - 6,
            outline="#1A2029", width=self._lw(1)
        )

        # tick meter analog
        n_ticks = 24
        cur = self.value / self.vmax if self.vmax > 0 else 0.0
        for i in range(n_ticks + 1):
            deg = A_START + (i / float(n_ticks)) * A_SWEEP
            major = (i % 4 == 0)
            r1 = Y_TICK_MIN
            r2 = r1 + (7 if major else 3.5)
            x1, y1 = polar(deg, r1)
            x2, y2 = polar(deg, r2)
            frac = i / float(n_ticks)
            col = EDGE2 if not major else DIM
            if self.value > 0:
                col = acc if frac <= cur else (DIM if major else EDGE2)
            c.create_line(x1, y1, x2, y2, fill=col, width=self._lw(2 if major else 1))

        # track busur (ujung rata)
        self._band(A_START, A_START + A_SWEEP, TRACK)

        # bar dari angka 0, searah jarum jam, ujung rounded
        frac = max(0.0, min(1.0, cur))
        if frac > 0.002:
            end = A_START + frac * A_SWEEP
            self._band(A_START, end, acc)
            self._cap(A_START, acc)
            self._cap(end, acc)
            # highlight tipis di dalam bar (ujung rounded juga, biar tidak ada-notch)
            hw = W_GAUGE - 8
            lo = min(end, A_START) + 2.2
            hb = max(end, A_START) - 2.2
            if hb - lo > 1.0:
                hi = self._lighten(acc, 0.22)
                self._band(lo, hb, hi, w=hw)
                self._cap(lo, hi, w=hw)
                self._cap(hb, hi, w=hw)

        # skala: 0 dan max tepat di ujung bar
        lx, ly = polar(A_START, Y_LABEL)
        rx, ry = polar(A_START + A_SWEEP, Y_LABEL)
        c.create_text(lx, ly, text="0", font=(FONT, self._fs(10)), fill=FAINT)
        c.create_text(rx, ry, text="%d" % int(self.vmax), font=(FONT, self._fs(10)), fill=FAINT)

        # nilai besar
        if self.phase in ("boot", "ping", "down", "up", "proxy", "err"):
            big = "--" if self.value <= 0 else "%.1f" % self.value
        else:
            best = max([v for v in (self.down, self.up) if v] or [0])
            big = "%.1f" % best if best > 0 else "--"
        c.create_text(CX, Y_VALUE, text=big, font=(FONT, self._fs(34), "bold"), fill=TXT)
        c.create_text(CX, Y_UNIT, text="Mbps", font=(FONT, self._fs(11)), fill=DIM)
        c.create_text(CX, Y_PHASE, text=self._phase_text(), font=(FONT, self._fs(8), "bold"),
                      fill=acc if self.phase != "ready" else DIM)

        # garis pemisah
        c.create_line(CX - 56, Y_RULE, CX + 56, Y_RULE, fill=EDGE, width=self._lw(1))

        # ip + ping
        ip_txt = self.ip if len(self.ip) <= 15 else "..." + self.ip[-12:]
        c.create_text(CX, Y_IP, text="IP %s" % ip_txt,
                      font=(FONT_MONO, self._fs(9), "bold"), fill=TXT)
        ping_txt = "%d ms" % self.ping if self.ping >= 0 else "-- ms"
        c.create_text(CX, Y_GEO, text="%s  \u00b7  %s" % (ping_txt, self.geo.get("kota", "-")),
                      font=(FONT, self._fs(9)), fill=DIM)

        # tombol kecil
        self._small_button(SMALL_L, "\u21bb", "refresh", FAINT)
        self._small_button(SMALL_R_POS, "\u2715", "close", C_WARN)

        # card connect / disconnect
        x0, x1 = CX - CARD_W / 2, CX + CARD_W / 2
        y0, y1 = CARD_Y0, CARD_Y0 + CARD_H
        if self.busy:
            fill, border, title, sub = "#161B22", "#2A323D", "MEMUAI...", "please wait"
        elif self.connected:
            fill, border = "#1C1418", "#40222A"
            title, sub = "DISCONNECT", "via " + (self.proxy or "proxy")
        else:
            hot = self.hover == "main"
            fill = "#122019" if hot else "#0D1613"
            border = "#2E5A4C" if hot else "#22322C"
            title, sub = "CONNECT", self._status_line()
        self._round_rect(x0, y0, x1, y1, CARD_R, fill, border, 1.5)
        c.create_text(CX, y0 + 15, text=title, font=(FONT, self._fs(12), "bold"),
                      fill=(DIM if self.busy else (C_WARN if self.connected else C_DOWN)))
        c.create_text(CX, y0 + 30, text=sub[:22], font=(FONT, self._fs(8)),
                      fill=DIM if not self.busy else FAINT)

        # semua geometry ikut ukuran widget (canvas.scale tidak mengubah font/line width)
        c.scale("all", 0, 0, self.k, self.k)

    def _status_line(self):
        if self.notice:
            return self.notice
        if self.phase == "proxy":
            return "mencari proxy..."
        return "ganti IP publik"

    def _small_button(self, pos, glyph, tag, col):
        x, y = pos
        hot = self.hover == tag
        r = SMALL_R + (2 if hot else 0)
        self.canvas.create_oval(x - r, y - r, x + r, y + r,
                                fill=col if hot else BODY, outline=col, width=self._lw(1))
        self.canvas.create_text(x, y, text=glyph, font=(FONT, self._fs(11), "bold"),
                                fill=BODY if hot else col)

    @staticmethod
    def _lighten(hex_color, f=0.35):
        try:
            h = hex_color.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            r = int(r + (255 - r) * f)
            g = int(g + (255 - g) * f)
            b = int(b + (255 - b) * f)
            return "#%02X%02X%02X" % (r, g, b)
        except Exception:
            return hex_color

    # -- event mouse -------------------------------------------------
    def _hit(self, x, y):
        # koordinat event mengikuti ukuran widget, kembalikan ke skala dasar
        x, y = x / self.k, y / self.k
        if math.hypot(x - CX, y - CY) > R_BODY:
            return None
        if math.hypot(x - SMALL_L[0], y - SMALL_L[1]) <= SMALL_R + 5:
            return "refresh"
        if math.hypot(x - SMALL_R_POS[0], y - SMALL_R_POS[1]) <= SMALL_R + 5:
            return "close"
        if abs(x - CX) <= CARD_W / 2 and CARD_Y0 <= y <= CARD_Y0 + CARD_H:
            return "main"
        return None

    def _set_hover(self, tag):
        if tag != self.hover:
            self.hover = tag
            self.canvas.configure(cursor="hand2" if tag else "arrow")
            self.render()

    def on_motion(self, e):
        self._set_hover(self._hit(e.x, e.y))

    def on_press(self, e):
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())

    def on_drag(self, e):
        if not self._drag:
            return
        _, _, x0, y0 = self._drag
        self.geometry("%dx%d+%d+%d" % (SIZE, SIZE, x0 + e.x_root - self._drag[0],
                                       y0 + e.y_root - self._drag[1]))

    def on_release(self, e):
        self._drag = None
        hit = self._hit(e.x, e.y)
        if hit == "close":
            self.quit()
        elif hit == "refresh":
            self.start_speedtest()
        elif hit == "main":
            self.toggle_connection()

    def on_menu(self, e):
        m = tk.Menu(self, tearoff=0, bg=BODY, fg=TXT, activebackground=EDGE)
        m.add_command(label="Ulangi speedtest", command=self.start_speedtest)
        m.add_command(label="Perbarui IP", command=self.refresh_ip)
        m.add_separator()
        m.add_command(label="Keluar", command=self.quit)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def quit(self):
        self._save_pos()
        self.destroy()

    # -- loop animasi -----------------------------------------------
    def _tick(self):
        now = time.time()
        dt = max(0.008, min(now - self._prev, 0.06))
        self._prev = now

        if self.phase in ("boot", "ping", "proxy"):
            # denyut halus: 22% .. 52% skala
            pulse = 0.22 + 0.15 * (0.5 + 0.5 * math.sin(now * 3.4))
            self.value += (self.vmax * pulse - self.value) * min(1.0, dt * 7.0)
        else:
            # easing eksponensial, waktu-independent, turun lebih cepat
            tau = 0.085 if self.target < self.value else 0.16
            self.value += (self.target - self.value) * (1.0 - math.exp(-dt / tau))
            if abs(self.target - self.value) < 0.015:
                self.value = self.target

        self._zoom_tick(dt)  # update k + geometry DULU ...
        try:
            self.render()  # ... baru gambar pakai k terbaru
        except Exception:
            pass
        try:
            self.after(55, self._tick)
        except Exception:
            pass

    # -- helper: panggil fungsi di thread UI -------------------------
    def _ui(self, fn, *a):
        self.after(0, lambda: fn(*a))

    def _set_busy(self, busy):
        self.busy = busy
        self.render()

    def _auto_start(self):
        threading.Thread(target=self._job_ip_then_speed, daemon=True).start()

    # -- 1. IP + geo -----------------------------------------------
    def _job_ip_then_speed(self):
        ok = self._fetch_ip()
        if ok:
            self._ui(self.start_speedtest)
        else:
            self._ui(self._phase_err)

    def _fetch_ip(self):
        try:
            ip = get_public_ip()
            self.ping = ping_ms()
            self._ui(self._set_ip, ip)
            threading.Thread(target=self._job_geo, args=(ip,), daemon=True).start()
            return True
        except Exception:
            return False

    def _job_geo(self, ip):
        geo = get_geo(ip)
        self._ui(self._set_geo, geo)

    def refresh_ip(self):
        self.phase = "boot"
        self.notice = ""
        self._ui(self.render)
        threading.Thread(target=self._job_ip_only, daemon=True).start()

    def _job_ip_only(self):
        if not self._fetch_ip():
            self._ui(self._phase_err)

    def _set_ip(self, ip):
        self.ip = ip
        self.phase = "ready"
        self.notice = ""
        self.render()

    def _set_geo(self, geo):
        self.geo = geo
        self.render()

    def _phase_err(self):
        self.phase = "err"
        self.ip = "offline"
        self.notice = "TIDAK ADA KONEKSI"
        self._set_busy(False)

    # -- 2. speedtest -----------------------------------------------
    def start_speedtest(self):
        if self.busy:
            return
        self.notice = ""
        self._set_busy(True)
        self.down = None
        self.up = None
        self.vmax = 100.0
        self.target = 0.0
        self.value = 0.0
        threading.Thread(target=self._job_speed, daemon=True).start()

    def _job_speed(self):
        # ping
        self._ui(self._set_phase, "ping")
        ms = ping_ms()
        self.ping = ms

        # download
        self._ui(self._set_phase, "down")
        self._ui(self._set_scale, 100.0)
        down = measure_download(on_tick=lambda mbps, d, t: self._live(mbps))
        self.down = down if down > 0 else None

        # upload
        if down > 0:
            self._ui(self._set_phase, "up")
            self._ui(self._set_scale, 50.0)
            up = measure_upload(on_tick=lambda mbps, d, t: self._live(mbps))
            self.up = up if up > 0 else None

        best = max([v for v in (self.down, self.up) if v] or [0])
        self._ui(self._finish_speed, best)

    def _live(self, mbps):
        """Nilai live + skala ikut naik otomatis mengikuti kecepatan (kelipatan 50)."""
        if mbps > 0:
            self.target = mbps
            if mbps > self.vmax * 0.92:
                vmax = nice_max(mbps * 1.12)
                if vmax > self.vmax:
                    self.vmax = float(vmax)
        else:
            self.target = 0.0

    def _set_phase(self, phase):
        self.phase = phase
        self.target = 0.0
        self.render()

    def _set_scale(self, v):
        self.vmax = nice_max(v)
        self.render()

    def _finish_speed(self, best):
        self.vmax = nice_max(best if best > 0 else 100)
        self.target = best
        self.phase = "ready"
        self._set_busy(False)

    # -- 3. connect / disconnect ------------------------------------
    def toggle_connection(self):
        if self.busy:
            return
        self.notice = ""
        if self.connected:
            self._set_busy(True)
            threading.Thread(target=self._job_disconnect, daemon=True).start()
        else:
            self._set_busy(True)
            self.phase = "proxy"
            self.render()
            threading.Thread(target=self._job_connect, daemon=True).start()

    def _job_connect(self):
        refresh_urllib()
        if not self.proxies:
            self.proxies = fetch_proxy_list()
        cands = self.proxies[:120]
        if not cands:
            self._ui(self._proxy_failed, "tidak ada proxy ditemukan")
            return
        proxy, ip_seen = find_working_proxy(cands)
        if not proxy:
            self._ui(self._proxy_failed, "semua proxy gagal")
            return
        ok, err = set_system_proxy(proxy)
        if not ok:
            self._ui(self._proxy_failed, err)
            return
        refresh_urllib()
        self.proxy = proxy
        self.connected = True
        self._ui(self._proxy_connected, proxy, ip_seen)

    def _job_disconnect(self):
        ok, err = clear_system_proxy()
        refresh_urllib()
        self.connected = False
        self.proxy = ""
        if not ok:
            self._ui(self._proxy_failed, err)
            return
        if not self._fetch_ip():
            self._ui(self._phase_err)
            return
        self._ui(self._proxy_disconnected)

    def _proxy_connected(self, proxy, ip_seen):
        self._set_busy(False)
        self.phase = "ready"
        self.ip = ip_seen or self.ip
        self.render()
        threading.Thread(target=self._job_recheck, daemon=True).start()

    def _job_recheck(self):
        self._fetch_ip()

    def _proxy_disconnected(self):
        self._set_busy(False)
        self.phase = "ready"
        self.render()

    def _proxy_failed(self, msg):
        self.notice = ("GAGAL " + str(msg))[:26].upper()
        self.phase = "err"
        self._set_busy(False)


def main():
    app = WrapIP()
    app.mainloop()


if __name__ == "__main__":
    main()