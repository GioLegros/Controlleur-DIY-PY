#!/usr/bin/env python3
import os, io, time, threading, requests, sys, json, argparse
import pygame
from pathlib import Path
from PIL import Image
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

# ================== ARGUMENTS ==================
parser = argparse.ArgumentParser()
parser.add_argument("--debug", action="store_true", help="Active le mode debug (clavier, pas de GPIO ni framebuffer)")
args = parser.parse_args()
DEBUG = args.debug

# ================== CONFIG FICHIER ==================
CONFIG_PATH = Path(__file__).resolve().parent / "spotify_keys.json"
def load_config(path):
    if not path.exists():
        sys.exit(f"[ERROR] Fichier de configuration introuvable : {path}")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ["SPOTIFY_CLIENT_ID","SPOTIFY_CLIENT_SECRET","SPOTIFY_REDIRECT_URI"]:
        if k not in cfg or not cfg[k]:
            sys.exit(f"[ERROR] Clé de configuration manquante: {k}")
    if "SPOTIFY_SCOPE" not in cfg:
        cfg["SPOTIFY_SCOPE"] = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
    if "PC_HELPER_BASE" not in cfg:
        cfg["PC_HELPER_BASE"] = "http://192.168.0.103:5005"
    return cfg

cfg = load_config(CONFIG_PATH)
PC_HELPER_BASE = cfg["PC_HELPER_BASE"]

