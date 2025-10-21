#!/usr/bin/env python3
import os
import io
import time
import threading
import requests
import sys
import json
import pygame
from pathlib import Path
from PIL import Image

# ================== CONFIG FICHIER ==================
CONFIG_PATH = Path(__file__).resolve().parent / "spotify_keys.json"

def load_config(path):
    if not path.exists():
        sys.exit(f"[ERROR] Fichier de configuration introuvable : {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        sys.exit(f"[ERROR] Impossible de lire {path}: {e}")
    required = ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI"]
    for k in required:
        if k not in cfg or not cfg[k]:
            sys.exit(f"[ERROR] Clé de configuration manquante: {k} dans {path}")
    if "SPOTIFY_SCOPE" not in cfg:
        cfg["SPOTIFY_SCOPE"] = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
    if "PC_HELPER_BASE" not in cfg:
        cfg["PC_HELPER_BASE"] = "http://192.168.0.102:5005"
    return cfg

cfg = load_config(CONFIG_PATH)
PC_HELPER_BASE = cfg["PC_HELPER_BASE"]

# Spotify (Spotipy)
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
SPOTIFY_CLIENT_ID     = cfg["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = cfg["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI  = cfg["SPOTIFY_REDIRECT_URI"]
SPOTIFY_SCOPE         = cfg["SPOTIFY_SCOPE"]

# ================== HARDWARE / UI ==================
BTN_PINS = {17:"B1_PREV", 27:"B2_PLAY", 22:"B3_NEXT", 5:"B4_MODE"}
ENC_A, ENC_B, ENC_SW = 6, 13, 19

W, H = 480, 800
FPS = 12
ICONS_PATH = str(Path(__file__).resolve().parent / "icons")
ROTATE_SCREEN = True

HTTP_TIMEOUT_S = 0.6
ART_TIMEOUT_S  = 1.2
SPOTIFY_POLL_S = 2.0
METRICS_POLL_S = 5.0
RESYNC_PROGRESS_S = 5.0

# ================== ENV SDL / Pygame ==================
os.environ["SDL_FBDEV"] = "/dev/fb0"
os.environ["SDL_MOUSEDRV"] = "TSLIB"
os.environ["SDL_MOUSEDEV"] = "/dev/input/touchscreen"

for driver in ["fbcon", "directfb", "kmsdrm", "x11"]:
    try:
        os.environ["SDL_VIDEODRIVER"] = driver
        import pygame as _pg_test
        _pg_test.display.init()
        print(f"Driver SDL utilisé : {driver}")
        break
    except Exception:
        print(f"Driver SDL non disponible : {driver}")
else:
    sys.exit("Aucun driver vidéo compatible trouvé. Essaie avec 'startx'.")

pygame.init()
screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
pygame.mouse.set_visible(False)
pygame.display.set_caption("PiPanel")

# Fonts
try:
    FONT  = pygame.font.SysFont("Inter", 26)
    BIG   = pygame.font.SysFont("Inter", 34, bold=True)
    SMALL = pygame.font.SysFont("Inter", 22)
except Exception:
    FONT  = pygame.font.Font(None, 26)
    BIG   = pygame.font.Font(None, 34)
    SMALL = pygame.font.Font(None, 22)

frame = pygame.Surface((W, H))
clock = pygame.time.Clock()

# ================== ICONES ==================
def load_icon(name, fallback_size=(48,48)):
    path = os.path.join(ICONS_PATH, name)
    try:
        img = pygame.image.load(path).convert_alpha()
        return img
    except Exception:
        s = pygame.Surface(fallback_size, pygame.SRCALPHA)
        pygame.draw.rect(s, (100,100,100,255), s.get_rect(), border_radius=6)
        return s

prev_icon  = load_icon("prev.png")
next_icon  = load_icon("next.png")
play_icon  = load_icon("play.png")
pause_icon = load_icon("pause.png")
mode_icon  = load_icon("mode.png")

# ================== ETAT PARTAGE ==================
state_lock = threading.Lock()
state = {
    "mode": "SPOTIFY",
    "now_title": "—",
    "now_artist": "—",
    "progress_ms": 0,
    "duration_ms": 1,
    "playing": False,
    "track_id": None,
    "art_surface": None,
    "bg_surface": None,
    "text_color": (255,255,255),
    "metrics": {}
}

# ================== COMMANDES PC ==================
def media_cmd(cmd):
    try:
        requests.post(f"{PC_HELPER_BASE}/media", json={"cmd": cmd}, timeout=HTTP_TIMEOUT_S)
    except Exception:
        pass

# ================== SPOTIFY ==================
CACHE_DIR = Path(__file__).resolve().parent
sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope=SPOTIFY_SCOPE,
    open_browser=False,
    cache_path=str(CACHE_DIR / ".cache")
))

