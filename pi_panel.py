#!/usr/bin/env python3
import os, io, time, threading, requests, sys, json
import pygame
from pathlib import Path
from PIL import Image
from gpiozero import RotaryEncoder, Button
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

# ------------ LIRE LA CONFIG ------------
# Le fichier config.json doit se trouver dans le même dossier que ce script.
CONFIG_PATH = Path(__file__).resolve().parent / "spotify_keys.json"

def load_config(path):
    if not path.exists():
        sys.exit(f"[ERROR] Fichier de configuration introuvable : {path}\nCrée un fichier 'config.json' dans le même dossier (voir exemple).")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        sys.exit(f"[ERROR] Impossible de lire {path}: {e}")
    # valeurs attendues
    required = ["SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI"]
    for k in required:
        if k not in cfg or not cfg[k]:
            sys.exit(f"[ERROR] Clé de configuration manquante: {k} dans {path}")
    # scope optionnel — si non présent on prend la valeur par défaut
    if "SPOTIFY_SCOPE" not in cfg:
        cfg["SPOTIFY_SCOPE"] = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
    # PC_HELPER_BASE optionnel
    if "PC_HELPER_BASE" not in cfg:
        cfg["PC_HELPER_BASE"] = "http://192.168.0.102:5005"
    return cfg

cfg = load_config(CONFIG_PATH)