SPOTIFY_CLIENT_ID     = cfg["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = cfg["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI  = cfg["SPOTIFY_REDIRECT_URI"]
SPOTIFY_SCOPE         = cfg["SPOTIFY_SCOPE"]

# ================== HARDWARE / UI ==================
BTN_PINS = {17:"B1_PREV", 27:"B2_PLAY", 22:"B3_NEXT", 5:"B4_MODE"}
ENC_A, ENC_B, ENC_SW = 6, 13, 19

W, H = 480, 800
FPS = 10
ICONS_PATH = str(Path(__file__).resolve().parent / "icons")
ROTATE_SCREEN = True

HTTP_TIMEOUT_S = 0.6
ART_TIMEOUT_S  = 1.2
SPOTIFY_POLL_S = 1
METRICS_POLL_S = 5.0
RESYNC_PROGRESS_S = 5.0

# ================== ENV SDL / Pygame ==================
if not DEBUG:
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
else:
    print("[DEBUG] Mode debug activé — pas de framebuffer, pas de GPIO")

pygame.init()
if DEBUG:
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("PiPanel DEBUG")
    pygame.mouse.set_visible(True)
else:
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
        return pygame.image.load(path).convert_alpha()
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
        print(f"[CMD] {cmd}")
    except Exception as e:
        print(f"[WARN] media_cmd failed: {e}")

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
    print(f"[THREAD] Chargement image: {url}")
    try:
        data = requests.get(url, timeout=ART_TIMEOUT_S).content
        img = Image.open(io.BytesIO(data)).convert("RGB").resize((300,300), Image.LANCZOS)
        surf = pygame.image.fromstring(img.tobytes(), img.size, img.mode)
        avg = img.resize((1,1)).getpixel((0,0))

        # Créer un dégradé de fond
        surf_bg = pygame.Surface((W, H))
        for y in range(H):
            ratio = y / H
            color = tuple(int(c*(1-ratio)) for c in avg)
            pygame.draw.line(surf_bg, color, (0,y), (W,y))

        # Choisir couleur texte selon la luminosité
        lum = 0.299*avg[0]+0.587*avg[1]+0.114*avg[2]
        tcol = (255,255,255) if lum<128 else (20,20,20)

        with state_lock:
            state["art_surface"] = surf
            state["bg_surface"] = surf_bg
            state["text_color"] = tcol
        print("[THREAD] Image Spotify chargée")
    except Exception as e:
        print(f"[WARN] artwork: {e}")


# ================== METRICS ==================
def poll_metrics():
    try:
        r = requests.get(f"{PC_HELPER_BASE}/metrics", timeout=HTTP_TIMEOUT_S)
        with state_lock:
            state["metrics"] = r.json() if r.status_code == 200 else {}
    except Exception:
        pass

# ================== GPIO ==================
IS_RPI = False
if not DEBUG:
    try:
        import RPi.GPIO as GPIO
        IS_RPI = True
    except Exception as e:
        print(f"[WARN] GPIO non dispo: {e}")

if IS_RPI:
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in BTN_PINS:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(ENC_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(ENC_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(ENC_SW, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def gpio_loop():
    print("[GPIO] Boucle active. En attente d'actions...")
    
    # 1. Initialisation des états
    last_btn = {p: GPIO.input(p) for p in BTN_PINS}
    
    # Pour la molette (Encodeur)
    last_A = GPIO.input(ENC_A)
    last_SW = GPIO.input(ENC_SW) # On lit l'état initial du clic

    while True:
        for pin, name in BTN_PINS.items():
            val = GPIO.input(pin)
            if val == 0 and last_btn[pin] == 1:
                if name == "B1_PREV": media_cmd("prev")
                elif name == "B2_PLAY": media_cmd("playpause")
                elif name == "B3_NEXT": media_cmd("next")
                elif name == "B4_MODE":
                    with state_lock:
                        state["mode"] = "STATS" if state["mode"]=="SPOTIFY" else "SPOTIFY"
            last_btn[pin] = val

        sw_val = GPIO.input(ENC_SW)
        if sw_val == 0 and last_SW == 1:
            print("[GPIO] Clic Molette détecté !")
            media_cmd("mute") 
        last_SW = sw_val

        A = GPIO.input(ENC_A)
        if A != last_A:
            B = GPIO.input(ENC_B)
            if A == 1: 
                if B == 0: 
                    print("[GPIO] Rotation: Vol UP")
                    media_cmd("vol_up")
                else:
                    print("[GPIO] Rotation: Vol DOWN")
                    media_cmd("vol_down")
            last_A = A
        time.sleep(0.001)

# ================== RENDER HELPERS ==================
def blit_rotated():
    if ROTATE_SCREEN and not DEBUG:
        rotated = pygame.transform.rotate(frame, 90)
        screen.blit(rotated, rotated.get_rect(center=screen.get_rect().center))
    else:
        screen.blit(frame, (0,0))

def draw_progress_bar(s, x, y, w, h, r):
    pygame.draw.rect(s, (25,25,25), (x,y,w,h), border_radius=6)
    pygame.draw.rect(s, (30,215,96), (x,y,int(w*r),h), border_radius=6)

def ms_str(ms):
    s = int(ms/1000)
    return f"{s//60}:{s%60:02d}"

# ================== RENDER ==================
scroll_offset = 0
scroll_speed = 3.0  # pixels par frame
scroll_pause = 20  # frames de pause au début et à la fin
scroll_counter = 0

def render_spotify(s):
    global scroll_offset, scroll_counter

    with state_lock:
        art, bg, tcol = state["art_surface"], state["bg_surface"], state["text_color"]
        title, artist = state["now_title"], state["now_artist"]
        p, d, play = state["progress_ms"], state["duration_ms"], state["playing"]

    # bg
    if bg: s.blit(bg, (0, 0))
    else: s.fill((10, 10, 10))

    # dubug mode
    if DEBUG:
        s.blit(SMALL.render("[DEBUG MODE]", True, (255, 80, 80)), (10, 10))

    mode_t = SMALL.render("Spotify", True, tcol)
    s.blit(mode_t, (W//2 - mode_t.get_width()//2, 40))

    # image
    if art: s.blit(art, (W//2 - 150, 80))
    else:
        placeholder = SMALL.render("Chargement de la pochette...", True, (200,200,200))
        s.blit(placeholder, (W//2 - placeholder.get_width()//2, 220))

    # progress bar
    ratio = min(1, max(0, p/d))
    draw_progress_bar(s, 60, 450, W-120, 8, ratio)
    t1 = SMALL.render(ms_str(p), True, (230,230,230))
    t2 = SMALL.render(ms_str(d), True, (230,230,230))
    s.blit(t1, (60, 460))
    s.blit(t2, (W-60 - t2.get_width(), 460))

    title_s = BIG.render(title, True, tcol)
    artist_s = FONT.render(artist, True, tcol)

    # length zone
    max_w = W - 100
    title_y = 380
    artist_y = 410

    if title_s.get_width() > max_w:
        # moving text
        scroll_counter += 1
        if scroll_counter > scroll_pause:
            scroll_offset += scroll_speed
        if scroll_offset > title_s.get_width():
            scroll_offset = -max_w
            scroll_counter = 0

        s.blit(title_s, (60 - scroll_offset, title_y))
    else:
        # centered text
        s.blit(title_s, (W//2 - title_s.get_width()//2, title_y))

    # Artiste moving text if needed
    if artist_s.get_width() > max_w:
        s.blit(artist_s, (60 - (scroll_offset / 2), artist_y))
    else:
        s.blit(artist_s, (W//2 - artist_s.get_width()//2, artist_y))

    # --- Boutons ---
    s.blit(prev_icon, (W//2 - 150, 520))
    s.blit(pause_icon if play else play_icon, (W//2 - 32, 520))
    s.blit(next_icon, (W//2 + 86, 520))
    s.blit(mode_icon, (W//2 - mode_icon.get_width()//2, 640))


def render_stats(s):
    s.fill((20,20,25))
    with state_lock: metrics=state.get("metrics",{})
    y=100
    for l,k in [("CPU","cpu"),("GPU","gpu"),("Temp CPU (°C)","temp_cpu"),("Temp GPU (°C)","temp_gpu")]:
        val=str(metrics.get(k,"n/a"))
        s.blit(BIG.render(f"{l}",True,(255,255,255)),(60,y))
        valr=BIG.render(f"{val}",True,(200,200,200))
        s.blit(valr,(W-60-valr.get_width(),y));y+=60

# ================== LOGIC ==================
def logic_loop():
    last_sp = 0
    last_m = 0
    last_id = None

    while True:
        dt = clock.get_time()

        # barre de progression progressive
        with state_lock:
            if state["playing"]:
                state["progress_ms"] = min(state["progress_ms"] + dt, state["duration_ms"])

        now = time.time()

        if now - last_sp >= SPOTIFY_POLL_S:
            try:
                cur = sp.current_playback() or {}
                item = cur.get("item") or {}
                tid = item.get("id")

                #maj état
                with state_lock:
                    state["now_title"] = item.get("name", "—")
                    state["now_artist"] = ", ".join(a["name"] for a in item.get("artists", [])) or "—"
                    state["duration_ms"] = item.get("duration_ms") or 1
                    state["progress_ms"] = cur.get("progress_ms", 0)
                    state["playing"] = cur.get("is_playing", False)
                    state["track_id"] = tid

                #new track ?
                if tid and tid != last_id:
                    print(f"[DEBUG] Nouveau morceau: {state['now_title']} - {state['now_artist']}")

                    # charge la pochette en thread
                    imgs = item.get("album", {}).get("images") or []
                    if imgs:
                        url = imgs[0]["url"]

                        # suppr image avoiding flash
                        with state_lock:
                            state["art_surface"] = None

                        threading.Thread(
                            target=fetch_and_apply_artwork,
                            args=(url,),
                            daemon=True
                        ).start()

                    last_id = tid

            except Exception as e:
                print(f"[WARN] Spotify poll: {e}")

            last_sp = now

        # --- Poll Metrics (CPU/GPU) ---
        if now - last_m >= METRICS_POLL_S:
            poll_metrics()
            last_m = now

        time.sleep(0.2)

# ================== RENDER LOOP ==================
def render_loop():
    print(f"[INFO] Mode {'DEBUG (clavier)' if DEBUG else 'RPI (GPIO)'}")
    while True:
        # Événements
        for e in pygame.event.get():
            if e.type==pygame.QUIT: pygame.quit();sys.exit()
            if DEBUG and e.type==pygame.KEYDOWN:
                if e.key in [pygame.K_ESCAPE,pygame.K_q]: pygame.quit();sys.exit()
                elif e.key==pygame.K_LEFT: media_cmd("prev")
                elif e.key==pygame.K_RIGHT: media_cmd("next")
                elif e.key==pygame.K_SPACE: media_cmd("playpause")
                elif e.key==pygame.K_UP: media_cmd("vol_up")
                elif e.key==pygame.K_DOWN: media_cmd("vol_down")
                elif e.key==pygame.K_m:
                    with state_lock: state["mode"]="STATS" if state["mode"]=="SPOTIFY" else "SPOTIFY"

        # Rendu
        frame.fill((0,0,0))
        with state_lock: mode=state["mode"]
        render_spotify(frame) if mode=="SPOTIFY" else render_stats(frame)
        screen.fill((0,0,0));blit_rotated();pygame.display.flip();clock.tick(FPS)

# ================== MAIN ==================
if __name__ == "__main__":
    if IS_RPI and not DEBUG:
        threading.Thread(target=gpio_loop,daemon=True).start()
    threading.Thread(target=logic_loop,daemon=True).start()
    render_loop()
