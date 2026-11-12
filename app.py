# -*- coding: utf-8 -*-
"""
Mini Reproductor Windows · versión PyQt6
========================================
Widget flotante que muestra y controla lo que suena en Windows (Spotify, YouTube,
navegador...) a través del SMTC del sistema. Misma funcionalidad que la v2 de
CustomTkinter, pero reconstruido sobre PyQt6.

Instalación (ya no hacen falta customtkinter, pillow ni pystray):
    pip install PyQt6 pycaw comtypes winrt-runtime winrt-Windows.Media.Control ^
        winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections

Escalado (DPI)
  · Todo se dibuja en píxeles lógicos: Qt los multiplica por el factor de cada monitor
    (100 %, 125 %, 150 %...), así que nada se ve borroso ni descolocado.
  · Iconos, botones, barras, portada y letras son vectoriales / se pintan a resolución nativa.
  · Posición y tamaño se validan contra los monitores conectados al arrancar.

Diseño
  · Los iconos de assets/btn_*.png se usan si existen; si no, se dibujan por código.
  · winrt y pycaw se importan dentro de sus hilos (evita el conflicto COM entre Qt y winrt).
  · Cualquier excepción inesperada se registra en %APPDATA%\\MiniWindowsMediaPlayer\\error.log
    en vez de cerrar la aplicación.

Controles
  · Arrastrar desde cualquier zona libre · esquina ◢ para redimensionar · doble clic en la
    portada = modo mini · rueda sobre la portada = volumen · clic en el altavoz = silenciar.
  · Clic o arrastre en la barra de progreso = saltar. Doble clic en una línea de la letra = saltar.
  · Atajos (ventana activa): Espacio, ← →, ↑ ↓, L (letra), M (mini).
"""
from __future__ import annotations

import asyncio
import bisect
import colorsys
import ctypes
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import winreg
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage,
    QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QShortcut,
    QKeySequence,
)
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QAbstractButton, QApplication, QHBoxLayout, QLabel, QLayout, QMenu, QSizeGrip,
    QSizePolicy, QSystemTrayIcon, QVBoxLayout, QWidget,
)

# ──────────────────────────────────────────────────────────────────────────────
#  Constantes
# ──────────────────────────────────────────────────────────────────────────────
APP_NAME = "MiniWindowsMediaPlayer"
APP_TITLE = "Mini Reproductor Windows"
USER_AGENT = "MiniWindowsMediaPlayer/3.0"
REG_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
SCHEMA = 2               # versión del formato de config (las posiciones de la v1/v2 no valen)

POLL_INTERVAL = 0.4      # s entre consultas a Windows (la posición se interpola entre medias)
UI_TICK_MS = 100         # refresco de la interfaz
LYRICS_OFFSET = 0.3      # s: la línea se resalta un poco antes de cantarse
VOLUME_POLL_S = 2.0      # cada cuánto se lee el volumen real de la app

SCRIPT_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
DATA_DIR = os.path.join(os.environ.get("APPDATA", SCRIPT_DIR), APP_NAME)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LEGACY_CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")   # la de la v1
LYRICS_DIR = os.path.join(DATA_DIR, "lyrics")
LOG_FILE = os.path.join(DATA_DIR, "error.log")

DEFAULT_ACCENT = (29, 185, 84)     # verde
DEFAULT_BG = (26, 26, 26)


def log_exception(exc_type, exc, tb):
    """Registra la excepción en un archivo (y en consola si existe) sin cerrar la app."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}")
    except Exception:
        pass
    try:
        sys.stderr.write(text)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
#  Utilidades puras (color, tiempo)
# ──────────────────────────────────────────────────────────────────────────────
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def mix(c1, c2, t):
    return tuple(a + (b - a) * t for a, b in zip(c1, c2))


def scale_rgb(c, k):
    return tuple(v * k for v in c)


def rgb_to_hex(c):
    r, g, b = (int(round(clamp(v, 0, 255))) for v in c[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def fmt_time(seconds):
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def make_palette(accent, bg=None):
    """Paleta completa (tuplas RGB) a partir del color de acento."""
    white = (255, 255, 255)
    bg = bg if bg is not None else scale_rgb(accent, 0.11)
    return {
        "accent": tuple(accent),
        "accent_hover": mix(accent, white, 0.25),
        "bg": tuple(bg),
        "card": mix(bg, white, 0.10),
        "hover": mix(bg, white, 0.14),
        "track": mix(bg, white, 0.22),
        "lyrics_bg": scale_rgb(bg, 0.65),
    }


DEFAULT_PALETTE = make_palette(DEFAULT_ACCENT, bg=DEFAULT_BG)


def accent_from_pixels(pixels):
    """
    Color de acento a partir de una lista de píxeles (r, g, b): agrupa los colores en
    cubos, puntúa cada grupo por saturación, brillo y proporción, y devuelve el mejor
    ya aclarado lo justo para verse bien sobre un fondo oscuro. Si la imagen es casi
    gris, devuelve un tono claro y neutro.
    """
    n_total = len(pixels) or 1
    buckets = {}
    for r, g, b in pixels:
        key = (r >> 6, g >> 6, b >> 6)
        acc = buckets.get(key)
        if acc is None:
            buckets[key] = [r, g, b, 1]
        else:
            acc[0] += r
            acc[1] += g
            acc[2] += b
            acc[3] += 1

    best, best_score = None, -1.0
    for sr, sg, sb, n in buckets.values():
        r, g, b = sr / n, sg / n, sb / n
        _, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        share = n / n_total
        if s < 0.25 or v < 0.20 or share < 0.01:
            continue
        score = s * 1.5 + v + share * 1.2
        if score > best_score:
            best, best_score = (r, g, b), score

    if best is None:                       # portada gris / muy oscura
        r = sum(p[0] for p in pixels) / n_total
        g = sum(p[1] for p in pixels) / n_total
        b = sum(p[2] for p in pixels) / n_total
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        s, v = min(s, 0.25), max(v, 0.75)
    else:
        h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in best))
        v = max(v, 0.62)
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (r * 255, g * 255, b * 255)


def extract_accent_color(qimage):
    """Versión para QImage: reduce la portada a 40x40 y delega en accent_from_pixels."""
    small = qimage.scaled(40, 40, Qt.AspectRatioMode.IgnoreAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    pixels = []
    for y in range(small.height()):
        for x in range(small.width()):
            c = small.pixelColor(x, y)
            pixels.append((c.red(), c.green(), c.blue()))
    return accent_from_pixels(pixels)


def merge_artists(artist, album_artist):
    if artist and album_artist:
        return artist if album_artist.lower() in artist.lower() else f"{artist}, {album_artist}"
    return artist or album_artist or "Desconocido"



# ──────────────────────────────────────────────────────────────────────────────
#  Inicio automático con Windows
# ──────────────────────────────────────────────────────────────────────────────
def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")   # sin ventana de consola
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(sys.argv[0])}"'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        print(f"Error gestionando el registro de Windows: {e}")
        return False



# ──────────────────────────────────────────────────────────────────────────────
#  Configuración
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class Config:
    normal_w: int = 460
    normal_h: int = 180
    mini_w: int = 140
    mini_h: int = 160
    lyrics_height: int = 180
    is_mini: bool = False
    show_lyrics: bool = False
    is_topmost: bool = True
    opacity_val: float = 1.0
    win_x: Optional[int] = None
    win_y: Optional[int] = None
    schema: int = 0

    @classmethod
    def load(cls):
        cfg = cls()
        for path in (CONFIG_FILE, LEGACY_CONFIG_FILE):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for fld in fields(cfg):
                    if fld.name in data:
                        setattr(cfg, fld.name, data[fld.name])
                break
            except Exception as e:
                print(f"Error cargando la configuración: {e}")
        cfg = cfg.sanitized()
        if cfg.schema != SCHEMA:               # config de la versión Tk: posiciones en otras unidades
            cfg.win_x = cfg.win_y = None
            cfg.schema = SCHEMA
        return cfg

    def sanitized(self):
        try:
            self.normal_w = int(clamp(int(self.normal_w), 360, 1600))
            self.normal_h = int(clamp(int(self.normal_h), 176, 900))
            self.mini_w = int(clamp(int(self.mini_w), 120, 800))
            self.mini_h = int(clamp(int(self.mini_h), 150, 800))
            self.lyrics_height = int(clamp(int(self.lyrics_height), 100, 900))
            self.opacity_val = float(clamp(float(self.opacity_val), 0.3, 1.0))
            self.is_mini = bool(self.is_mini)
            self.show_lyrics = bool(self.show_lyrics)
            self.is_topmost = bool(self.is_topmost)
            self.win_x = None if self.win_x is None else int(self.win_x)
            self.win_y = None if self.win_y is None else int(self.win_y)
            self.schema = int(self.schema)
            return self
        except (TypeError, ValueError):
            return Config()                      # fichero corrupto: valores por defecto

    def save(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=4)
            os.replace(tmp, CONFIG_FILE)         # escritura atómica
        except Exception as e:
            print(f"Error guardando la configuración: {e}")


# ──────────────────────────────────────────────────────────────────────────────
#  Letras (LRCLIB)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class Lyrics:
    status: str = "idle"          # idle | ok | instrumental | notfound | error
    synced: list = field(default_factory=list)   # [(segundos, texto), ...]
    plain: str = ""


_TS_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_PAREN_NOISE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(?:feat|ft|with|remaster(?:ed)?|version|edit|mix|live|bonus|explicit|official|video|audio)\b[^\)\]]*[\)\]]",
    re.I,
)
_DASH_NOISE = re.compile(
    r"\s+-\s+.*\b(?:version|edit|mix|live|remix|remaster(?:ed)?)\b.*$", re.I
)


def parse_lrc(text):
    """'[01:23.45] texto' -> [(83.45, 'texto')], ordenado. Admite varias marcas por línea."""
    entries = []
    for raw in (text or "").splitlines():
        stamps = _TS_RE.findall(raw)
        if not stamps:
            continue
        line = _TS_RE.sub("", raw).strip()
        for m, s in stamps:
            entries.append((int(m) * 60 + float(s), line))
    entries.sort(key=lambda e: e[0])
    return entries


def clean_title(title):
    t = _PAREN_NOISE.sub("", title or "")
    t = _DASH_NOISE.sub("", t).strip()
    return t or (title or "").strip()


def first_artist(artist):
    parts = re.split(r"\s*(?:,|;|/|\bfeat\.?\b|\bft\.?\b)\s*", artist or "", maxsplit=1, flags=re.I)
    return parts[0].strip() or (artist or "").strip()


def pick_best_lyrics(results, duration):
    """Entre varios resultados, el de duración más parecida (con preferencia por los sincronizados)."""
    if not isinstance(results, list):
        return None
    best, best_score = None, None
    for r in results:
        if not isinstance(r, dict):
            continue
        if not (r.get("syncedLyrics") or r.get("plainLyrics") or r.get("instrumental")):
            continue
        d = r.get("duration")
        diff = abs(float(d) - duration) if (duration and d) else 0.0
        score = diff - (4.0 if r.get("syncedLyrics") else 0.0)
        if best is None or score < best_score:
            best, best_score = r, score
    return best


def lyrics_from_record(rec, duration):
    synced_raw = rec.get("syncedLyrics") or ""
    plain = (rec.get("plainLyrics") or "").strip()
    if rec.get("instrumental") and not (synced_raw or plain):
        return Lyrics(status="instrumental")
    d = rec.get("duration")
    mismatch = bool(duration and d and abs(float(d) - duration) > 10)   # otra versión del tema
    synced = [] if mismatch else parse_lrc(synced_raw)
    if not synced and not plain and synced_raw:
        plain = "\n".join(t for _, t in parse_lrc(synced_raw))
    if not synced and not plain:
        return Lyrics(status="notfound")
    return Lyrics(status="ok", synced=synced, plain=plain)


def http_json(url, timeout=6):
    """JSON de la URL; None si el servidor responde 4xx/5xx; lanza si no hay red."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return None


