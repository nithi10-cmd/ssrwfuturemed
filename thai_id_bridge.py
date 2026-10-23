"""
RehabGlove Clinic: ตัวเปิดแอป + ตัวกลางอ่านบัตรประชาชนไทย
====================================================
เปิดแอป (Windows):  ดับเบิลคลิก RehabGlove.bat หรือ  python thai_id_bridge.py --open
  -> เปิดหน้าแอปที่ http://127.0.0.1:8765/ ในหน้าต่างของตัวเอง (Chrome/Edge โหมดแอป)

เบราว์เซอร์อ่านเครื่องอ่านสมาร์ทการ์ดตรงๆ ไม่ได้ (Chrome บล็อกอุปกรณ์ประเภทนี้)
โปรแกรมนี้จึงทำหน้าที่อ่านบัตรผ่าน PC/SC แล้วเปิดให้เว็บแอปดึงข้อมูลทาง
http://127.0.0.1:8765 (ใช้ได้เฉพาะเครื่องนี้เท่านั้น)

ไม่ต้องติดตั้งไลบรารีเพิ่ม ใช้ไดรเวอร์สมาร์ทการ์ดที่มากับระบบโดยตรง
  Windows: winscard.dll  |  Linux: libpcsclite (ต้องมี pcscd)  |  macOS: PCSC.framework
รัน:       python thai_id_bridge.py
ทดสอบ (ไม่มีเครื่องอ่าน):  python thai_id_bridge.py --mock

ถ้าเปิดเว็บแอปจากโดเมนอื่น (เช่น GitHub Pages) ให้อนุญาตเพิ่ม:
           python thai_id_bridge.py --allow-origin https://yourname.github.io

API
  GET /card          -> {"bridge": true, "reader": "...", "present": bool, "seq": n, "data": {...}, "error": "..."}
  GET /card?force=1  -> อ่านบัตรที่เสียบอยู่ใหม่อีกครั้ง

ข้อมูลที่อ่าน: เลขบัตร, คำนำหน้า/ชื่อ/นามสกุล (ไทย+อังกฤษ), วันเกิด, เพศ, ที่อยู่,
             วันออกบัตร/หมดอายุ (ไม่อ่านรูปถ่าย)
ข้อมูลไม่ถูกบันทึกลงดิสก์ และจะถูกล้างทันทีเมื่อดึงบัตรออก
"""

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = 8765
# ไฟล์หน้าแอปอยู่ในโฟลเดอร์ web (ถ้าแพ็กเป็น .exe ด้วย PyInstaller จะอยู่ใน sys._MEIPASS)
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB_DIR = BASE_DIR / "web"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".webmanifest": "application/manifest+json", ".json": "application/json",
    ".png": "image/png", ".gz": "application/gzip", ".wasm": "application/wasm", ".jpg": "image/jpeg", ".ico": "image/x-icon", ".svg": "image/svg+xml", ".css": "text/css; charset=utf-8",
}

# ---------- APDU ของบัตรประชาชนไทย ----------
SELECT_THAI_ID = [0x00, 0xA4, 0x04, 0x00, 0x08, 0xA0, 0x00, 0x00, 0x00, 0x54, 0x48, 0x00, 0x01]
FIELDS = {
    "cid":        [0x80, 0xB0, 0x00, 0x04, 0x02, 0x00, 0x0D],
    "name_th":    [0x80, 0xB0, 0x00, 0x11, 0x02, 0x00, 0x64],
    "name_en":    [0x80, 0xB0, 0x00, 0x75, 0x02, 0x00, 0x64],
    "dob":        [0x80, 0xB0, 0x00, 0xD9, 0x02, 0x00, 0x08],
    "gender":     [0x80, 0xB0, 0x00, 0xE1, 0x02, 0x00, 0x01],
    "issue_date": [0x80, 0xB0, 0x01, 0x67, 0x02, 0x00, 0x08],
    "expire":     [0x80, 0xB0, 0x01, 0x6F, 0x02, 0x00, 0x08],
    "address":    [0x80, 0xB0, 0x15, 0x79, 0x02, 0x00, 0x64],
}