# ------------ CONFIG ------------
PC_HELPER_BASE = cfg["PC_HELPER_BASE"]
SPOTIFY_CLIENT_ID     = cfg["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = cfg["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI  = cfg["SPOTIFY_REDIRECT_URI"]
SPOTIFY_SCOPE = cfg["SPOTIFY_SCOPE"]

# GPIO PINS
BTN_PINS = {17:"B1_PREV", 27:"B2_PLAY", 22:"B3_NEXT", 5:"B4_MODE"}
ENC_A, ENC_B, ENC_SW = 6, 13, 19

# UI CONFIG
W, H = 480, 800
FPS = 12
ICONS_PATH = "/home/giovanni/Code/Controller DIY/icons"

# Configuration affichage Pygame selon le driver dispo
os.environ["SDL_FBDEV"] = "/dev/fb0"
os.environ["SDL_MOUSEDRV"] = "TSLIB"
os.environ["SDL_MOUSEDEV"] = "/dev/input/touchscreen"

for driver in ["fbcon", "directfb", "kmsdrm", "x11"]:
    try:
        os.environ["SDL_VIDEODRIVER"] = driver
        import pygame
        pygame.display.init()
        print(f"Driver SDL utilisé : {driver}")
        break
    except pygame.error:
        print(f"river SDL non disponible : {driver}")
else:
    sys.exit("Aucun driver vidéo compatible trouvé. Essaie avec 'startx'.")

# --------------------------------
pygame.init()
screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
ROTATE_SCREEN = True  # active ou désactive la rotation

# ICONS
prev_icon  = pygame.image.load(os.path.join(ICONS_PATH, "prev.png")).convert_alpha()
next_icon  = pygame.image.load(os.path.join(ICONS_PATH, "next.png")).convert_alpha()
play_icon  = pygame.image.load(os.path.join(ICONS_PATH, "play.png")).convert_alpha()
pause_icon = pygame.image.load(os.path.join(ICONS_PATH, "pause.png")).convert_alpha()
mode_icon = pygame.image.load(os.path.join(ICONS_PATH, "mode.png")).convert_alpha()

frame = pygame.Surface((W, H))

def blit_rotated():
    """Dessine la surface 'frame' sur l'écran en la tournant si nécessaire"""
    if ROTATE_SCREEN:
        rotated = pygame.transform.rotate(frame, 90)  # rotation sens horaire
        rect = rotated.get_rect(center=screen.get_rect().center)
        screen.blit(rotated, rect.topleft)
    else:
        screen.blit(frame, (0, 0))


pygame.mouse.set_visible(False)
pygame.display.set_caption("PiPanel")

# Affichage initial avant chargement Spotify
screen.fill((18, 18, 18))
pygame.display.flip()

FONT = pygame.font.SysFont("Inter", 26)
BIG  = pygame.font.SysFont("Inter", 34, bold=True)
SMALL = pygame.font.SysFont("Inter", 22)

text = BIG.render("Connexion à Spotify...", True, (200, 200, 200))
screen.blit(text, (W//2 - text.get_width()//2, H//2 - text.get_height()//2))
pygame.display.flip()

# ---------- GPIO SETUP ----------
encoder = RotaryEncoder(a=ENC_A, b=ENC_B, max_steps=0)
encoder_button = Button(ENC_SW, pull_up=True, bounce_time=0.2)
buttons = {pin: Button(pin, pull_up=True, bounce_time=0.2) for pin in BTN_PINS}

# ---------- Spotify setup ----------
CACHE_DIR = Path(__file__).resolve().parent

sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope=SPOTIFY_SCOPE,
    open_browser=False,
    cache_path=str(CACHE_DIR / ".cache")
))


# ---------- State ----------
mode = "SPOTIFY"
art_surface = None
bg_surface = None
now_title = "—"
now_artist = "—"
progress_ms = 0
duration_ms = 1
playing = False
text_color = (255, 255, 255)

# ---------- Helpers ----------
def http_post(path, payload):
    try:
        requests.post(f"{PC_HELPER_BASE}{path}", json=payload, timeout=0.3)
    except:
        pass

def media_cmd(cmd):
    http_post("/media", {"cmd": cmd})

def ms_str(ms):
    s = int(ms/1000)
    return f"{s//60}:{s%60:02d}"

# ---------- Spotify & visuals ----------
def create_gradient_bg(color):
    small_h = 200  # réduit
    surf = pygame.Surface((W, small_h))
    for y in range(small_h):
        ratio = y / small_h
        r = int(color[0] * (1 - ratio))
        g = int(color[1] * (1 - ratio))
        b = int(color[2] * (1 - ratio))
        pygame.draw.line(surf, (r,g,b), (0,y), (W,y))
    return pygame.transform.smoothscale(surf, (W, H))


def fetch_art(url):
    global art_surface, bg_surface, text_color
    try:
        img_data = requests.get(url, timeout=2).content
        img = Image.open(io.BytesIO(img_data)).convert("RGB")
        img = img.resize((300, 300), Image.LANCZOS)
        art_surface = pygame.image.fromstring(img.tobytes(), img.size, img.mode)

        # --- extraire la couleur dominante ---
        tmp_path = "/tmp/art.jpg"
        with open(tmp_path, "wb") as f:
            f.write(img_data)
        avg_color = img.resize((1,1)).getpixel((0,0))
        bg_surface = create_gradient_bg(avg_color)

        # --- choisir couleur du texte selon la luminosité du fond ---
        def is_dark_color(c):
            r, g, b = c
            luminance = 0.299*r + 0.587*g + 0.114*b
            return luminance < 128

        if is_dark_color(avg_color):
            text_color = (255, 255, 255)  # texte clair sur fond sombre
        else:
            text_color = (20, 20, 20)     # texte foncé sur fond clair

    except Exception:
        art_surface = None
        bg_surface = pygame.Surface((W, H))
        bg_surface.fill((20, 20, 20))
        text_color = (255, 255, 255)



def spotify_loop():
    global now_title, now_artist, progress_ms, duration_ms, playing
    last_track_id = None
    print("Thread Spotify démarré...")
    while True:
        try:
            cur = sp.current_playback() or {}
            item = cur.get("item") or {}
            now_title = item.get("name","—")
            now_artist = ", ".join([a["name"] for a in item.get("artists",[])]) if item else "—"
            duration_ms = (item.get("duration_ms") or 1)
            progress_ms = cur.get("progress_ms", 0)
            playing = cur.get("is_playing", False)
            track_id = item.get("id")
            if track_id and track_id != last_track_id:
                images = (item.get("album",{}).get("images") or [])
                if images:
                    threading.Thread(target=fetch_art, args=(images[0]["url"],), daemon=True).start()
                last_track_id = track_id
        except Exception:
            pass
        time.sleep(2)

threading.Thread(target=spotify_loop, daemon=True).start()

# ---------- Rendering ----------
def draw_progress_bar(x, y, w, h, ratio):
    base_color = (25, 25, 25)
    fill_color = (30, 215, 96)  # vert Spotify
    pygame.draw.rect(frame, base_color, (x, y, w, h), border_radius=6)
    pygame.draw.rect(frame, fill_color, (x, y, int(w * ratio), h), border_radius=6)


def render_spotify():
    global last_bg_surface, fade_start, fade_from, fade_to

    # Gestion du changement de fond (transition douce)
    if bg_surface != last_bg_surface:
        fade_start = pygame.time.get_ticks()
        fade_from = last_bg_surface
        fade_to = bg_surface
        last_bg_surface = bg_surface

    # Calcul du fondu
    if fade_from and fade_to:
        elapsed = pygame.time.get_ticks() - fade_start
        alpha = min(1.0, elapsed / fade_duration)
        if alpha < 1.0:
            # interpolation visuelle entre les 2 fonds
            blended = pygame.Surface((W, H)).convert()
            fade_from_scaled = fade_from.copy()
            fade_to_scaled = fade_to.copy()
            fade_to_scaled.set_alpha(int(255 * alpha))
            blended.blit(fade_from_scaled, (0, 0))
            blended.blit(fade_to_scaled, (0, 0))
            frame.blit(blended, (0, 0))
        else:
            frame.blit(fade_to, (0, 0))
            fade_from = None  # transition terminée
    else:
        # Fond statique
        if bg_surface:
            frame.blit(bg_surface, (0, 0))
        else:
            frame.fill((0, 0, 0))


    # nom du mode
    mode_text = SMALL.render("Spotify", True, text_color)
    frame.blit(mode_text, (W//2 - mode_text.get_width()//2, 20))

    # pochette
    if art_surface:
        frame.blit(art_surface, (W//2 - 150, 60))
    else:
        placeholder = SMALL.render("Chargement de l'album...", True, (200,200,200))
        frame.blit(placeholder, (W//2 - placeholder.get_width()//2, 220))

    # titre + artiste
    title = BIG.render(now_title, True, text_color)
    artist = FONT.render(now_artist, True, text_color)
    frame.blit(title, (W//2 - title.get_width()//2, 380))
    frame.blit(artist, (W//2 - artist.get_width()//2, 410))

    # barre progression
    ratio = min(1.0, max(0.0, progress_ms / float(duration_ms)))
    bar_y = 450
    draw_progress_bar(60, bar_y, W-120, 8, ratio)
    t1 = SMALL.render(ms_str(progress_ms), True, (230,230,230))
    t2 = SMALL.render(ms_str(duration_ms), True, (230,230,230))
    frame.blit(t1, (60, bar_y+10))
    frame.blit(t2, (W-60 - t2.get_width(), bar_y+10))

    center_y = 520
    frame.blit(prev_icon,  (W//2 - 150, center_y))
    if playing:
        frame.blit(pause_icon, (W//2 - 32, center_y))
    else:
        frame.blit(play_icon, (W//2 - 32, center_y))
    frame.blit(next_icon,  (W//2 + 86, center_y))

    # bouton mode centré en bas
    frame.blit(mode_icon, (W//2 - mode_icon.get_width()//2, 640))

def render_stats():
    screen.fill((20,20,25))
    try:
        data = requests.get(f"{PC_HELPER_BASE}/metrics", timeout=0.8).json()
    except Exception:
        data = {"cpu":"n/a","gpu":"n/a","temp_cpu":"n/a","temp_gpu":"n/a"}

    y = 100
    for label, key in [("CPU","cpu"),("GPU","gpu"),("Temp CPU (°C)","temp_cpu"),("Temp GPU (°C)","temp_gpu")]:
        val = str(data.get(key,"n/a"))
        line = BIG.render(f"{label}", True, (255,255,255))
        valr = BIG.render(f"{val}", True, (200,200,200))
        frame.blit(line, (60, y))
        frame.blit(valr, (W-60 - valr.get_width(), y))
        y += 60

    info = SMALL.render("B4: Revenir à Spotify", True, (200,200,200))
    frame.blit(info, (W//2 - info.get_width()//2, H - 60))

# ---------- Controls ----------
def on_rotate():
    delta = encoder.steps
    encoder.steps = 0
    if delta > 0:
        media_cmd("vol_down")
    elif delta < 0:
        media_cmd("vol_up")

def on_click():
    media_cmd("playpause")

def on_button_press(pin):
    global mode
    name = BTN_PINS[pin]
    if name == "B1_PREV":
        media_cmd("prev")
    elif name == "B2_PLAY":
        media_cmd("playpause")
    elif name == "B3_NEXT":
        media_cmd("next")
    elif name == "B4_MODE":
        mode = "STATS" if mode == "SPOTIFY" else "SPOTIFY"

encoder.when_rotated = on_rotate
encoder_button.when_pressed = on_click
for pin, btn in buttons.items():
    btn.when_pressed = lambda b=pin: on_button_press(b)

# ---------- MAIN LOOP ----------
fade_start = 0
fade_duration = 500  # en ms
fade_from = None
fade_to = None

last_bg_surface = None
clock = pygame.time.Clock()
last_sync = time.time()

try:
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                raise KeyboardInterrupt
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                raise KeyboardInterrupt

        # --- dessin principal ---
        if mode == "SPOTIFY":
            dt = clock.get_time()

            # progression fluide
            if playing:
                progress_ms += dt
                if progress_ms > duration_ms:
                    progress_ms = duration_ms

            # resync toutes les 5 secondes
            if time.time() - last_sync > 5:
                try:
                    cur = sp.current_playback()
                    if cur:
                        progress_ms = cur.get("progress_ms", progress_ms)
                        duration_ms = (cur.get("item") or {}).get("duration_ms", duration_ms)
                    last_sync = time.time()
                except Exception:
                    pass

            render_spotify()

        else:
            render_stats()

        screen.fill((0, 0, 0))
        blit_rotated()
        pygame.display.update()
        clock.tick(FPS)

except KeyboardInterrupt:
    pygame.quit()