class LyricsProvider:
    API = "https://lrclib.net/api"

    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except OSError:
            pass

    def fetch(self, title, artist, album, duration):
        raw_key = f"{artist}|{title}|{album}|{int(duration)}".lower()
        path = os.path.join(self.cache_dir, hashlib.sha1(raw_key.encode("utf-8")).hexdigest() + ".json")
        record = self._read_cache(path)
        if record is None:
            try:
                record = self._lookup(title, artist, album, duration)
            except Exception:
                return Lyrics(status="error")
            if record is None:
                return Lyrics(status="notfound")
            self._write_cache(path, record)
        return lyrics_from_record(record, duration)

    def _lookup(self, title, artist, album, duration):
        clean = clean_title(title)
        main_artist = first_artist(artist)
        attempts = []
        if clean and artist and album and duration:                       # 1) firma exacta
            q = urllib.parse.urlencode({"track_name": clean, "artist_name": artist,
                                        "album_name": album, "duration": int(round(duration))})
            attempts.append(("get", f"{self.API}/get?{q}"))
        q = urllib.parse.urlencode({"track_name": clean, "artist_name": main_artist})
        attempts.append(("search", f"{self.API}/search?{q}"))              # 2) búsqueda estructurada
        q = urllib.parse.urlencode({"q": f"{main_artist} {clean}".strip()})
        attempts.append(("search", f"{self.API}/search?{q}"))              # 3) búsqueda libre

        for kind, url in attempts:
            data = http_json(url)
            if data is None:
                continue
            if kind == "get":
                if isinstance(data, dict) and (data.get("syncedLyrics") or data.get("plainLyrics")
                                               or data.get("instrumental")):
                    return data
            else:
                best = pick_best_lyrics(data, duration)
                if best:
                    return best
        return None

    @staticmethod
    def _read_cache(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def _write_cache(path, record):
        try:
            keep = {k: record.get(k) for k in ("syncedLyrics", "plainLyrics", "instrumental", "duration")}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(keep, f)
        except Exception:
            pass



# ──────────────────────────────────────────────────────────────────────────────
#  Backend multimedia (SMTC) — hilo propio con su bucle asyncio
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class MediaSnapshot:
    has_session: bool = False
    track_id: str = ""
    title: str = ""
    artist: str = ""            # para mostrar
    lyrics_artist: str = ""     # artista "limpio" para buscar letras
    album: str = ""
    app_id: str = ""
    position: float = 0.0
    duration: float = 0.0
    is_playing: bool = False
    sampled_at: float = 0.0     # time.monotonic() del momento de la muestra
    cover_bytes: Optional[bytes] = None     # imagen sin decodificar, solo cuando llega una nueva
    cover_failed: bool = False              # se agotaron los reintentos sin portada


class MediaBackend(threading.Thread):
    """
    Lee el estado multimedia de Windows. winrt se importa DENTRO del hilo: si se importara
    en el hilo principal inicializaría COM en modo MTA y chocaría con el OleInitialize de Qt.
    """

    def __init__(self, out_queue):
        super().__init__(daemon=True)
        self.out = out_queue
        self.running = True
        self.loop = None
        self._manager = None
        self._wake = None
        self._start_offset = 0.0
        self._cover_track = ""
        self._cover_ok = False
        self._cover_attempts = 0
        self._last_digest = None
        self._MediaManager = self._PlaybackStatus = self._DataReader = None

    # -- ciclo de vida -------------------------------------------------------
    def run(self):
        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as MediaManager,
                GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
            )
            from winrt.windows.storage.streams import DataReader
        except Exception as e:
            self.out.put(("error", f"No se pudo cargar winrt: {e}"))
            return
        self._MediaManager, self._PlaybackStatus, self._DataReader = MediaManager, PlaybackStatus, DataReader
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    def stop(self):
        self.running = False
        if self.loop and self._wake:
            try:
                self.loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                pass

    async def _main(self):
        self._wake = asyncio.Event()
        while self.running:
            try:
                self.out.put(("media", await self._poll()))
            except Exception:
                self._manager = None            # se vuelve a pedir en la siguiente vuelta
            try:
                await asyncio.wait_for(self._wake.wait(), POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _get_manager(self):
        if self._manager is None:
            self._manager = await self._MediaManager.request_async()
        return self._manager

    # -- órdenes -------------------------------------------------------------
    def send(self, cmd, arg=None):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self._command(cmd, arg), self.loop)

    async def _command(self, cmd, arg):
        try:
            session = (await self._get_manager()).get_current_session()
            if not session:
                return
            if cmd == "play_pause":
                await session.try_toggle_play_pause_async()
            elif cmd == "next":
                await session.try_skip_next_async()
            elif cmd == "prev":
                await session.try_skip_previous_async()
            elif cmd == "seek":
                ticks = int((float(arg) + self._start_offset) * 10_000_000)   # unidades de 100 ns
                await session.try_change_playback_position_async(ticks)
        except Exception:
            pass
        await asyncio.sleep(0.15)               # deja que Windows actualice y refresca ya
        if self._wake:
            self._wake.set()

    # -- lectura de estado ---------------------------------------------------
    async def _poll(self):
        session = (await self._get_manager()).get_current_session()
        if session is None:
            return MediaSnapshot(has_session=False, sampled_at=time.monotonic())

        app_id = session.source_app_user_model_id or ""
        props = await session.try_get_media_properties_async()
        timeline = session.get_timeline_properties()
        playback = session.get_playback_info()

        title = (props.title or "").strip() or "Sin título"
        artist_raw = (props.artist or "").strip()
        album_artist = (props.album_artist or "").strip()
        album = (getattr(props, "album_title", "") or "").strip()
        artist = merge_artists(artist_raw, album_artist)

        is_playing = bool(playback and playback.playback_status == self._PlaybackStatus.PLAYING)
        position = duration = 0.0
        if timeline:
            start = timeline.start_time.total_seconds()
            end = timeline.end_time.total_seconds()
            self._start_offset = start
            duration = max(0.0, end - start)
            if duration > 0:
                base = timeline.position.total_seconds() - start
                position = base
                if is_playing and timeline.last_updated_time:
                    try:
                        last = timeline.last_updated_time
                        now = datetime.now(timezone.utc) if last.tzinfo else datetime.now()
                        elapsed = (now - last).total_seconds()
                        if 0 <= elapsed < 3600:
                            position = base + elapsed
                    except Exception:
                        pass
                position = clamp(position, 0.0, duration)

        track_id = f"{title}|{artist}|{album}"
        cover, cover_failed = await self._maybe_load_cover(track_id, props)

        return MediaSnapshot(
            has_session=True, track_id=track_id, title=title, artist=artist,
            lyrics_artist=artist_raw or album_artist, album=album, app_id=app_id,
            position=position, duration=duration, is_playing=is_playing,
            sampled_at=time.monotonic(), cover_bytes=cover, cover_failed=cover_failed,
        )

    async def _maybe_load_cover(self, track_id, props):
        """La portada suele llegar unos instantes después del título: se reintenta."""
        if track_id != self._cover_track:
            self._cover_track, self._cover_ok, self._cover_attempts = track_id, False, 0
        if self._cover_ok or self._cover_attempts >= 25:      # ~10 s sin suerte: nos rendimos
            return None, False
        self._cover_attempts += 1

        if props.thumbnail:
            data, digest = await self._read_thumbnail(props.thumbnail)
            if data:
                # Si es idéntica a la canción anterior, puede ser la portada antigua
                # aún no actualizada: damos unos reintentos antes de aceptarla.
                if digest == self._last_digest and self._cover_attempts < 4:
                    return None, False
                self._cover_ok = True
                self._last_digest = digest
                return data, False
        return None, self._cover_attempts >= 25

    async def _read_thumbnail(self, ref):
        stream = None
        try:
            stream = await ref.open_read_async()
            size = stream.size
            if not size:
                return None, None
            reader = self._DataReader(stream)
            await reader.load_async(size)
            buf = bytearray(size)
            reader.read_bytes(buf)
            data = bytes(buf)
            return data, hashlib.md5(data).hexdigest()
        except Exception:
            return None, None
        finally:
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
#  Control de volumen por aplicación (pycaw) — hilo propio con COM inicializado
# ──────────────────────────────────────────────────────────────────────────────
class AudioController(threading.Thread):
    def __init__(self, out_queue):
        super().__init__(daemon=True)
        self.out = out_queue
        self.tasks = queue.Queue()
        self._AudioUtilities = None
        self._ISimpleAudioVolume = None

    def set_volume(self, app_id, value):
        self.tasks.put(("set", app_id, value))

    def request_volume(self, app_id):
        self.tasks.put(("get", app_id, None))

    def stop(self):
        self.tasks.put(("stop", None, None))

    def run(self):
        comtypes = None
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            comtypes = None
        try:
            from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
            self._AudioUtilities, self._ISimpleAudioVolume = AudioUtilities, ISimpleAudioVolume
        except Exception as e:
            self.out.put(("error", f"No se pudo cargar pycaw (volumen desactivado): {e}"))
            return

        while True:
            batch = [self.tasks.get()]
            while True:                                 # fusiona las peticiones acumuladas
                try:
                    batch.append(self.tasks.get_nowait())
                except queue.Empty:
                    break
            if any(t[0] == "stop" for t in batch):
                break
            last_set = last_get = None
            for task in batch:
                if task[0] == "set":
                    last_set = task
                elif task[0] == "get":
                    last_get = task
            try:
                if last_set:
                    self._set(last_set[1], last_set[2])
                if last_get:
                    vol = self._get(last_get[1])
                    if vol is not None:
                        self.out.put(("volume", vol))
            except Exception:
                pass

        if comtypes is not None:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    @staticmethod
    def _matches(session, app_id):
        app = (app_id or "").lower()
        if not app:
            return False
        try:
            proc = session.Process
            if not proc:
                return False
            name = proc.name().lower()
        except Exception:
            return False
        stem = name[:-4] if name.endswith(".exe") else name
        return bool(stem) and (stem in app or name in app or app.startswith(stem))

    def _iface(self, session):
        vol = getattr(session, "SimpleAudioVolume", None)
        return vol if vol is not None else session._ctl.QueryInterface(self._ISimpleAudioVolume)

    def _set(self, app_id, value):
        for session in self._AudioUtilities.GetAllSessions():
            if self._matches(session, app_id):
                self._iface(session).SetMasterVolume(float(clamp(value, 0.0, 1.0)), None)

    def _get(self, app_id):
        for session in self._AudioUtilities.GetAllSessions():
            if self._matches(session, app_id):
                return float(self._iface(session).GetMasterVolume())
        return None