MOCK_DATA = {
    "cid": "1101700203450", "prefix": "นาย", "firstName": "สมชาย", "lastName": "ใจดี",
    "nameEn": "Mr. Somchai Jaidee", "dob": "1965-03-14", "gender": "M",
    "address": "99/1 หมู่ที่ 2 ตำบลศาลายา อำเภอพุทธมณฑล จังหวัดนครปฐม",
    "issueDate": "2020-01-10", "expireDate": "2029-03-13",
}


def thai_date(raw):
    """'25080314' (พ.ศ.) -> '1965-03-14' (ค.ศ.); ถ้าไม่มีวัน/เดือนจะใช้ 01"""
    raw = raw.strip()
    if not re.fullmatch(r"\d{8}", raw):
        return ""
    y, m, d = int(raw[:4]) - 543, raw[4:6], raw[6:8]
    m = m if m != "00" else "01"
    d = d if d != "00" else "01"
    return f"{y:04d}-{m}-{d}"


def split_name(raw):
    """'นาย#สมชาย##ใจดี' -> (คำนำหน้า, ชื่อ, นามสกุล)"""
    parts = raw.split("#")
    parts += [""] * (4 - len(parts))
    prefix, first, middle, last = (p.strip() for p in parts[:4])
    if middle:
        first = f"{first} {middle}"
    return prefix, first, last


# ---------- PC/SC ผ่าน ctypes (แทน pyscard ซึ่งต้องคอมไพล์บน Python รุ่นใหม่) ----------
SCARD_SCOPE_USER = 0
SCARD_SHARE_SHARED = 2
SCARD_PROTOCOL_T0_T1 = 3
SCARD_LEAVE_CARD = 0

SCARD_E_NO_SMARTCARD = 0x8010000C
SCARD_E_SHARING_VIOLATION = 0x8010000B
SCARD_E_NO_SERVICE = 0x8010001D
SCARD_E_SERVICE_STOPPED = 0x8010001E
SCARD_E_NO_READERS_AVAILABLE = 0x8010002E
SCARD_E_INVALID_HANDLE = 0x80100003
SCARD_E_READER_UNAVAILABLE = 0x80100017
SCARD_E_UNKNOWN_READER = 0x80100009
SCARD_W_UNRESPONSIVE_CARD = 0x80100066
SCARD_W_UNPOWERED_CARD = 0x80100067
SCARD_W_REMOVED_CARD = 0x80100069

NO_CARD = {SCARD_E_NO_SMARTCARD, SCARD_W_REMOVED_CARD, SCARD_W_UNRESPONSIVE_CARD, SCARD_W_UNPOWERED_CARD}
CONTEXT_LOST = {SCARD_E_NO_SERVICE, SCARD_E_SERVICE_STOPPED, SCARD_E_INVALID_HANDLE}
READER_GONE = {SCARD_E_READER_UNAVAILABLE, SCARD_E_UNKNOWN_READER}


class PcscError(Exception):
    def __init__(self, code, where):
        self.code = code
        super().__init__(f"{where} ล้มเหลว (0x{code:08X})")