def fetch_and_apply_artwork(url):
    try:
        data = requests.get(url, timeout=ART_TIMEOUT_S).content
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img = img.resize((300,300), Image.LANCZOS)
        surf = pygame.image.fromstring(img.tobytes(), img.size, img.mode)
        avg_color = img.resize((1,1)).getpixel((0,0))
        small_h = 200
        surf_bg = pygame.Surface((W, small_h))
        for y in range(small_h):
            ratio = y / float(small_h)
            r = int(avg_color[0] * (1 - ratio))
            g = int(avg_color[1] * (1 - ratio))
            b = int(avg_color[2] * (1 - ratio))
            pygame.draw.line(surf_bg, (r,g,b), (0,y), (W,y))
        surf_bg = pygame.transform.smoothscale(surf_bg, (W,H))
        luminance = 0.299*avg_color[0] + 0.587*avg_color[1] + 0.114*avg_color[2]
        tcolor = (255,255,255) if luminance < 128 else (20,20,20)
        with state_lock:
            state["art_surface"] = surf
            state["bg_surface"] = surf_bg
            state["text_color"] = tcolor
    except Exception:
        with state_lock:
            state["art_surface"] = None
            bg = pygame.Surface((W,H)); bg.fill((20,20,20))
            state["bg_surface"] = bg
            state["text_color"] = (255,255,255)

# ================== METRICS ==================
def poll_metrics():
    try:
        r = requests.get(f"{PC_HELPER_BASE}/metrics", timeout=HTTP_TIMEOUT_S)
        data = r.json() if r.status_code == 200 else {}
        with state_lock:
            state["metrics"] = data
    except Exception:
        pass

# ================== GPIO LOOP ==================
try:
    import RPi.GPIO as GPIO
except Exception as e:
    sys.exit(f"[ERROR] RPi.GPIO introuvable: {e}")

GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