# ──────────────────────────────────────────────────────────────────────────────
#  Widgets propios (todo se pinta en píxeles lógicos: Qt aplica el DPI de cada monitor)
# ──────────────────────────────────────────────────────────────────────────────
ALIGN_LEFT_VCENTER = Qt.AlignmentFlag.AlignLeft.value | Qt.AlignmentFlag.AlignVCenter.value
ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter.value


def ui_font(px, bold=False):
    f = QFont("Segoe UI")
    f.setPixelSize(int(px))
    f.setBold(bold)
    return f


def find_asset(rel_path):
    for base in (getattr(sys, "_MEIPASS", None), SCRIPT_DIR, os.path.abspath(".")):
        if base:
            full = os.path.join(base, rel_path)
            if os.path.exists(full):
                return full
    return None


_ASSET_PIXMAPS = {}


def asset_pixmap(kind):
    """assets/btn_<kind>.png si existe (la carpeta de la v1); si no, None y se dibuja el icono."""
    if kind not in _ASSET_PIXMAPS:
        path = find_asset(f"assets/btn_{kind}.png")
        pm = QPixmap(path) if path else None
        _ASSET_PIXMAPS[kind] = pm if (pm is not None and not pm.isNull()) else None
    return _ASSET_PIXMAPS[kind]


class Theme(QObject):
    """Paleta activa. Los widgets leen de aquí al pintarse y se repintan con `changed`."""
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.pal = dict(DEFAULT_PALETTE)

    def color(self, key, alpha=255):
        r, g, b = (int(clamp(v, 0, 255)) for v in self.pal[key])
        return QColor(r, g, b, alpha)

    def hex(self, key):
        return rgb_to_hex(self.pal[key])

    def set_palette(self, pal):
        self.pal = pal
        self.changed.emit()


class FlatSlider(QWidget):
    """Barra/slider plano. `valueChanged` solo se emite por interacción del usuario."""
    valueChanged = pyqtSignal(float)
    pressed = pyqtSignal()
    released = pyqtSignal(float)

    def __init__(self, theme, handle="always", track_h=4, wheel=False, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._handle = handle          # "always" | "never"
        self._track_h = track_h
        self._wheel = wheel
        self._value = 0.0
        self._dragging = False
        self._hover = False
        self._margin = 8 if handle == "always" else 0
        self.setFixedHeight(18 if handle == "always" else 14)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        theme.changed.connect(self.update)

    def value(self):
        return self._value

    def set_value(self, v):
        v = clamp(float(v), 0.0, 1.0)
        if v != self._value:
            self._value = v
            self.update()

    def _frac(self, x):
        usable = max(1.0, self.width() - 2 * self._margin)
        return clamp((x - self._margin) / usable, 0.0, 1.0)

    def _set_from_event(self, e):
        self._value = self._frac(e.position().x())
        self.update()
        self.valueChanged.emit(self._value)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.pressed.emit()
            self._set_from_event(e)
            e.accept()

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._set_from_event(e)

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._value = self._frac(e.position().x())
            self.update()
            self.released.emit(self._value)

    def wheelEvent(self, e):
        if not self._wheel:
            e.ignore()
            return
        step = 0.05 if e.angleDelta().y() > 0 else -0.05
        self._value = clamp(self._value + step, 0.0, 1.0)
        self.update()
        self.valueChanged.emit(self._value)
        e.accept()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, m = self.width(), self.height(), self._margin
        th = self._track_h + (2 if (self._hover or self._dragging) and self._handle == "never" else 0)
        cy = h / 2
        usable = max(0.0, w - 2 * m)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.theme.color("track"))
        p.drawRoundedRect(QRectF(m, cy - th / 2, usable, th), th / 2, th / 2)
        fx = m + usable * self._value
        if self._value > 0:
            p.setBrush(self.theme.color("accent"))
            p.drawRoundedRect(QRectF(m, cy - th / 2, fx - m, th), th / 2, th / 2)
        if self._handle == "always":
            hot = self._hover or self._dragging
            p.setBrush(self.theme.color("accent_hover" if hot else "accent"))
            p.drawEllipse(QPointF(fx, cy), 6.0, 6.0)