class Pcsc:
    """เรียกไลบรารี PC/SC ของระบบปฏิบัติการโดยตรง"""

    def __init__(self):
        if sys.platform == "win32":
            lib = ctypes.WinDLL("winscard")
            DWORD, LONG, HANDLE = ctypes.c_uint32, ctypes.c_int32, ctypes.c_size_t
            self.wide = True
            list_readers, connect, status = lib.SCardListReadersW, lib.SCardConnectW, lib.SCardStatusW
            name_t = ctypes.c_wchar_p
        else:
            if sys.platform == "darwin":
                lib = ctypes.CDLL("/System/Library/Frameworks/PCSC.framework/PCSC")
                DWORD, LONG, HANDLE = ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32
            else:
                lib = ctypes.CDLL("libpcsclite.so.1")
                DWORD, LONG, HANDLE = ctypes.c_ulong, ctypes.c_long, ctypes.c_long
            self.wide = False
            list_readers, connect, status = lib.SCardListReaders, lib.SCardConnect, lib.SCardStatus
            name_t = ctypes.c_char_p

        class IoRequest(ctypes.Structure):
            _fields_ = [("dwProtocol", DWORD), ("cbPciLength", DWORD)]

        self.DWORD, self.HANDLE, self.IoRequest = DWORD, HANDLE, IoRequest
        pD, pH = ctypes.POINTER(DWORD), ctypes.POINTER(HANDLE)

        def bind(fn, *args):
            fn.argtypes, fn.restype = list(args), LONG
            return fn

        self._establish = bind(lib.SCardEstablishContext, DWORD, ctypes.c_void_p, ctypes.c_void_p, pH)
        self._release = bind(lib.SCardReleaseContext, HANDLE)
        self._list = bind(list_readers, HANDLE, ctypes.c_void_p, ctypes.c_void_p, pD)
        self._connect = bind(connect, HANDLE, name_t, DWORD, DWORD, pH, pD)
        self._disconnect = bind(lib.SCardDisconnect, HANDLE, DWORD)
        self._transmit = bind(lib.SCardTransmit, HANDLE, ctypes.POINTER(IoRequest), ctypes.c_void_p, DWORD,
                              ctypes.c_void_p, ctypes.c_void_p, pD)
        self._status = bind(status, HANDLE, ctypes.c_void_p, pD, pD, pD, ctypes.c_void_p, pD)
        self.ctx = None

    @staticmethod
    def _check(rv, where):
        rv &= 0xFFFFFFFF
        if rv != 0:
            raise PcscError(rv, where)

    def open(self):
        if self.ctx is None:
            h = self.HANDLE()
            self._check(self._establish(SCARD_SCOPE_USER, None, None, ctypes.byref(h)), "SCardEstablishContext")
            self.ctx = h

    def close(self):
        if self.ctx is not None:
            self._release(self.ctx)
            self.ctx = None

    def readers(self):
        n = self.DWORD(0)
        rv = self._list(self.ctx, None, None, ctypes.byref(n)) & 0xFFFFFFFF
        if rv == SCARD_E_NO_READERS_AVAILABLE:
            return []
        self._check(rv, "SCardListReaders")
        if self.wide:
            buf = ctypes.create_unicode_buffer(n.value)
            self._check(self._list(self.ctx, None, buf, ctypes.byref(n)), "SCardListReaders")
            raw = buf[:n.value]
        else:
            buf = ctypes.create_string_buffer(n.value)
            self._check(self._list(self.ctx, None, buf, ctypes.byref(n)), "SCardListReaders")
            raw = buf.raw[:n.value].decode("utf-8", errors="replace")
        return [r for r in raw.split("\0") if r]

    def connect(self, reader):
        h, proto = self.HANDLE(), self.DWORD()
        name = reader if self.wide else reader.encode("utf-8")
        self._check(self._connect(self.ctx, name, SCARD_SHARE_SHARED, SCARD_PROTOCOL_T0_T1,
                                  ctypes.byref(h), ctypes.byref(proto)), "SCardConnect")
        return Card(self, h, proto.value)


