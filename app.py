import asyncio
import threading
import time
import os
import sys
from datetime import datetime, timezone
from io import BytesIO
import customtkinter as ctk
from PIL import Image
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus
)
from winrt.windows.storage.streams import DataReader
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

ctk.set_appearance_mode("Dark")

class MediaWidgetApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        # --- MEMORIA DE TAMAÑOS PERSONALIZADOS ---
        self.normal_w, self.normal_h = 460, 180
        self.mini_w, self.mini_h = 140, 160

        self.title("Mini Reproductor Windows")
        self.geometry(f"{self.normal_w}x{self.normal_h}")
        self.overrideredirect(True)
        self.attributes("-topmost", True)

        self.last_track_id = ""
        self.current_source_app_id = ""
        self.is_mini = False
        self.current_pil_image = None
        self.is_playing_state = False

        # --- CARGA DE IMÁGENES PIL BASE PARA ESCALADO DINÁMICO ---
        self.pil_play = self.load_pil_image("assets/btn_play.png")
        self.pil_pause = self.load_pil_image("assets/btn_pause.png")
        self.pil_prev = self.load_pil_image("assets/btn_prev.png")
        self.pil_next = self.load_pil_image("assets/btn_next.png")

        # Imágenes tk iniciales
        self.img_play = ctk.CTkImage(self.pil_play, size=(32, 32)) if self.pil_play else None
        self.img_pause = ctk.CTkImage(self.pil_pause, size=(32, 32)) if self.pil_pause else None
        self.img_prev = ctk.CTkImage(self.pil_prev, size=(24, 24)) if self.pil_prev else None
        self.img_next = ctk.CTkImage(self.pil_next, size=(24, 24)) if self.pil_next else None

        self.img_play_mini = ctk.CTkImage(self.pil_play, size=(22, 22)) if self.pil_play else None
        self.img_pause_mini = ctk.CTkImage(self.pil_pause, size=(22, 22)) if self.pil_pause else None
        self.img_prev_mini = ctk.CTkImage(self.pil_prev, size=(16, 16)) if self.pil_prev else None
        self.img_next_mini = ctk.CTkImage(self.pil_next, size=(16, 16)) if self.pil_next else None

        # --- BOTÓN CERRAR GLOBAL FIJO ---
        self.btn_close = ctk.CTkButton(self, text="✕", width=25, height=25, 
                                       corner_radius=0, fg_color="transparent", text_color="#aaaaaa",
                                       hover_color="#ff4444", command=self.on_close)
        self.btn_close.place(relx=1.0, rely=0.0, x=-25, y=0) 

        # --- TIRADOR DE REDIMENSIÓN ---
        self.grip = ctk.CTkLabel(self, text="◢", font=("Segoe UI", 10), text_color="#666666")
        self.grip.place(relx=1.0, rely=1.0, x=-5, y=-5, anchor="se")
        self.grip.bind("<ButtonPress-1>", self.start_resize)
        self.grip.bind("<B1-Motion>", self.do_resize)

        # ==========================================
        #           VISTA 1: MODO NORMAL
        # ==========================================
        self.normal_view = ctk.CTkFrame(self, corner_radius=12, border_width=1, border_color="#333333")
        self.normal_view.pack(fill="both", expand=True)

        self.cover_frame = ctk.CTkFrame(self.normal_view, width=100, height=100, corner_radius=8)
        self.cover_frame.pack(side="left", padx=12, pady=12)
        self.cover_frame.pack_propagate(False)
        self.lbl_cover = ctk.CTkLabel(self.cover_frame, text="🎵", font=("Segoe UI", 20))
        self.lbl_cover.pack(fill="both", expand=True)

        self.info_frame = ctk.CTkFrame(self.normal_view, fg_color="transparent")
        self.info_frame.pack(side="right", fill="both", expand=True, padx=(0, 15), pady=10)

        self.lbl_title = ctk.CTkLabel(self.info_frame, text="Cargando...", font=("Segoe UI", 14, "bold"), anchor="w", wraplength=280, justify="left")
        self.lbl_title.pack(fill="x", pady=(2, 0))
        self.lbl_artist = ctk.CTkLabel(self.info_frame, text="--", font=("Segoe UI", 11), text_color="#aaaaaa", anchor="w", justify="left")
        self.lbl_artist.pack(fill="x", pady=(0, 4))

        self.time_frame = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        self.time_frame.pack(fill="x")
        self.lbl_elapsed = ctk.CTkLabel(self.time_frame, text="00:00", font=("Segoe UI", 10), text_color="#aaaaaa")
        self.lbl_elapsed.pack(side="left")
        self.lbl_remaining = ctk.CTkLabel(self.time_frame, text="-00:00", font=("Segoe UI", 10), text_color="#aaaaaa")
        self.lbl_remaining.pack(side="right")

        self.progress_bar = ctk.CTkProgressBar(self.info_frame, height=5)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=2)

        self.controls_frame = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        self.controls_frame.pack(fill="x", pady=(2, 0))

        self.btn_prev = ctk.CTkButton(self.controls_frame, text="⏮" if not self.img_prev else "", image=self.img_prev, width=32, fg_color="transparent", command=lambda: self.send_media_command("prev"))
        self.btn_play = ctk.CTkButton(self.controls_frame, text="⏸" if not self.img_pause else "", image=self.img_pause, width=40, fg_color="transparent", command=lambda: self.send_media_command("play_pause"))
        self.btn_next = ctk.CTkButton(self.controls_frame, text="⏭" if not self.img_next else "", image=self.img_next, width=32, fg_color="transparent", command=lambda: self.send_media_command("next"))

        self.btn_prev.pack(side="left", padx=2, expand=True)
        self.btn_play.pack(side="left", padx=2, expand=True)
        self.btn_next.pack(side="left", padx=2, expand=True)

        self.vol_frame = ctk.CTkFrame(self.info_frame, fg_color="transparent")
        self.vol_frame.pack(fill="x", pady=(4, 0))

        self.lbl_vol_icon = ctk.CTkLabel(self.vol_frame, text="🔊", font=("Segoe UI", 10), text_color="#aaaaaa")
        self.lbl_vol_icon.pack(side="left", padx=(0, 4))

        self.slider_vol = ctk.CTkSlider(self.vol_frame, from_=0, to=1, height=10, command=self.on_volume_change)
        self.slider_vol.set(0.8)
        self.slider_vol.pack(side="left", fill="x", expand=True)

        # ==========================================
        #           VISTA 2: MODO MINI
        # ==========================================
        self.mini_view = ctk.CTkFrame(self, corner_radius=12, border_width=1, border_color="#333333")
        
        self.cover_frame_mini = ctk.CTkFrame(self.mini_view, width=80, height=80, corner_radius=6)
        self.cover_frame_mini.pack(pady=(10, 4))
        self.cover_frame_mini.pack_propagate(False)

        self.lbl_cover_mini = ctk.CTkLabel(self.cover_frame_mini, text="🎵", font=("Segoe UI", 16))
        self.lbl_cover_mini.pack(fill="both", expand=True)

        self.progress_bar_mini = ctk.CTkProgressBar(self.mini_view, height=3, width=110)
        self.progress_bar_mini.set(0)
        self.progress_bar_mini.pack(pady=2)

        self.controls_mini = ctk.CTkFrame(self.mini_view, fg_color="transparent")
        self.controls_mini.pack(pady=(2, 1))

        self.btn_prev_mini = ctk.CTkButton(self.controls_mini, text="⏮" if not self.img_prev_mini else "", image=self.img_prev_mini, width=22, fg_color="transparent", command=lambda: self.send_media_command("prev"))
        self.btn_play_mini = ctk.CTkButton(self.controls_mini, text="⏸" if not self.img_pause_mini else "", image=self.img_pause_mini, width=28, fg_color="transparent", command=lambda: self.send_media_command("play_pause"))
        self.btn_next_mini = ctk.CTkButton(self.controls_mini, text="⏭" if not self.img_next_mini else "", image=self.img_next_mini, width=22, fg_color="transparent", command=lambda: self.send_media_command("next"))

        self.btn_prev_mini.pack(side="left", padx=1)
        self.btn_play_mini.pack(side="left", padx=2)
        self.btn_next_mini.pack(side="left", padx=1)

        self.vol_frame_mini = ctk.CTkFrame(self.mini_view, fg_color="transparent")
        self.vol_frame_mini.pack(pady=(1, 6))

        self.lbl_vol_icon_mini = ctk.CTkLabel(self.vol_frame_mini, text="🔊", font=("Segoe UI", 9), text_color="#aaaaaa")
        self.lbl_vol_icon_mini.pack(side="left", padx=(0, 2))

        self.slider_vol_mini = ctk.CTkSlider(self.vol_frame_mini, from_=0, to=1, height=8, width=80, command=self.on_volume_change)
        self.slider_vol_mini.set(0.8)
        self.slider_vol_mini.pack(side="left")

        # --- EVENTOS PARA ARRASTRAR Y MODO MINI ---
        for widget in [self.normal_view, self.lbl_cover, self.lbl_title, self.lbl_artist, self.mini_view, self.lbl_cover_mini]:
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

        self.lbl_cover.bind("<Double-Button-1>", self.toggle_mini_mode)
        self.lbl_cover_mini.bind("<Double-Button-1>", self.toggle_mini_mode)

        self.btn_close.lift()
        self.grip.lift()

        self.running = True
        self.thread = threading.Thread(target=self.bg_update_loop, daemon=True)
        self.thread.start()

    def load_pil_image(self, path):
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        full_path = os.path.join(base_path, path)
        if os.path.exists(full_path):
            return Image.open(full_path).convert("RGBA")
        return None

    # --- REDIMENSIÓN CON SEGUIMIENTO DE CURSOR EXACTO ---
    def start_resize(self, event):
        # Guardamos la posición absoluta en pantalla del borde superior izquierdo
        self.win_x = self.winfo_x()
        self.win_y = self.winfo_y()

    def do_resize(self, event):
        # La nueva dimensión es directamente la posición del ratón menos la esquina de origen
        new_w = event.x_root - self.win_x
        new_h = event.y_root - self.win_y
        
        if self.is_mini:
            min_w, min_h = 120, 130
            new_w = max(min_w, new_w)
            new_h = max(min_h, new_h)
            self.mini_w, self.mini_h = new_w, new_h
        else:
            min_w, min_h = 360, 140
            new_w = max(min_w, new_w)
            new_h = max(min_h, new_h)
            self.normal_w, self.normal_h = new_w, new_h

        self.geometry(f"{new_w}x{new_h}")
        self.update_cover_scaling(new_w, new_h)

    def update_cover_scaling(self, w=None, h=None):
        """Ajusta dinámicamente la carátula y los botones según el tamaño actual."""
        if w is None or h is None:
            w, h = (self.mini_w, self.mini_h) if self.is_mini else (self.normal_w, self.normal_h)

        self.geometry(f"{w}x{h}")

        if self.is_mini:
            # En modo mini flexible, calculamos la portada según el espacio vertical y horizontal disponible
            cover_size = max(40, min(w - 24, h - 80))
            self.cover_frame_mini.configure(width=cover_size, height=cover_size)
            
            # Escalado horizontal libre para barras de control
            self.progress_bar_mini.configure(width=max(60, w - 24))
            self.slider_vol_mini.configure(width=max(50, w - 50))

            # Factor de escala visual suave para botones
            scale = max(0.8, w / 140.0)
            play_icon_sz = max(16, int(22 * scale))
            nav_icon_sz = max(12, int(16 * scale))

            if self.pil_play and self.pil_pause and self.pil_prev and self.pil_next:
                self.img_play_mini = ctk.CTkImage(self.pil_play, size=(play_icon_sz, play_icon_sz))
                self.img_pause_mini = ctk.CTkImage(self.pil_pause, size=(play_icon_sz, play_icon_sz))
                self.img_prev_mini = ctk.CTkImage(self.pil_prev, size=(nav_icon_sz, nav_icon_sz))
                self.img_next_mini = ctk.CTkImage(self.pil_next, size=(nav_icon_sz, nav_icon_sz))

                active_img = self.img_pause_mini if self.is_playing_state else self.img_play_mini
                self.btn_play_mini.configure(image=active_img, width=int(28 * scale))
                self.btn_prev_mini.configure(image=self.img_prev_mini, width=int(22 * scale))
                self.btn_next_mini.configure(image=self.img_next_mini, width=int(22 * scale))

            if self.current_pil_image:
                ctk_img = ctk.CTkImage(light_image=self.current_pil_image, dark_image=self.current_pil_image, size=(cover_size, cover_size))
                self.lbl_cover_mini.configure(image=ctk_img, text="")
            else:
                self.lbl_cover_mini.configure(image=None, text="🎵")
        else:
            cover_size = max(60, h - 24)
            self.cover_frame.configure(width=cover_size, height=cover_size)
            self.lbl_title.configure(wraplength=max(150, w - cover_size - 60))
            if self.current_pil_image:
                ctk_img = ctk.CTkImage(light_image=self.current_pil_image, dark_image=self.current_pil_image, size=(cover_size, cover_size))
                self.lbl_cover.configure(image=ctk_img, text="")
            else:
                self.lbl_cover.configure(image=None, text="🎵")

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.winfo_x() + deltax
        y = self.winfo_y() + deltay
        self.geometry(f"+{x}+{y}")

    def toggle_mini_mode(self, event):
        if self.is_mini:
            self.mini_view.pack_forget()
            self.geometry(f"{self.normal_w}x{self.normal_h}")
            self.normal_view.pack(fill="both", expand=True)
            self.btn_close.place(relx=1.0, rely=0.0, x=-25, y=0)
            self.btn_close.lift()
            self.grip.lift()
            self.is_mini = False
            self.update_cover_scaling(self.normal_w, self.normal_h)
        else:
            self.normal_view.pack_forget()
            self.geometry(f"{self.mini_w}x{self.mini_h}")
            self.mini_view.pack(fill="both", expand=True)
            self.btn_close.place(relx=1.0, rely=0.0, x=-22, y=2)
            self.btn_close.lift()
            self.grip.lift()
            self.is_mini = True
            self.update_cover_scaling(self.mini_w, self.mini_h)

    def on_volume_change(self, val):
        vol_val = float(val)
        self.slider_vol.set(vol_val)
        self.slider_vol_mini.set(vol_val)
        threading.Thread(target=self._apply_volume, args=(vol_val,), daemon=True).start()

    def _apply_volume(self, val):
        try:
            sessions = AudioUtilities.GetAllSessions()
            app_id = getattr(self, "current_source_app_id", "").lower()
            for session in sessions:
                if session.Process:
                    p_name = session.Process.name().lower()
                    p_stem = p_name.replace(".exe", "")
                    if app_id and (p_stem in app_id or p_name in app_id or app_id.startswith(p_stem)):
                        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                        volume.SetMasterVolume(val, None)
        except Exception:
            pass

    def sync_app_volume(self):
        try:
            sessions = AudioUtilities.GetAllSessions()
            app_id = getattr(self, "current_source_app_id", "").lower()
            for session in sessions:
                if session.Process:
                    p_name = session.Process.name().lower()
                    p_stem = p_name.replace(".exe", "")
                    if app_id and (p_stem in app_id or p_name in app_id or app_id.startswith(p_stem)):
                        volume = session._ctl.QueryInterface(ISimpleAudioVolume)
                        current_vol = volume.GetMasterVolume()
                        self.slider_vol.set(current_vol)
                        self.slider_vol_mini.set(current_vol)
                        break
        except Exception:
            pass

    def apply_adaptive_colors(self, pil_image):
        if not pil_image:
            return
        img1x1 = pil_image.resize((1, 1))
        color = img1x1.getpixel((0, 0))
        r, g, b = color[:3]

        accent_hex = f"#{r:02x}{g:02x}{b:02x}"
        bg_hex = f"#{int(r*0.15):02x}{int(g*0.15):02x}{int(b*0.15):02x}"

        self.normal_view.configure(fg_color=bg_hex)
        self.mini_view.configure(fg_color=bg_hex)
        self.progress_bar.configure(progress_color=accent_hex)
        self.progress_bar_mini.configure(progress_color=accent_hex)
        self.slider_vol.configure(progress_color=accent_hex)
        self.slider_vol_mini.configure(progress_color=accent_hex)

    def bg_update_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        async def run_periodic():
            while self.running:
                try:
                    data = await self.fetch_media_info()
                    self.after(0, self.update_ui, data)
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        self.loop.run_until_complete(run_periodic())

    def send_media_command(self, cmd):
        if hasattr(self, 'loop'):
            asyncio.run_coroutine_threadsafe(self.async_media_command(cmd), self.loop)

    async def async_media_command(self, cmd):
        sessions = await MediaManager.request_async()
        current = sessions.get_current_session()
        if current:
            if cmd == "play_pause":
                await current.try_toggle_play_pause_async()
            elif cmd == "next":
                await current.try_skip_next_async()
            elif cmd == "prev":
                await current.try_skip_previous_async()

    async def fetch_media_info(self):
        sessions = await MediaManager.request_async()
        current_session = sessions.get_current_session()
        if not current_session:
            return None

        self.current_source_app_id = current_session.source_app_user_model_id if current_session.source_app_user_model_id else ""

        info = await current_session.try_get_media_properties_async()
        timeline = current_session.get_timeline_properties()
        playback = current_session.get_playback_info()

        title = info.title if info.title else "Sin título"
        artist = info.artist if info.artist else "Desconocido"

        pos, total = 0.0, 0.0
        is_playing = (playback and playback.playback_status == PlaybackStatus.PLAYING)

        if timeline and timeline.end_time.total_seconds() > 0:
            total = timeline.end_time.total_seconds()
            base_pos = timeline.position.total_seconds()
            if is_playing and timeline.last_updated_time:
                try:
                    last_update = timeline.last_updated_time
                    now = datetime.now(timezone.utc) if last_update.tzinfo else datetime.now()
                    elapsed = (now - last_update).total_seconds()
                    pos = base_pos + elapsed if 0 <= elapsed < 3600 else base_pos
                except:
                    pos = base_pos
            else:
                pos = base_pos
            pos = min(pos, total)

        track_id = f"{title}_{artist}"
        pil_image = None
        if track_id != self.last_track_id and info.thumbnail:
            try:
                stream = await info.thumbnail.open_read_async()
                size = stream.size
                if size > 0:
                    reader = DataReader(stream)
                    await reader.load_async(size)
                    buffer = bytearray(size)
                    reader.read_bytes(buffer)
                    pil_image = Image.open(BytesIO(buffer)).convert("RGBA")
            except:
                pass

        return {
            "track_id": track_id, "title": title, "artist": artist,
            "pos": pos, "total": total, "is_playing": is_playing, "pil_image": pil_image
        }

    def update_ui(self, data):
        if not data:
            self.lbl_title.configure(text="Sin reproducción activa")
            self.lbl_artist.configure(text="Abre Spotify o YouTube")
            self.progress_bar.set(0)
            self.progress_bar_mini.set(0)
            if not self.img_play:
                self.btn_play.configure(text="▶")
                self.btn_play_mini.configure(text="▶")
            return

        self.lbl_title.configure(text=data["title"])
        self.lbl_artist.configure(text=data["artist"])
        self.is_playing_state = data["is_playing"]
        
        if data["is_playing"]:
            if self.img_pause:
                self.btn_play.configure(image=self.img_pause)
                self.btn_play_mini.configure(image=self.img_pause_mini)
            else:
                self.btn_play.configure(text="⏸")
                self.btn_play_mini.configure(text="⏸")
        else:
            if self.img_play:
                self.btn_play.configure(image=self.img_play)
                self.btn_play_mini.configure(image=self.img_play_mini)
            else:
                self.btn_play.configure(text="▶")
                self.btn_play_mini.configure(text="▶")

        if data["track_id"] != self.last_track_id:
            self.last_track_id = data["track_id"]
            self.current_pil_image = data["pil_image"]
            self.sync_app_volume()
            if data["pil_image"]:
                self.apply_adaptive_colors(data["pil_image"])
            else:
                self.lbl_cover.configure(image=None, text="🎵")
                self.lbl_cover_mini.configure(image=None, text="🎵")
            
            self.update_cover_scaling()

        pos, total = data["pos"], data["total"]
        if total > 0:
            prog = min(max(pos / total, 0.0), 1.0)
            self.progress_bar.set(prog)
            self.progress_bar_mini.set(prog)
            pos_m, pos_s = divmod(int(pos), 60)
            rem_m, rem_s = divmod(int(max(0, total - pos)), 60)
            self.lbl_elapsed.configure(text=f"{pos_m:02d}:{pos_s:02d}")
            self.lbl_remaining.configure(text=f"-{rem_m:02d}:{rem_s:02d}")

    def on_close(self):
        self.running = False
        self.destroy()

if __name__ == "__main__":
    app = MediaWidgetApp()
    app.mainloop()