class MediaButton(QAbstractButton):
    """Prev / play / pause / next. Usa assets/btn_*.png si existen; si no, dibuja el icono."""

    def __init__(self, kind, theme, icon_px, pad=6, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.kind = kind
        self.icon_px = icon_px
        self.pad = pad
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        theme.changed.connect(self.update)
        self._fit()

    def _fit(self):
        s = self.icon_px + 2 * self.pad
        self.setFixedSize(s, s)

    def set_kind(self, kind):
        if kind != self.kind:
            self.kind = kind
            self.update()

    def set_icon_px(self, px):
        px = int(px)
        if px != self.icon_px:
            self.icon_px = px
            self._fit()
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        if self._hover:
            p.setBrush(self.theme.color("hover"))
            p.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)

        s = float(self.icon_px)
        x0, y0 = (w - s) / 2, (h - s) / 2
        pm = asset_pixmap(self.kind)
        if pm is not None:
            p.drawPixmap(QRectF(x0, y0, s, s), pm, QRectF(pm.rect()))
            return

        def pt(fx, fy):
            return QPointF(x0 + fx * s, y0 + fy * s)

        p.setBrush(QColor(255, 255, 255))
        if self.kind == "play":
            p.drawPolygon(QPolygonF([pt(.30, .18), pt(.30, .82), pt(.82, .50)]))
        elif self.kind == "pause":
            p.drawRoundedRect(QRectF(x0 + .24 * s, y0 + .18 * s, .19 * s, .64 * s), .04 * s, .04 * s)
            p.drawRoundedRect(QRectF(x0 + .57 * s, y0 + .18 * s, .19 * s, .64 * s), .04 * s, .04 * s)
        elif self.kind == "prev":
            p.drawRoundedRect(QRectF(x0 + .20 * s, y0 + .22 * s, .10 * s, .56 * s), .03 * s, .03 * s)
            p.drawPolygon(QPolygonF([pt(.82, .22), pt(.82, .78), pt(.36, .50)]))
        elif self.kind == "next":
            p.drawRoundedRect(QRectF(x0 + .70 * s, y0 + .22 * s, .10 * s, .56 * s), .03 * s, .03 * s)
            p.drawPolygon(QPolygonF([pt(.18, .22), pt(.18, .78), pt(.64, .50)]))