class Card:
    def __init__(self, pcsc, handle, protocol):
        self.p, self.h = pcsc, handle
        self.pci = pcsc.IoRequest(protocol, ctypes.sizeof(pcsc.IoRequest))

    def getATR(self):
        p = self.p
        name = (ctypes.create_unicode_buffer if p.wide else ctypes.create_string_buffer)(512)
        name_len, state, proto = p.DWORD(512), p.DWORD(), p.DWORD()
        atr, atr_len = ctypes.create_string_buffer(64), p.DWORD(64)
        p._check(p._status(self.h, name, ctypes.byref(name_len), ctypes.byref(state), ctypes.byref(proto),
                           atr, ctypes.byref(atr_len)), "SCardStatus")
        return list(atr.raw[:atr_len.value])

    def transmit(self, apdu):
        p = self.p
        send = (ctypes.c_ubyte * len(apdu))(*apdu)
        recv, n = ctypes.create_string_buffer(258), p.DWORD(258)
        p._check(p._transmit(self.h, ctypes.byref(self.pci), send, len(apdu), None, recv, ctypes.byref(n)),
                 "SCardTransmit")
        data = list(recv.raw[:n.value])
        if len(data) < 2:
            raise IOError("บัตรตอบกลับไม่ครบ")
        return data[:-2], data[-2], data[-1]

    def disconnect(self):
        self.p._disconnect(self.h, SCARD_LEAVE_CARD)


PCSC_HINT = {
    SCARD_E_NO_SERVICE: "บริการ Smart Card ของระบบไม่ทำงาน (Windows: เปิด services.msc แล้วเริ่มบริการ Smart Card)",
    SCARD_E_SERVICE_STOPPED: "บริการ Smart Card หยุดทำงาน (มักเกิดเมื่อถอดเครื่องอ่านออก) กำลังลองใหม่",
    SCARD_E_SHARING_VIOLATION: "มีโปรแกรมอื่นใช้งานเครื่องอ่านบัตรอยู่ กรุณาปิดโปรแกรมนั้นก่อน",
}