for pin in BTN_PINS.keys():
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(ENC_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(ENC_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(ENC_SW, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def gpio_loop():
    last_btn = {pin: GPIO.input(pin) for pin in BTN_PINS}
    last_sw  = GPIO.input(ENC_SW)
    last_A   = GPIO.input(ENC_A)
    debounce_ms = 40
    last_time_btn = {pin: 0 for pin in BTN_PINS}
    last_time_sw  = 0

    while True:
        now_ms = int(time.time() * 1000)
        for pin, name in BTN_PINS.items():
            val = GPIO.input(pin)
            if val != last_btn[pin]:
                last_btn[pin] = val
                if val == 0 and (now_ms - last_time_btn[pin] > debounce_ms):
                    last_time_btn[pin] = now_ms
                    if name == "B1_PREV":
                        media_cmd("prev")
                    elif name == "B2_PLAY":
                        media_cmd("playpause")
                    elif name == "B3_NEXT":
                        media_cmd("next")
                    elif name == "B4_MODE":
                        with state_lock:
                            state["mode"] = "STATS" if state["mode"] == "SPOTIFY" else "SPOTIFY"

        sw = GPIO.input(ENC_SW)
        if sw != last_sw:
            last_sw = sw
            if sw == 0 and (now_ms - last_time_sw > debounce_ms):
                last_time_sw = now_ms
                media_cmd("playpause")

        A = GPIO.input(ENC_A)
        if A != last_A:
            last_A = A
            if A == 1:
                B = GPIO.input(ENC_B)
                if B == 0:
                    media_cmd("vol_up")
                else:
                    media_cmd("vol_down")
        time.sleep(0.0015)

# ================== RENDER HELPERS ==================
def blit_rotated():
    if ROTATE_SCREEN:
        rotated = pygame.transform.rotate(frame, 90)
        rect = rotated.get_rect(center=screen.get_rect().center)
        screen.blit(rotated, rect.topleft)
    else:
        screen.blit(frame, (0, 0))

def draw_progress_bar(surface, x, y, w, h, ratio):
    base_color = (25, 25, 25)
    fill_color = (30, 215, 96)
    pygame.draw.rect(surface, base_color, (x, y, w, h), border_radius=6)
    pygame.draw.rect(surface, fill_color, (x, y, int(w*ratio), h), border_radius=6)

def ms_str(ms):
    try:
        s = int(ms/1000)
        return f"{s//60}:{s%60:02d}"
    except Exception:
        return "0:00"

def render_spotify(surface):
    with state_lock:
        art = state["art_surface"]
        bg = state["bg_surface"]
        tcolor = state["text_color"]
        title = state["now_title"]
        artist = state["now_artist"]
        progress_ms = state["progress_ms"]
        duration_ms = state["duration_ms"]
        playing = state["playing"]

    if bg:
        surface.blit(bg, (0,0))
    else:
        surface.fill((10,10,10))

    mode_text = SMALL.render("Spotify", True, tcolor)
    surface.blit(mode_text, (W//2 - mode_text.get_width()//2, 20))

    if art:
        surface.blit(art, (W//2 - 150, 60))
    else:
        placeholder = SMALL.render("Chargement de l'album...", True, (200,200,200))
        surface.blit(placeholder, (W//2 - placeholder.get_width()//2, 220))

    title_s  = BIG.render(title, True, tcolor)
    artist_s = FONT.render(artist, True, tcolor)
    surface.blit(title_s,  (W//2 - title_s.get_width()//2, 380))
    surface.blit(artist_s, (W//2 - artist_s.get_width()//2, 410))

    try:
        ratio = min(1.0, max(0.0, float(progress_ms)/float(duration_ms)))
    except Exception:
        ratio = 0.0
    bar_y = 450
    draw_progress_bar(surface, 60, bar_y, W-120, 8, ratio)
    t1 = SMALL.render(ms_str(progress_ms), True, (230,230,230))
    t2 = SMALL.render(ms_str(duration_ms), True, (230,230,230))
    surface.blit(t1, (60, bar_y+10))
    surface.blit(t2, (W-60 - t2.get_width(), bar_y+10))

    center_y = 520
    surface.blit(prev_icon,   (W//2 - 150, center_y))
    surface.blit(pause_icon if playing else play_icon, (W//2 - 32, center_y))
    surface.blit(next_icon,   (W//2 + 86,  center_y))
    surface.blit(mode_icon, (W//2 - mode_icon.get_width()//2, 640))

def render_stats(surface):
    surface.fill((20,20,25))
    with state_lock:
        metrics = state.get("metrics", {})
    y = 100
    for label, key in [("CPU","cpu"),("GPU","gpu"),("Temp CPU (°C)","temp_cpu"),("Temp GPU (°C)","temp_gpu")]:
        val = str(metrics.get(key,"n/a"))
        line = BIG.render(f"{label}", True, (255,255,255))
        valr = BIG.render(f"{val}",   True, (200,200,200))
        surface.blit(line, (60, y))
        surface.blit(valr, (W-60 - valr.get_width(), y))
        y += 60
    info = SMALL.render("B4: Revenir à Spotify", True, (200,200,200))
    surface.blit(info, (W//2 - info.get_width()//2, H - 60))

# ================== THREAD LOGIQUE (Spotify + metrics) ==================
def logic_loop():
    last_spotify_poll = 0.0
    last_metrics_poll = 0.0
    last_resync = 0.0
    last_track_id = None

    while True:
        dt_ms = clock.get_time()
        with state_lock:
            if state["playing"]:
                state["progress_ms"] += dt_ms
                if state["progress_ms"] > state["duration_ms"]:
                    state["progress_ms"] = state["duration_ms"]

        now = time.time()

        if now - last_spotify_poll >= SPOTIFY_POLL_S:
            try:
                cur = sp.current_playback() or {}
                item = cur.get("item") or {}
                track_id = item.get("id")

                with state_lock:
                    state["now_title"] = item.get("name", "—")
                    state["now_artist"] = ", ".join([a["name"] for a in item.get("artists", [])]) if item else "—"
                    state["duration_ms"] = item.get("duration_ms") or 1
                    state["progress_ms"] = cur.get("progress_ms", state["progress_ms"])
                    state["playing"] = cur.get("is_playing", False)
                    state["track_id"] = track_id

                if track_id and track_id != last_track_id:
                    images = (item.get("album",{}).get("images") or [])
                    if images:
                        fetch_and_apply_artwork(images[0]["url"])
                    last_track_id = track_id
            except Exception:
                pass
            last_spotify_poll = now

        if now - last_resync >= RESYNC_PROGRESS_S:
            try:
                cur = sp.current_playback() or {}
                with state_lock:
                    state["progress_ms"] = cur.get("progress_ms", state["progress_ms"])
                    it = cur.get("item") or {}
                    if it:
                        state["duration_ms"] = it.get("duration_ms", state["duration_ms"])
            except Exception:
                pass
            last_resync = now

        if now - last_metrics_poll >= METRICS_POLL_S:
            poll_metrics()
            last_metrics_poll = now

        time.sleep(0.2)

# ================== BOUCLE RENDU (pygame) ==================
def render_loop():
    with state_lock:
        if state["bg_surface"] is None:
            bg = pygame.Surface((W,H)); bg.fill((20,20,20))
            state["bg_surface"] = bg

    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                pygame.quit(); sys.exit()

        frame.fill((0,0,0))
        with state_lock:
            m = state["mode"]
        if m == "SPOTIFY":
            render_spotify(frame)
        else:
            render_stats(frame)

        screen.fill((0,0,0))
        blit_rotated()
        pygame.display.update()
        clock.tick(FPS)

# ================== LANCEMENT ==================
if __name__ == "__main__":
    t_gpio = threading.Thread(target=gpio_loop, daemon=True)
    t_gpio.start()

    t_logic = threading.Thread(target=logic_loop, daemon=True)
    t_logic.start()

    render_loop()