class GlyphButton(QAbstractButton):
    """Botones redondos de la esquina: ⋮ (menú) y ✕ (cerrar), dibujados sin depender de fuentes."""

    def __init__(self, kind, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.kind = kind               # "menu" | "close"
        self._hover = False
        self._active = False
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        theme.changed.connect(self.update)

    def set_active(self, active):
        self._active = active
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        glyph = QColor("#aaaaaa")
        if self.kind == "close" and self._hover:
            p.setBrush(QColor("#ff4444"))
            p.drawEllipse(QRectF(0, 0, 24, 24))
            glyph = QColor("#ffffff")
        elif self._hover or self._active:
            p.setBrush(self.theme.color("hover"))
            p.drawEllipse(QRectF(0, 0, 24, 24))
            glyph = QColor("#dddddd")
        if self.kind == "menu":
            p.setBrush(glyph)
            for cy in (7.0, 12.0, 17.0):
                p.drawEllipse(QPointF(12, cy), 1.7, 1.7)
        else:
            pen = QPen(glyph, 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(8, 8), QPointF(16, 16))
            p.drawLine(QPointF(16, 8), QPointF(8, 16))


class VolumeButton(QAbstractButton):
    """Altavoz que refleja el nivel de volumen; al pulsarlo silencia / restaura."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0.8
        self._hover = False
        self.setFixedSize(22, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Clic: silenciar / restaurar")

    def set_level(self, v):
        self._level = v
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#dddddd" if self._hover else "#aaaaaa")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(3, 8), QPointF(6.5, 8), QPointF(10.5, 4.5),
                                 QPointF(10.5, 15.5), QPointF(6.5, 12), QPointF(3, 12)]))
        pen = QPen(color, 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        v = self._level
        if v < 0.01:
            p.drawLine(QPointF(13.5, 7.5), QPointF(19, 12.5))
            p.drawLine(QPointF(19, 7.5), QPointF(13.5, 12.5))
        else:
            waves = 1 if v < 0.34 else 2 if v < 0.67 else 3
            for i in range(waves):
                r = 3.0 + 2.6 * i
                p.drawArc(QRectF(10.5 - r, 10 - r, 2 * r, 2 * r), -50 * 16, 100 * 16)


class ElidedLabel(QLabel):
    """Etiqueta de una línea que recorta con «…» y muestra el texto completo en el tooltip."""

    def __init__(self, text="", px=12, bold=False, color="#ffffff", parent=None):
        super().__init__("", parent)
        self._full = text
        self._color = QColor(color)
        self.setFont(ui_font(px, bold))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def set_full_text(self, text):
        self._full = text
        self.setToolTip(text)
        self.update()

    def sizeHint(self):
        return QSize(0, QFontMetrics(self.font()).height() + 3)

    def minimumSizeHint(self):
        return QSize(0, QFontMetrics(self.font()).height() + 3)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self.font())
        p.setPen(self._color)
        text = QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        p.drawText(QRectF(self.rect()), ALIGN_LEFT_VCENTER, text)


class CoverWidget(QWidget):
    """Portada con esquinas redondeadas, renderizada a la resolución real del monitor."""
    doubleClicked = pyqtSignal()
    wheelScrolled = pyqtSignal(int)

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._image = None
        self._cache = None
        self._cache_key = None
        theme.changed.connect(self.update)

    def set_image(self, qimage):
        self._image = qimage
        self._cache = self._cache_key = None
        self.update()

    def _rendered(self):
        dpr = self.devicePixelRatioF()
        pw, ph = max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr))
        key = (pw, ph, id(self._image))
        if self._cache_key != key:
            scaled = self._image.scaled(QSize(pw, ph), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                        Qt.TransformationMode.SmoothTransformation)
            crop = scaled.copy(QRect((scaled.width() - pw) // 2, (scaled.height() - ph) // 2, pw, ph))
            pm = QPixmap(pw, ph)
            pm.fill(Qt.GlobalColor.transparent)
            q = QPainter(pm)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            q.setPen(Qt.PenStyle.NoPen)
            q.setBrush(QBrush(crop))
            radius = min(pw, ph) * 0.12
            q.drawRoundedRect(QRectF(0, 0, pw, ph), radius, radius)
            q.end()
            pm.setDevicePixelRatio(dpr)
            self._cache, self._cache_key = pm, key
        return self._cache

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if self._image is None or self._image.isNull():
            radius = min(w, h) * 0.12
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.theme.color("card"))
            p.drawRoundedRect(QRectF(0, 0, w, h), radius, radius)
            p.setPen(QColor("#777777"))
            p.setFont(ui_font(max(14, int(min(w, h) * 0.4))))
            p.drawText(QRectF(0, 0, w, h), ALIGN_CENTER, "♪")
        else:
            p.drawPixmap(0, 0, self._rendered())

    def mousePressEvent(self, e):
        e.ignore()                      # deja que el arrastre llegue a la ventana

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()

    def wheelEvent(self, e):
        self.wheelScrolled.emit(e.angleDelta().y())
        e.accept()


class LyricsView(QWidget):
    """Letra centrada con desplazamiento suave; la línea activa se resalta con el color de acento."""
    lineActivated = pyqtSignal(int)
    MARGIN = 14
    GAP = 10
    _FLAGS = Qt.AlignmentFlag.AlignHCenter.value | Qt.TextFlag.TextWordWrap.value

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._lines = []
        self._synced = False
        self._message = ""
        self._heights = []
        self._tops = []
        self._total = 0.0
        self._cur = -1
        self._offset = 0.0
        self._target = 0.0
        self._pause_until = 0.0
        self._font = ui_font(13)
        self._font_bold = ui_font(13, True)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self.setMinimumHeight(60)
        theme.changed.connect(self.update)

    # -- contenido -----------------------------------------------------------
    def set_message(self, text):
        self._lines, self._synced, self._message = [], False, text
        self._heights, self._tops, self._total = [], [], 0.0
        self._cur, self._offset, self._target = -1, 0.0, 0.0
        self.update()

    def set_lines(self, lines, synced):
        self._lines, self._synced, self._message = list(lines), bool(synced), ""
        self._cur, self._offset, self._target = -1, 0.0, 0.0
        self._pause_until = 0.0
        self._relayout()
        self.update()

    def set_current(self, idx):
        if idx == self._cur:
            return
        self._cur = idx
        if self._synced and time.monotonic() >= self._pause_until:
            self._target = self._center_offset(idx) if idx >= 0 else 0.0
            self._timer.start()
        self.update()

    def resume_follow(self):
        self._pause_until = 0.0
        if self._synced and self._cur >= 0:
            self._target = self._center_offset(self._cur)
            self._timer.start()

    # -- geometría -----------------------------------------------------------
    def _pad(self):
        return self.height() / 2 if self._synced else 6.0

    def _max_offset(self):
        return max(0.0, self._total + 2 * self._pad() - self.height())

    def _center_offset(self, i):
        if not (0 <= i < len(self._tops)):
            return 0.0
        off = self._pad() + self._tops[i] + self._heights[i] / 2 - self.height() / 2
        return clamp(off, 0.0, self._max_offset())

    def _relayout(self):
        w = max(10, self.width() - 2 * self.MARGIN)
        fm = QFontMetrics(self._font_bold)
        self._heights, self._tops = [], []
        y = 0.0
        for text in self._lines:
            r = fm.boundingRect(QRect(0, 0, w, 100000), self._FLAGS, text or "♪")
            h = float(r.height() + self.GAP)
            self._tops.append(y)
            self._heights.append(h)
            y += h
        self._total = y

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._lines:
            self._relayout()
            if self._synced and self._cur >= 0:
                self._target = self._offset = self._center_offset(self._cur)
            else:
                self._target = self._offset = clamp(self._offset, 0.0, self._max_offset())

    def _step(self):
        diff = self._target - self._offset
        if abs(diff) < 0.4:
            self._offset = self._target
            self._timer.stop()
        else:
            self._offset += diff * 0.2
        self.update()

    # -- interacción ---------------------------------------------------------
    def wheelEvent(self, e):
        self._target = clamp(self._target - e.angleDelta().y() * 0.6, 0.0, self._max_offset())
        self._pause_until = time.monotonic() + 4.0           # el usuario lee por su cuenta
        self._timer.start()
        e.accept()

    def mouseDoubleClickEvent(self, e):
        if not (self._synced and self._lines):
            return
        ay = e.position().y() + self._offset - self._pad()
        for i, top in enumerate(self._tops):
            if top <= ay < top + self._heights[i]:
                self.lineActivated.emit(i)
                return

    # -- pintado ---------------------------------------------------------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect())
        bg = self.theme.color("lyrics_bg")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, 8, 8)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        p.setClipPath(path)

        w = self.width() - 2 * self.MARGIN
        if not self._lines:
            if self._message:
                p.setPen(QColor("#8c8c8c"))
                p.setFont(self._font)
                p.drawText(QRectF(self.MARGIN, 0, w, self.height()),
                           self._FLAGS | Qt.AlignmentFlag.AlignVCenter.value, self._message)
            return

        pad = self._pad()
        accent = self.theme.color("accent")
        for i, text in enumerate(self._lines):
            y = pad + self._tops[i] - self._offset
            h = self._heights[i]
            if y + h < 0:
                continue
            if y > self.height():
                break
            if self._synced:
                if i == self._cur:
                    color, font = accent, self._font_bold
                else:
                    dist = abs(i - self._cur) if self._cur >= 0 else i + 1
                    color = QColor(154, 154, 154)
                    color.setAlphaF(max(0.28, 1.0 - 0.2 * dist))
                    font = self._font
            else:
                color, font = QColor(190, 190, 190), self._font
            p.setFont(font)
            p.setPen(color)
            p.drawText(QRectF(self.MARGIN, y, w, h - self.GAP), self._FLAGS, text or "♪")

        # fundido en los bordes superior e inferior
        clear = QColor(bg)
        clear.setAlpha(0)
        top = QLinearGradient(0, 0, 0, 28)
        top.setColorAt(0.0, bg)
        top.setColorAt(1.0, clear)
        p.fillRect(QRectF(0, 0, self.width(), 28), QBrush(top))
        hh = self.height()
        bottom = QLinearGradient(0, hh - 28, 0, hh)
        bottom.setColorAt(0.0, clear)
        bottom.setColorAt(1.0, bg)
        p.fillRect(QRectF(0, hh - 28, self.width(), 28), QBrush(bottom))


class MenuItem(QAbstractButton):
    """Fila del menú de opciones."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._active = False
        self._hover = False
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        theme.changed.connect(self.update)

    def set_item(self, text, active=False):
        self.setText(text)
        self._active = active
        self.update()

    def sizeHint(self):
        return QSize(180, 26)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#333333"))
            p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.setFont(ui_font(12))
        p.setPen(self.theme.color("accent") if self._active else QColor("#dddddd"))
        p.drawText(QRectF(10, 0, self.width() - 14, self.height()),
                   ALIGN_LEFT_VCENTER, self.text())


class OptionsPopup(QWidget):
    """Menú flotante (ventana tipo Popup: se cierra solo al hacer clic fuera o con Esc)."""
    closed = pyqtSignal()

    def __init__(self, theme, parent, tray_available):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.theme = theme
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(200)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 10)
        lay.setSpacing(1)
        self.item_lyrics = MenuItem(theme)
        self.item_autostart = MenuItem(theme)
        self.item_topmost = MenuItem(theme)
        self.item_tray = MenuItem(theme)
        self.item_tray.set_item("📥 Minimizar a la bandeja")
        self.item_autostart.setToolTip("Inicia la aplicación automáticamente\nal encender el ordenador.")
        for it in (self.item_lyrics, self.item_autostart, self.item_topmost):
            lay.addWidget(it)
        if tray_available:
            lay.addWidget(self.item_tray)
        else:
            self.item_tray.hide()

        self.lbl_opacity = QLabel("👁 Opacidad: 100%")
        self.lbl_opacity.setFont(ui_font(11))
        self.lbl_opacity.setStyleSheet("color: #aaaaaa; padding-left: 6px; padding-top: 4px;")
        lay.addWidget(self.lbl_opacity)
        self.slider_opacity = FlatSlider(theme, handle="always", wheel=True)
        lay.addWidget(self.slider_opacity)

    def show_at(self, global_pos):
        self.adjustSize()
        w, h = self.width(), self.height()
        scr = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        x = int(clamp(global_pos.x() - w, g.left(), max(g.left(), g.right() - w)))
        y = int(clamp(global_pos.y(), g.top(), max(g.top(), g.bottom() - h)))
        self.move(x, y)
        self.show()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.closed.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#444444"), 1))
        p.setBrush(QColor("#222222"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)


class Grip(QSizeGrip):
    """Esquina de redimensionado (QSizeGrip usa el redimensionado nativo del sistema)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#666666"), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for d in (4.0, 8.0, 12.0):
            p.drawLine(QPointF(15, 15 - d), QPointF(15 - d, 15))


def make_tray_pixmap(accent):
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(*(int(clamp(c, 0, 255)) for c in accent[:3])))
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    p.setBrush(QColor(255, 255, 255))
    p.drawPolygon(QPolygonF([QPointF(25, 19), QPointF(25, 45), QPointF(47, 32)]))
    p.end()
    return pm


# ──────────────────────────────────────────────────────────────────────────────
#  Ventana principal
# ──────────────────────────────────────────────────────────────────────────────
class MiniPlayer(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = Config.load()
        self.theme = Theme()

        # ---- estado ----
        self.running = True
        self.ui_queue = queue.Queue()          # única vía de entrada desde otros hilos
        self.backend = MediaBackend(self.ui_queue)
        self.audio = AudioController(self.ui_queue)
        self.lyrics_provider = LyricsProvider(LYRICS_DIR)

        self.media = MediaSnapshot()
        self.shown_track_id = None
        self.shown_playing = None
        self.idle_shown = False
        self.cover_image = None
        self.last_labels = ("", "")
        self.mini_icon_sizes = (22, 16)

        self.seeking = False
        self.seek_fraction = 0.0
        self.seek_guard_until = 0.0
        self.pre_mute_volume = 0.5
        self.last_volume_user_ts = 0.0
        self.last_volume_poll = 0.0

        self.lyrics = Lyrics()
        self.lyrics_req_id = 0
        self.lyrics_track_id = None
        self.lyric_times = []
        self.current_lyric_idx = -2

        self.autostart_on = autostart_enabled()
        self.tray = None
        self.tray_menu = None
        self._theme_anim = None
        self._drag_off = None
        self._internal_resize = 0              # >0 mientras redimensionamos nosotros
        self._options_closed_at = 0.0
        self._shortcuts = []

        # ---- ventana sin marco, translúcida (esquinas redondeadas pintadas por nosotros) ----
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.cfg.is_topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(APP_TITLE)
        self.setWindowOpacity(self.cfg.opacity_val)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(800)
        self.save_timer.timeout.connect(self._save_now)

        self._build_ui()
        self._build_options()
        self._build_shortcuts()
        self.theme.changed.connect(self.update)

        # ---- tamaño y posición inicial ----
        w, h = self._window_size()
        self._set_size(w, h)
        pos = self._valid_position(self.cfg.win_x, self.cfg.win_y, w, h)
        if pos is None:
            g = QGuiApplication.primaryScreen().availableGeometry()
            pos = (g.right() - w - 24, g.bottom() - h - 24)
        self.move(pos[0], pos[1])
        self._apply_mode(resize=False)
        self._apply_play_icons()
        self._refresh_menu_items()
        self._show_idle()

        # ---- bandeja, hilos y bucle de interfaz ----
        self._setup_tray()
        self.backend.start()
        self.audio.start()
        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(UI_TICK_MS)
        self.tick_timer.timeout.connect(self._ui_tick)
        self.tick_timer.start()

    # ══════════════════════════════════════════════════════════════════════
    #  Construcción de la interfaz
    # ══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _small_label(text, px):
        lbl = QLabel(text)
        lbl.setFont(ui_font(px))
        lbl.setStyleSheet("color: #aaaaaa; background: transparent;")
        return lbl

    def _build_ui(self):
        th = self.theme
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        # ---------- vista normal ----------
        self.normal_page = QWidget()
        npl = QVBoxLayout(self.normal_page)
        npl.setContentsMargins(0, 0, 0, 0)
        npl.setSpacing(0)

        self.top_row = QWidget()
        hl = QHBoxLayout(self.top_row)
        hl.setContentsMargins(12, 22, 15, 10)
        hl.setSpacing(12)
        self.cover = CoverWidget(th)
        self.cover.setFixedSize(144, 144)
        hl.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.lbl_title = ElidedLabel("Cargando...", 14, True, "#f2f2f2")
        self.lbl_artist = ElidedLabel("--", 11, False, "#aaaaaa")
        self.lbl_elapsed = self._small_label("00:00", 10)
        self.lbl_remaining = self._small_label("-00:00", 10)
        self.seek = FlatSlider(th, handle="never", track_h=6)
        self.seek.setToolTip("Haz clic para saltar a ese punto")
        self.btn_prev = MediaButton("prev", th, 24)
        self.btn_play = MediaButton("play", th, 32)
        self.btn_next = MediaButton("next", th, 24)
        self.vol_icon = VolumeButton()
        self.slider_vol = FlatSlider(th, handle="always", track_h=4, wheel=True)
        self.slider_vol.set_value(0.8)

        col.addStretch(1)
        col.addWidget(self.lbl_title)
        col.addWidget(self.lbl_artist)
        col.addSpacing(4)
        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.addWidget(self.lbl_elapsed)
        time_row.addStretch(1)
        time_row.addWidget(self.lbl_remaining)
        col.addLayout(time_row)
        col.addSpacing(2)
        col.addWidget(self.seek)
        col.addSpacing(2)
        ctrl = QHBoxLayout()
        ctrl.setContentsMargins(0, 0, 0, 0)
        ctrl.addStretch(1)
        ctrl.addWidget(self.btn_prev)
        ctrl.addStretch(1)
        ctrl.addWidget(self.btn_play)
        ctrl.addStretch(1)
        ctrl.addWidget(self.btn_next)
        ctrl.addStretch(1)
        col.addLayout(ctrl)
        col.addSpacing(4)
        vol_row = QHBoxLayout()
        vol_row.setContentsMargins(0, 0, 0, 0)
        vol_row.setSpacing(4)
        vol_row.addWidget(self.vol_icon)
        vol_row.addWidget(self.slider_vol, 1)
        col.addLayout(vol_row)
        col.addStretch(1)
        hl.addLayout(col, 1)

        self.lyrics_box = QWidget()
        lb = QVBoxLayout(self.lyrics_box)
        lb.setContentsMargins(12, 0, 12, 12)
        self.lyrics_view = LyricsView(th)
        lb.addWidget(self.lyrics_view)

        npl.addWidget(self.top_row)
        npl.addWidget(self.lyrics_box, 1)

        # ---------- vista mini ----------
        self.mini_page = QWidget()
        ml = QVBoxLayout(self.mini_page)
        ml.setContentsMargins(10, 22, 10, 6)
        ml.setSpacing(2)
        self.cover_mini = CoverWidget(th)
        self.cover_mini.setFixedSize(70, 70)
        self.seek_mini = FlatSlider(th, handle="never", track_h=5)
        self.btn_prev_mini = MediaButton("prev", th, 16, pad=3)
        self.btn_play_mini = MediaButton("play", th, 22, pad=3)
        self.btn_next_mini = MediaButton("next", th, 16, pad=3)
        self.vol_icon_mini = VolumeButton()
        self.slider_vol_mini = FlatSlider(th, handle="always", track_h=4, wheel=True)
        self.slider_vol_mini.set_value(0.8)

        ml.addWidget(self.cover_mini, 0, Qt.AlignmentFlag.AlignHCenter)
        ml.addWidget(self.seek_mini)
        ctrl_m = QHBoxLayout()
        ctrl_m.setContentsMargins(0, 0, 0, 0)
        ctrl_m.addStretch(1)
        ctrl_m.addWidget(self.btn_prev_mini)
        ctrl_m.addWidget(self.btn_play_mini)
        ctrl_m.addWidget(self.btn_next_mini)
        ctrl_m.addStretch(1)
        ml.addLayout(ctrl_m)
        vol_m = QHBoxLayout()
        vol_m.setContentsMargins(0, 0, 0, 0)
        vol_m.setSpacing(2)
        vol_m.addWidget(self.vol_icon_mini)
        vol_m.addWidget(self.slider_vol_mini, 1)
        ml.addLayout(vol_m)
        ml.addStretch(1)

        root.addWidget(self.normal_page)
        root.addWidget(self.mini_page)

        # ---------- botones flotantes de la esquina ----------
        self.btn_menu = GlyphButton("menu", th, self)
        self.btn_close = GlyphButton("close", th, self)
        self.grip = Grip(self)

        # ---------- conexiones ----------
        send = self.backend.send
        for prev, play, nxt in ((self.btn_prev, self.btn_play, self.btn_next),
                                (self.btn_prev_mini, self.btn_play_mini, self.btn_next_mini)):
            prev.clicked.connect(lambda *_: send("prev"))
            play.clicked.connect(lambda *_: send("play_pause"))
            nxt.clicked.connect(lambda *_: send("next"))
        for bar in (self.seek, self.seek_mini):
            bar.pressed.connect(self._seek_begin)
            bar.valueChanged.connect(self._seek_move)
            bar.released.connect(self._seek_end)
        for s in (self.slider_vol, self.slider_vol_mini):
            s.valueChanged.connect(self._on_volume_slider)
        for icon in (self.vol_icon, self.vol_icon_mini):
            icon.clicked.connect(self.toggle_mute)
        for cover in (self.cover, self.cover_mini):
            cover.doubleClicked.connect(self.toggle_mini_mode)
            cover.wheelScrolled.connect(self._on_cover_wheel)
        self.lyrics_view.lineActivated.connect(self._on_lyric_line)
        self.btn_menu.clicked.connect(self._toggle_options)
        self.btn_close.clicked.connect(self.on_close)

    def _build_options(self):
        self.options = OptionsPopup(self.theme, self, QSystemTrayIcon.isSystemTrayAvailable())
        o = self.options
        o.item_lyrics.clicked.connect(lambda *_: (o.hide(), self.toggle_lyrics()))
        o.item_autostart.clicked.connect(lambda *_: (self._toggle_autostart(), o.hide()))
        o.item_topmost.clicked.connect(lambda *_: self._toggle_topmost())
        o.item_tray.clicked.connect(lambda *_: self.hide_to_tray())
        o.slider_opacity.valueChanged.connect(self._on_opacity)
        o.closed.connect(self._on_options_closed)

    def _build_shortcuts(self):
        def add(key, fn):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(fn)
            self._shortcuts.append(sc)

        add("Space", lambda: self.backend.send("play_pause"))
        add("Left", lambda: self.backend.send("prev"))
        add("Right", lambda: self.backend.send("next"))
        add("Up", lambda: self._nudge_volume(0.05))
        add("Down", lambda: self._nudge_volume(-0.05))
        add("L", self.toggle_lyrics)
        add("M", self.toggle_mini_mode)

    # ══════════════════════════════════════════════════════════════════════
    #  Pintado de la ventana
    # ══════════════════════════════════════════════════════════════════════
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self.theme.color("track"), 1))
        p.setBrush(self.theme.color("bg"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

    # ══════════════════════════════════════════════════════════════════════
    #  Tema / colores
    # ══════════════════════════════════════════════════════════════════════
    def _animate_palette(self, target, ms=240):
        if self._theme_anim is not None:
            self._theme_anim.stop()
        start = dict(self.theme.pal)
        anim = QVariantAnimation(self)
        anim.setDuration(ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.valueChanged.connect(
            lambda t: self.theme.set_palette({k: mix(start[k], target[k], float(t)) for k in target}))
        self._theme_anim = anim
        anim.start()

    def _set_cover(self, data):
        """`data`: bytes de la imagen, o None para volver al aspecto por defecto."""
        if data is None:
            self.cover_image = None
            target = DEFAULT_PALETTE
        else:
            img = QImage.fromData(data)
            if img.isNull():
                return
            self.cover_image = img
            target = make_palette(extract_accent_color(img))
        self.cover.set_image(self.cover_image)
        self.cover_mini.set_image(self.cover_image)
        self._animate_palette(target)
        if self.tray:
            self.tray.setIcon(QIcon(make_tray_pixmap(target["accent"])))

    # ══════════════════════════════════════════════════════════════════════
    #  Tamaño, modo mini, letra, arrastre
    # ══════════════════════════════════════════════════════════════════════
    def _window_size(self):
        c = self.cfg
        if c.is_mini:
            return c.mini_w, c.mini_h
        return c.normal_w, c.normal_h + (c.lyrics_height if c.show_lyrics else 0)

    def _set_size(self, w, h):
        self._internal_resize += 1
        try:
            self.resize(int(w), int(h))
        finally:
            self._internal_resize -= 1

    def _remember_size(self, w, h):
        c = self.cfg
        if c.is_mini:
            c.mini_w, c.mini_h = w, h
        else:
            c.normal_w = w
            if c.show_lyrics:
                c.lyrics_height = max(100, h - c.normal_h)
            else:
                c.normal_h = h

    def _apply_mode(self, resize=True):
        """Muestra la vista que toca y ajusta mínimos. Los cambios de tamaño que provoca
        no cuentan como redimensionado del usuario (no deben pisar los tamaños guardados)."""
        c = self.cfg
        self._internal_resize += 1
        try:
            self.normal_page.setVisible(not c.is_mini)
            self.mini_page.setVisible(c.is_mini)
            self.lyrics_box.setVisible(c.show_lyrics and not c.is_mini)
            if c.is_mini:
                self.setMinimumSize(120, 150)
            elif c.show_lyrics:
                self.top_row.setFixedHeight(c.normal_h)
                self.setMinimumSize(360, c.normal_h + 100)
            else:
                self.top_row.setMinimumHeight(0)
                self.top_row.setMaximumHeight(16777215)
                self.setMinimumSize(360, 176)
            if resize:
                w, h = self._window_size()
                self._set_size(w, h)
        finally:
            self._internal_resize -= 1
        self._update_cover_sizes()
        self._position_corner_widgets()

    def _position_corner_widgets(self):
        w, h = self.width(), self.height()
        self.btn_close.move(w - 28, 4)
        self.btn_menu.move(w - 54, 4)
        self.grip.move(w - 16, h - 16)
        self.btn_close.raise_()
        self.btn_menu.raise_()
        self.grip.raise_()

    def _update_cover_sizes(self):
        c = self.cfg
        w, h = self.width(), self.height()
        if c.is_mini:
            side = int(clamp(min(w - 24, h - 100), 40, 600))
            self.cover_mini.setFixedSize(side, side)
            k = max(0.8, w / 140.0)
            play_px, nav_px = max(16, int(22 * k)), max(12, int(16 * k))
            self.mini_icon_sizes = (play_px, nav_px)
            self.btn_play_mini.set_icon_px(play_px)
            self.btn_prev_mini.set_icon_px(nav_px)
            self.btn_next_mini.set_icon_px(nav_px)
        else:
            side = int(clamp(min(c.normal_h - 36, w * 0.45), 60, 600))
            self.cover.setFixedSize(side, side)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._internal_resize:
            self._remember_size(self.width(), self.height())
            self._schedule_save()
        self._update_cover_sizes()
        self._position_corner_widgets()

    def toggle_mini_mode(self, *_):
        c = self.cfg
        c.is_mini = not c.is_mini
        self.options.hide()
        self._apply_mode(resize=True)
        self._apply_play_icons()
        self.current_lyric_idx = -2
        self._schedule_save()

    def toggle_lyrics(self, *_):
        c = self.cfg
        if c.is_mini:
            return
        c.show_lyrics = not c.show_lyrics
        self._apply_mode(resize=True)
        if c.show_lyrics:
            self._ensure_lyrics()
            self.current_lyric_idx = -2
        self._refresh_menu_items()
        self._schedule_save()

    # ---- arrastre con imán a los bordes del monitor ---------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.activateWindow()                  # para que funcionen los atajos
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_off is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            cursor = e.globalPosition().toPoint()
            pos = cursor - self._drag_off
            x, y = self._snap(pos.x(), pos.y(), cursor)
            self.move(x, y)

    def mouseReleaseEvent(self, e):
        if self._drag_off is not None:
            self._drag_off = None
            self._schedule_save()

    def _snap(self, x, y, cursor, margin=14):
        scr = QGuiApplication.screenAt(cursor) or self.screen()
        if scr is None:
            return x, y
        g = scr.availableGeometry()
        left, top, right, bottom = g.left(), g.top(), g.right() + 1, g.bottom() + 1
        w, h = self.width(), self.height()
        if abs(x - left) < margin:
            x = left
        elif abs(x + w - right) < margin:
            x = right - w
        if abs(y - top) < margin:
            y = top
        elif abs(y + h - bottom) < margin:
            y = bottom - h
        return x, y

    @staticmethod
    def _valid_position(x, y, w, h):
        """Descarta una posición guardada que ya no cae en ningún monitor."""
        if x is None or y is None:
            return None
        rect = QRect(int(x), int(y), int(w), int(h))
        for scr in QGuiApplication.screens():
            inter = scr.availableGeometry().intersected(rect)
            if inter.width() >= 40 and inter.height() >= 40:
                return (int(x), int(y))
        return None

    # ---- siempre encima (sin recrear la ventana) -------------------------------------
    def _apply_topmost(self):
        try:
            user32 = ctypes.windll.user32
            user32.SetWindowPos.argtypes = [ctypes.c_ssize_t, ctypes.c_ssize_t, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            hwnd_insert = -1 if self.cfg.is_topmost else -2          # HWND_TOPMOST / HWND_NOTOPMOST
            user32.SetWindowPos(int(self.winId()), hwnd_insert, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    #  Menú de opciones
    # ══════════════════════════════════════════════════════════════════════
    def _refresh_menu_items(self):
        c, o = self.cfg, self.options
        o.item_lyrics.set_item("📜 Ocultar Letra" if c.show_lyrics else "📜 Mostrar Letra", c.show_lyrics)
        o.item_autostart.set_item("🚀 Inicio aut. (ON)" if self.autostart_on else "🚀 Inicio automático",
                                  self.autostart_on)
        o.item_topmost.set_item("📌 Siempre encima (ON)" if c.is_topmost else "📌 Siempre encima",
                                c.is_topmost)
        o.slider_opacity.set_value((c.opacity_val - 0.3) / 0.7)
        o.lbl_opacity.setText(f"👁 Opacidad: {int(round(c.opacity_val * 100))}%")

    def _toggle_options(self, *_):
        if self.options.isVisible():
            self.options.hide()
            return
        if time.monotonic() - self._options_closed_at < 0.25:    # el clic que acaba de cerrarlo
            return
        self._refresh_menu_items()
        anchor = self.btn_menu.mapToGlobal(QPoint(self.btn_menu.width(), self.btn_menu.height() + 2))
        self.options.show_at(anchor)
        self.btn_menu.set_active(True)

    def _on_options_closed(self):
        self._options_closed_at = time.monotonic()
        self.btn_menu.set_active(False)

    def _toggle_autostart(self):
        if set_autostart(not self.autostart_on):
            self.autostart_on = not self.autostart_on
        self._refresh_menu_items()

    def _toggle_topmost(self):
        self.cfg.is_topmost = not self.cfg.is_topmost
        self._apply_topmost()
        self._refresh_menu_items()
        self._schedule_save()

    def _on_opacity(self, v):
        self.cfg.opacity_val = round(0.3 + 0.7 * float(v), 2)
        self.setWindowOpacity(self.cfg.opacity_val)
        self.options.lbl_opacity.setText(f"👁 Opacidad: {int(round(self.cfg.opacity_val * 100))}%")
        self._schedule_save()

    # ══════════════════════════════════════════════════════════════════════
    #  Bandeja del sistema
    # ══════════════════════════════════════════════════════════════════════
    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        try:
            self.tray = QSystemTrayIcon(QIcon(make_tray_pixmap(DEFAULT_ACCENT)), self)
            menu = QMenu()
            entries = (
                ("Mostrar / ocultar", self._toggle_visibility),
                ("Reproducir / pausar", lambda: self.backend.send("play_pause")),
                ("Anterior", lambda: self.backend.send("prev")),
                ("Siguiente", lambda: self.backend.send("next")),
            )
            for text, fn in entries:
                action = menu.addAction(text)
                action.triggered.connect(lambda *_, f=fn: f())
            menu.addSeparator()
            quit_action = menu.addAction("Salir")
            quit_action.triggered.connect(lambda *_: self.on_close())
            self.tray_menu = menu
            self.tray.setContextMenu(menu)
            self.tray.setToolTip(APP_TITLE)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.show()
        except Exception as e:
            self.tray = None
            print(f"Error iniciando icono en la bandeja: {e}")

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visibility()

    def _set_tray_tooltip(self, text):
        if self.tray:
            self.tray.setToolTip(text[:120])

    def _toggle_visibility(self):
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.restore_from_tray()

    def hide_to_tray(self):
        self.options.hide()
        self.hide()

    def restore_from_tray(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._apply_topmost()

    # ══════════════════════════════════════════════════════════════════════
    #  Configuración (guardado con debounce)
    # ══════════════════════════════════════════════════════════════════════
    def _schedule_save(self):
        self.save_timer.start()           # reinicia el temporizador de 800 ms

    def _save_now(self):
        pos = self.pos()
        self.cfg.win_x, self.cfg.win_y = pos.x(), pos.y()
        self.cfg.save()

    # ══════════════════════════════════════════════════════════════════════
    #  Bucle de interfaz: cola de eventos + refresco
    # ══════════════════════════════════════════════════════════════════════
    def _ui_tick(self):
        if not self.running:
            return
        try:
            self._drain_queue()
            self._refresh_progress()
            now = time.monotonic()
            if self.media.has_session and now - self.last_volume_poll > VOLUME_POLL_S:
                self.last_volume_poll = now
                self.audio.request_volume(self.media.app_id)
        except Exception:
            log_exception(*sys.exc_info())

    def _drain_queue(self):
        while True:
            try:
                kind, *payload = self.ui_queue.get_nowait()
            except queue.Empty:
                return
            if kind == "media":
                self._on_media(payload[0])
            elif kind == "lyrics":
                self._on_lyrics(*payload)
            elif kind == "volume":
                self._on_volume_sync(payload[0])
            elif kind == "error":
                print(payload[0])
                if not self.media.has_session:
                    self.lbl_artist.set_full_text(str(payload[0])[:160])

    # ---- eventos multimedia -----------------------------------------------------
    def _on_media(self, snap):
        if not snap.has_session:
            self.media = snap
            if not self.idle_shown:
                self._show_idle()
            return

        # Si acabamos de saltar de posición, ignora muestras anteriores a la orden.
        if (time.monotonic() < self.seek_guard_until and self.media.has_session
                and snap.track_id == self.media.track_id):
            snap.position, snap.sampled_at = self.media.position, self.media.sampled_at

        self.media = snap
        self.idle_shown = False

        if snap.track_id != self.shown_track_id:
            self._on_track_changed(snap)
        if snap.cover_bytes:
            self._set_cover(snap.cover_bytes)
        elif snap.cover_failed and self.cover_image is not None:
            self._set_cover(None)
        if snap.is_playing != self.shown_playing:
            self.shown_playing = snap.is_playing
            self._apply_play_icons()

    def _on_track_changed(self, snap):
        self.shown_track_id = snap.track_id
        self.lbl_title.set_full_text(snap.title)
        self.lbl_artist.set_full_text(snap.artist)
        self.setWindowTitle(f"{snap.title} — {snap.artist}")
        self._set_tray_tooltip(f"{snap.title} — {snap.artist}")

        self.lyrics = Lyrics()
        self.lyrics_track_id = None
        self.lyric_times = []
        self.current_lyric_idx = -2
        self.lyrics_req_id += 1                  # invalida respuestas pendientes
        self.lyrics_view.set_message("")
        if self.cfg.show_lyrics:
            self._ensure_lyrics()
        self.last_volume_poll = 0.0              # fuerza lectura inmediata del volumen

    def _show_idle(self):
        self.idle_shown = True
        self.shown_track_id = None
        self.shown_playing = False
        self.lbl_title.set_full_text("Sin reproducción activa")
        self.lbl_artist.set_full_text("Abre Spotify o YouTube")
        self.seek.set_value(0)
        self.seek_mini.set_value(0)
        self.lbl_elapsed.setText("00:00")
        self.lbl_remaining.setText("-00:00")
        self.last_labels = ("", "")
        self._apply_play_icons()
        self.lyrics_track_id = None
        self.lyrics_req_id += 1
        self.lyric_times = []
        self.lyrics_view.set_message("")
        if self.cover_image is not None:
            self._set_cover(None)
        self._set_tray_tooltip(APP_TITLE)

    def _apply_play_icons(self):
        kind = "pause" if self.shown_playing else "play"
        self.btn_play.set_kind(kind)
        self.btn_play_mini.set_kind(kind)

    # ---- progreso ---------------------------------------------------------------------
    def _current_position(self):
        m = self.media
        pos = m.position
        if m.is_playing:
            pos += time.monotonic() - m.sampled_at        # interpolación entre muestras
        if m.duration > 0:
            pos = min(pos, m.duration)
        return max(0.0, pos)

    def _refresh_progress(self):
        m = self.media
        if not m.has_session:
            return
        total = m.duration
        pos = self.seek_fraction * total if self.seeking else self._current_position()

        if total > 0:
            if not self.seeking:
                frac = clamp(pos / total, 0.0, 1.0)
                self.seek.set_value(frac)
                self.seek_mini.set_value(frac)
            labels = (fmt_time(pos), "-" + fmt_time(total - pos))
        else:
            self.seek.set_value(0)
            self.seek_mini.set_value(0)
            labels = ("--:--", "--:--")
        if labels != self.last_labels and not self.cfg.is_mini:
            self.last_labels = labels
            self.lbl_elapsed.setText(labels[0])
            self.lbl_remaining.setText(labels[1])

        self._update_lyrics_highlight(self._current_position())

    # ---- saltar en la canción ---------------------------------------------------------
    def _seek_begin(self):
        if self.media.duration > 0:
            self.seeking = True

    def _seek_move(self, v):
        if not self.seeking:
            return
        self.seek_fraction = float(v)
        self.seek.set_value(v)
        self.seek_mini.set_value(v)

    def _seek_end(self, v):
        if not self.seeking:
            return
        self.seeking = False
        self._seek_to(float(v) * self.media.duration)

    def _seek_to(self, seconds):
        m = self.media
        if not m.has_session or m.duration <= 0:
            return
        seconds = clamp(seconds, 0.0, m.duration)
        m.position, m.sampled_at = seconds, time.monotonic()
        self.seek_guard_until = time.monotonic() + 1.0
        self.backend.send("seek", seconds)

    # ---- volumen -------------------------------------------------------------------------
    def _show_volume(self, v):
        self.slider_vol.set_value(v)
        self.slider_vol_mini.set_value(v)
        self.vol_icon.set_level(v)
        self.vol_icon_mini.set_level(v)

    def _on_volume_slider(self, v):
        v = float(v)
        self.last_volume_user_ts = time.monotonic()
        self._show_volume(v)
        self.audio.set_volume(self.media.app_id, v)

    def _on_volume_sync(self, v):
        if time.monotonic() - self.last_volume_user_ts > 1.5:     # no pisar al usuario mientras arrastra
            self._show_volume(v)

    def _nudge_volume(self, delta):
        self._on_volume_slider(clamp(self.slider_vol.value() + delta, 0.0, 1.0))

    def _on_cover_wheel(self, delta):
        self._nudge_volume(0.05 if delta > 0 else -0.05)

    def toggle_mute(self, *_):
        cur = self.slider_vol.value()
        if cur > 0.001:
            self.pre_mute_volume = cur
            self._on_volume_slider(0.0)
        else:
            self._on_volume_slider(self.pre_mute_volume or 0.5)

    # ---- letras ------------------------------------------------------------------------------
    def _ensure_lyrics(self):
        snap = self.media
        if not snap.has_session or self.lyrics_track_id == snap.track_id:
            return
        self.lyrics_track_id = snap.track_id
        self.lyrics_req_id += 1
        self.lyric_times = []
        self.lyrics_view.set_message("Buscando letra…")
        threading.Thread(
            target=self._lyrics_worker,
            args=(self.lyrics_req_id, snap.title, snap.lyrics_artist, snap.album, snap.duration),
            daemon=True,
        ).start()

    def _lyrics_worker(self, req_id, title, artist, album, duration):
        try:
            result = self.lyrics_provider.fetch(title, artist, album, duration)
        except Exception:
            result = Lyrics(status="error")
        self.ui_queue.put(("lyrics", req_id, result))

    def _on_lyrics(self, req_id, result):
        if req_id != self.lyrics_req_id:          # respuesta de una canción anterior
            return
        self.lyrics = result
        if result.status == "error":
            self.lyrics_track_id = None           # permite reintentar al reabrir el panel
        self._render_lyrics()

    def _render_lyrics(self):
        L = self.lyrics
        self.lyric_times = [t for t, _ in L.synced]
        self.current_lyric_idx = -2
        if L.status == "ok":
            if L.synced:
                self.lyrics_view.set_lines([line for _, line in L.synced], synced=True)
            else:
                self.lyrics_view.set_lines(L.plain.splitlines(), synced=False)
        elif L.status == "instrumental":
            self.lyrics_view.set_message("♪ Instrumental")
        elif L.status == "notfound":
            self.lyrics_view.set_message("Letra no encontrada.")
        elif L.status == "error":
            self.lyrics_view.set_message("No se pudo obtener la letra (¿sin conexión?).")
        else:
            self.lyrics_view.set_message("")

    def _update_lyrics_highlight(self, pos):
        if not self.lyric_times or not self.cfg.show_lyrics or self.cfg.is_mini:
            return
        idx = bisect.bisect_right(self.lyric_times, pos + LYRICS_OFFSET) - 1
        if idx == self.current_lyric_idx:
            return
        self.current_lyric_idx = idx
        self.lyrics_view.set_current(idx)

    def _on_lyric_line(self, idx):
        if 0 <= idx < len(self.lyric_times):
            self._seek_to(self.lyric_times[idx])
            self.lyrics_view.resume_follow()

    # ══════════════════════════════════════════════════════════════════════
    #  Cierre
    # ══════════════════════════════════════════════════════════════════════
    def closeEvent(self, e):
        self.on_close()
        e.accept()

    def on_close(self, *_):
        if not self.running:
            return
        self.running = False
        self.tick_timer.stop()
        self.save_timer.stop()
        self._save_now()
        self.backend.stop()
        self.audio.stop()
        if self.tray:
            self.tray.hide()
        QApplication.quit()


# ──────────────────────────────────────────────────────────────────────────────
#  Arranque (con instancia única mediante un socket local)
# ──────────────────────────────────────────────────────────────────────────────
TOOLTIP_QSS = ("QToolTip { color: #eeeeee; background-color: #2a2a2a; "
               "border: 1px solid #444444; padding: 3px 6px; }")


def main():
    sys.excepthook = log_exception            # PyQt6 aborta ante excepciones sin gestionar
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)   # 125 % / 150 % sin redondeos
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)      # la app sigue viva en la bandeja
    app.setStyleSheet(TOOLTIP_QSS)

    # Si ya hay una instancia, se le pide que se muestre y esta termina.
    sock = QLocalSocket()
    sock.connectToServer(APP_NAME)
    if sock.waitForConnected(250):
        sock.write(b"show")
        sock.waitForBytesWritten(250)
        sock.disconnectFromServer()
        return 0

    QLocalServer.removeServer(APP_NAME)
    server = QLocalServer()
    server.listen(APP_NAME)

    win = MiniPlayer()

    def on_second_instance():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readAll()
            conn.disconnectFromServer()
        win.restore_from_tray()

    server.newConnection.connect(on_second_instance)
    win.show()
    win._apply_topmost()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