class CardReader:
    def __init__(self, mock=False):
        self.mock = mock
        self.lock = threading.Lock()
        self.state = {"bridge": True, "reader": "", "present": False, "seq": 0, "data": None, "error": ""}
        self.force = False

    # ----- อ่านบัตร -----
    def _transmit(self, conn, apdu, get_resp):
        data, sw1, sw2 = conn.transmit(apdu)
        if sw1 == 0x61:
            data, sw1, sw2 = conn.transmit(get_resp + [sw2])
        if (sw1, sw2) != (0x90, 0x00):
            raise IOError(f"APDU ล้มเหลว SW={sw1:02X}{sw2:02X}")
        return bytes(data)

    def _read_card(self, conn):
        atr = conn.getATR()
        # บัตรรุ่นเก่า (ATR ขึ้นต้น 3B 67) ใช้ GET RESPONSE แบบ P2 = 01
        get_resp = [0x00, 0xC0, 0x00, 0x01] if atr[:2] == [0x3B, 0x67] else [0x00, 0xC0, 0x00, 0x00]
        self._transmit(conn, SELECT_THAI_ID, get_resp)

        raw = {}
        for key, apdu in FIELDS.items():
            raw[key] = self._transmit(conn, apdu, get_resp).decode("tis-620", errors="replace").strip()

        prefix, first, last = split_name(raw["name_th"])
        en_prefix, en_first, en_last = split_name(raw["name_en"])
        return {
            "cid": re.sub(r"\D", "", raw["cid"]),
            "prefix": prefix, "firstName": first, "lastName": last,
            "nameEn": " ".join(p for p in (en_prefix, en_first, en_last) if p),
            "dob": thai_date(raw["dob"]),
            "gender": {"1": "M", "2": "F"}.get(raw["gender"], ""),
            "address": re.sub(r"\s+", " ", raw["address"].replace("#", " ")).strip(),
            "issueDate": thai_date(raw["issue_date"]),
            "expireDate": thai_date(raw["expire"]),
        }

    def _update(self, **kw):
        with self.lock:
            changed_card = kw.get("data") is not None and kw["data"] != self.state["data"]
            self.state.update(kw)
            if changed_card or kw.get("bumpseq"):
                self.state["seq"] += 1
            self.state.pop("bumpseq", None)

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def request_reread(self):
        self.force = True

    # ----- เฝ้าดูการเสียบ/ถอดบัตร -----
    def run(self):
        if self.mock:
            self._update(reader="MOCK READER", present=True, data=MOCK_DATA, error="")
            while True:
                if self.force:
                    self.force = False
                    self._update(bumpseq=True)
                time.sleep(0.3)

        try:
            pcsc = Pcsc()
        except OSError as e:
            self._update(reader="", present=False, data=None, error=f"ไม่พบไลบรารีสมาร์ทการ์ดของระบบ: {e}")
            print("ไม่พบไลบรารีสมาร์ทการ์ดของระบบ:", e)
            return

        last_error = None
        while True:
            try:
                pcsc.open()
                rs = pcsc.readers()
                if not rs:
                    self._update(reader="", present=False, data=None, error="ไม่พบเครื่องอ่านบัตร")
                    time.sleep(1.5)
                    continue
                reader = rs[0]
                try:
                    card = pcsc.connect(reader)
                except PcscError as e:
                    if e.code in NO_CARD:
                        self._update(reader=reader, present=False, data=None, error="")
                        time.sleep(0.7)
                        continue
                    raise

                try:
                    snap = self.snapshot()
                    if not snap["present"] or snap["data"] is None or self.force:
                        self.force = False
                        try:
                            data = self._read_card(card)
                            self._update(reader=reader, present=True, data=data, error="", bumpseq=True)
                            print(f"อ่านบัตรสำเร็จ: {data['prefix']}{data['firstName']} {data['lastName']}")
                        except Exception as e:  # อ่านไม่สำเร็จ เช่น ดึงบัตรออกระหว่างอ่าน
                            if isinstance(e, PcscError) and e.code in NO_CARD:
                                self._update(reader=reader, present=False, data=None, error="")
                            else:
                                self._update(reader=reader, present=True, data=None, error=f"อ่านบัตรไม่สำเร็จ: {e}")
                                print("อ่านบัตรไม่สำเร็จ:", e)
                finally:
                    card.disconnect()
                last_error = None
                time.sleep(0.7)
            except PcscError as e:
                if e.code in CONTEXT_LOST or e.code in READER_GONE:
                    pcsc.close()  # เปิด context ใหม่รอบถัดไป
                msg = PCSC_HINT.get(e.code, str(e))
                self._update(reader="", present=False, data=None, error=msg)
                if msg != last_error:
                    print(msg)
                    last_error = msg
                time.sleep(1.5)
            except Exception as e:
                self._update(present=False, data=None, error=str(e))
                print("ข้อผิดพลาด:", e)
                time.sleep(2)

def make_handler(card, allowed_origins, port):
    def origin_ok(origin):
        if origin is None:
            return True  # เรียกตรงจาก curl / เปิดหน้าในเบราว์เซอร์
        if origin == "null":  # เปิดไฟล์ HTML จาก file://
            return True
        if re.fullmatch(r"https?://(localhost|127\.0\.0\.1)(:\d+)?", origin):
            return True
        return origin in allowed_origins

    # กัน DNS rebinding: เว็บอื่นที่ชี้ชื่อโดเมนมาที่ 127.0.0.1 จะส่ง Host เป็นชื่อโดเมนของมัน
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def _headers(self, code, origin, ctype="application/json; charset=utf-8", cache="no-store"):
            self.send_response(code)
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Type", ctype)
            self.end_headers()

        def do_OPTIONS(self):
            origin = self.headers.get("Origin")
            self._headers(204 if origin_ok(origin) else 403, origin if origin_ok(origin) else None)

        def do_GET(self):
            origin = self.headers.get("Origin")
            if self.headers.get("Host") not in allowed_hosts or not origin_ok(origin):
                self._headers(403, None)
                self.wfile.write(b'{"error":"forbidden"}')
                return
            url = urlparse(self.path)
            if url.path == "/card":
                if parse_qs(url.query).get("force") == ["1"]:
                    card.request_reread()
                    time.sleep(1.2)
                self._headers(200, origin)
                self.wfile.write(json.dumps(card.snapshot(), ensure_ascii=False).encode("utf-8"))
                return
            self._serve_static(url.path)

        def _serve_static(self, path):
            rel = path.lstrip("/") or "index.html"
            target = (WEB_DIR / rel).resolve()
            if WEB_DIR.resolve() not in target.parents or not target.is_file():
                self._headers(404, None)
                self.wfile.write(b'{"error":"not found"}')
                return
            cache = "no-cache" if target.suffix in (".html", ".js", ".webmanifest") else "max-age=86400"
            self._headers(200, None, CONTENT_TYPES.get(target.suffix, "application/octet-stream"), cache)
            self.wfile.write(target.read_bytes())

        def log_message(self, *args):
            pass  # ไม่พิมพ์ทุก request (เว็บถามทุก 1.5 วินาที)

    return Handler


def find_app_browser():
    """หา Chrome หรือ Edge สำหรับเปิดหน้าแอปแบบหน้าต่างของตัวเอง (ไม่มีแถบที่อยู่)"""
    if sys.platform == "win32":
        env = os.environ
        candidates = [
            Path(env.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
            Path(env.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
            Path(env.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
        return None
    if sys.platform == "darwin":
        for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
            if os.path.isfile(c):
                return c
        return None
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def open_app_window(url):
    browser = find_app_browser()
    if browser:
        subprocess.Popen([browser, f"--app={url}"])
    else:
        print("ไม่พบ Chrome หรือ Edge จะเปิดด้วยเบราว์เซอร์หลักแทน (การเชื่อมต่อถุงมือต้องใช้ Chrome หรือ Edge)")
        webbrowser.open(url)


def already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/card", timeout=1.5) as r:
            return json.load(r).get("bridge") is True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="RehabGlove Clinic: app launcher + Thai ID card bridge")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--open", action="store_true", help="เปิดหน้าแอปในหน้าต่างของตัวเอง")
    ap.add_argument("--mock", action="store_true", help="จำลองบัตร ไม่ต้องมีเครื่องอ่าน")
    ap.add_argument("--allow-origin", action="append", default=[], help="โดเมนเว็บแอปที่อนุญาตเพิ่ม")
    args = ap.parse_args()
    open_window = args.open or getattr(sys, "frozen", False)  # ไฟล์ .exe เปิดหน้าแอปให้เสมอ
    app_url = f"http://127.0.0.1:{args.port}/"

    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(card := CardReader(mock=args.mock),
                                                                            set(args.allow_origin), args.port))
    except OSError:
        if already_running(args.port):
            # เปิดโปรแกรมซ้ำ: แค่เปิดหน้าแอปอีกหน้าต่าง ใช้ตัวที่ทำงานอยู่แล้ว
            print("RehabGlove ทำงานอยู่แล้ว เปิดหน้าแอปให้ใหม่")
            if open_window:
                open_app_window(app_url)
            return
        print(f"พอร์ต {args.port} ถูกโปรแกรมอื่นใช้อยู่ ลองปิดโปรแกรมนั้น หรือรันด้วย --port 8766")
        return

    threading.Thread(target=card.run, daemon=True).start()
    print(f"RehabGlove Clinic ทำงานที่ {app_url}" + ("  (โหมดบัตรจำลอง)" if args.mock else ""))
    print(f"ตัวกลางอ่านบัตรประชาชน: {app_url}card")
    print("เปิดหน้าต่างนี้ทิ้งไว้ระหว่างใช้งาน ปิดหน้าต่างนี้ = ปิดโปรแกรม")
    if not WEB_DIR.is_dir():
        print(f"ไม่พบโฟลเดอร์หน้าแอป: {WEB_DIR}")
    if open_window:
        open_app_window(app_url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
