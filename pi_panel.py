#!/usr/bin/env python3
import os, io, time, threading, requests, sys, json, argparse, socket
import pygame
from pathlib import Path
from PIL import Image
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

# ================== ARGUMENTS ==================
parser = argparse.ArgumentParser()
parser.add_argument("--debug", action="store_true", help="Mode debug (clavier, pas de GPIO)")
args = parser.parse_args()
DEBUG = args.debug

# ================== AUTO-DECOUVERTE SERVEUR ==================
def find_server_ip():
    print("[AUTO] Recherche du serveur PC (attente du signal)...")
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        client.bind(("", 5006)) # Ecoute sur le port 5006
        while True:
            data, addr = client.recvfrom(1024)
            if b"PI_HELPER_SERVER_HERE" in data:
                server_ip = addr[0]
                print(f"[AUTO] Serveur trouvé : {server_ip}")
                client.close()
                return f"http://{server_ip}:5005"
    except Exception as e:
        print(f"[ERR] Auto-découverte échouée: {e}")
        return "http://127.0.0.1:5005" # Fallback

# ================== CONFIGURATION ==================
CONFIG_PATH = Path(__file__).resolve().parent / "spotify_keys.json"
def load_config(path):
    if not path.exists(): sys.exit(f"[ERROR] Config introuvable : {path}")
    with open(path, "r", encoding="utf-8") as f: cfg = json.load(f)
    
    # Gestion IP
    base = cfg.get("PC_HELPER_BASE", "AUTO")
    if base == "AUTO":
        cfg["PC_HELPER_BASE"] = find_server_ip()
    
    return cfg

cfg = load_config(CONFIG_PATH)
PC_HELPER_BASE = cfg["PC_HELPER_BASE"]
SPOTIFY_CLIENT_ID = cfg["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = cfg["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = cfg["SPOTIFY_REDIRECT_URI"]
SPOTIFY_SCOPE = cfg.get("SPOTIFY_SCOPE", "user-read-playback-state user-modify-playback-state user-read-currently-playing")

# ================== HARDWARE UI ==================
W, H = 480, 800
FPS = 15 # Un peu plus fluide
ICONS_PATH = str(Path(__file__).resolve().parent / "icons")
ROTATE_SCREEN = True

# Pins
BTN_PINS = {17:"B1_PREV", 27:"B2_PLAY", 22:"B3_NEXT", 5:"B4_MODE"}
ENC_A, ENC_B, ENC_SW = 6, 13, 19

# Commandes HTTP
def media_cmd(cmd):
    try:
        requests.post(f"{PC_HELPER_BASE}/media", json={"cmd": cmd}, timeout=0.2)
        print(f"[CMD] {cmd}")
    except: pass # On ignore les erreurs pour ne pas bloquer l'UI

# ================== GPIO (INTERRUPTIONS) ==================
# Variables globales encodeur
clk_last_state = 0

def setup_gpio():
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        # --- Boutons ---
        for pin, name in BTN_PINS.items():
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(pin, GPIO.FALLING, callback=btn_callback, bouncetime=250)

        # --- Encodeur ---
        GPIO.setup(ENC_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(ENC_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(ENC_SW, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        
        # Events Encodeur
        GPIO.add_event_detect(ENC_A, GPIO.BOTH, callback=rotary_callback)
        GPIO.add_event_detect(ENC_SW, GPIO.FALLING, callback=click_callback, bouncetime=300)
        
        print("[GPIO] Interruptions activées.")
    except Exception as e:
        print(f"[WARN] Erreur GPIO: {e}")

# Callbacks
def btn_callback(channel):
    name = BTN_PINS.get(channel)
    if not name: return
    if name == "B1_PREV": media_cmd("prev")
    elif name == "B2_PLAY": media_cmd("playpause")
    elif name == "B3_NEXT": media_cmd("next")
    elif name == "B4_MODE":
        with state_lock:
            state["mode"] = "STATS" if state["mode"]=="SPOTIFY" else "SPOTIFY"

def click_callback(channel):
    print("[GPIO] Clic Molette")
    media_cmd("mute_toggle")

def rotary_callback(channel):
    import RPi.GPIO as GPIO
    global clk_last_state
    clk = GPIO.input(ENC_A)
    dt = GPIO.input(ENC_B)
    
    if clk != clk_last_state:
        if clk == 0: # Front descendant
            # Si DT != CLK -> Sens 1, sinon Sens 2
            if dt != clk:
                media_cmd("vol_up")
            else:
                media_cmd("vol_down")
        clk_last_state = clk

# ================== PYGAME INIT ==================
if not DEBUG:
    os.environ["SDL_FBDEV"] = "/dev/fb0"
    os.environ["SDL_MOUSEDRV"] = "TSLIB"
    os.environ["SDL_MOUSEDEV"] = "/dev/input/touchscreen"
    # Drivers communs
    drivers = ["fbcon", "directfb", "kmsdrm"] 
    found = False
    for d in drivers:
        if not found:
            os.environ["SDL_VIDEODRIVER"] = d
            try: pygame.display.init(); found = True
            except: pass
pygame.init()

if DEBUG:
    screen = pygame.display.set_mode((W, H))
else:
    screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
    pygame.mouse.set_visible(False)

# Fonts & Assets
try:
    FONT = pygame.font.SysFont("Inter", 26)
    BIG = pygame.font.SysFont("Inter", 34, bold=True)
    SMALL = pygame.font.SysFont("Inter", 22)
except:
    FONT = pygame.font.Font(None, 26)
    BIG = pygame.font.Font(None, 34)
    SMALL = pygame.font.Font(None, 22)

frame = pygame.Surface((W, H))
clock = pygame.time.Clock()

def load_icon(name):
    path = os.path.join(ICONS_PATH, name)
    try: return pygame.image.load(path).convert_alpha()
    except: 
        s=pygame.Surface((48,48)); s.fill((50,50,50)); return s

prev_icon  = load_icon("prev.png")
next_icon  = load_icon("next.png")
play_icon  = load_icon("play.png")
pause_icon = load_icon("pause.png")
mode_icon  = load_icon("mode.png")

# ================== STATE & SPOTIFY ==================
state_lock = threading.Lock()
state = {
    "mode": "SPOTIFY", "now_title": "—", "now_artist": "—",
    "progress_ms": 0, "duration_ms": 1, "playing": False,
    "art_surface": None, "bg_surface": None, "text_color": (255,255,255),
    "metrics": {}
}

CACHE_DIR = Path(__file__).resolve().parent
sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI, scope=SPOTIFY_SCOPE,
    open_browser=False, cache_path=str(CACHE_DIR / ".cache")
))

def fetch_art(url):
    try:
        data = requests.get(url, timeout=2).content
        img = Image.open(io.BytesIO(data)).convert("RGB").resize((300,300), Image.LANCZOS)
        surf = pygame.image.fromstring(img.tobytes(), img.size, img.mode)
        
        # Moyenne couleur pour le fond
        avg = img.resize((1,1)).getpixel((0,0))
        bg = pygame.Surface((W, H))
        # Dégradé simple
        for y in range(H):
            r = y/H
            c = tuple(int(x*(1-r*0.8)) for x in avg)
            pygame.draw.line(bg, c, (0,y), (W,y))
            
        lum = 0.299*avg[0] + 0.587*avg[1] + 0.114*avg[2]
        tc = (255,255,255) if lum < 150 else (20,20,20)
        
        with state_lock:
            state["art_surface"] = surf
            state["bg_surface"] = bg
            state["text_color"] = tc
    except: pass

def logic_loop():
    last_sp = 0
    last_met = 0
    last_track_id = None
    
    while True:
        now = time.time()
        # Spotify Poll (1s)
        if now - last_sp > 1.0:
            try:
                cur = sp.current_playback()
                if cur and cur.get("item"):
                    item = cur["item"]
                    tid = item["id"]
                    is_playing = cur["is_playing"]
                    
                    with state_lock:
                        state["playing"] = is_playing
                        state["progress_ms"] = cur["progress_ms"]
                        state["duration_ms"] = item["duration_ms"]
                        state["now_title"] = item["name"]
                        state["now_artist"] = item["artists"][0]["name"]
                    
                    if tid != last_track_id:
                        imgs = item["album"]["images"]
                        if imgs:
                            threading.Thread(target=fetch_art, args=(imgs[0]["url"],), daemon=True).start()
                        last_track_id = tid
            except: pass
            last_sp = now
            
        # Metrics Poll (3s)
        if now - last_met > 3.0:
            try:
                r = requests.get(f"{PC_HELPER_BASE}/metrics", timeout=0.5)
                if r.status_code == 200:
                    with state_lock: state["metrics"] = r.json()
            except: pass
            last_met = now
            
        # Simulation progression locale
        if state["playing"]:
            time.sleep(0.1)
            with state_lock:
                state["progress_ms"] = min(state["progress_ms"]+100, state["duration_ms"])
        else:
            time.sleep(0.1)

# ================== RENDER ==================
def blit_rot():
    if ROTATE_SCREEN and not DEBUG:
        r = pygame.transform.rotate(frame, 90)
        screen.blit(r, r.get_rect(center=screen.get_rect().center))
    else: screen.blit(frame, (0,0))

def ms_str(ms):
    s = int(ms/1000)
    return f"{s//60}:{s%60:02d}"

def render_spotify(s):
    with state_lock:
        bg, art, tc = state["bg_surface"], state["art_surface"], state["text_color"]
        tit, art_n, p, d = state["now_title"], state["now_artist"], state["progress_ms"], state["duration_ms"]
        play = state["playing"]

    if bg: s.blit(bg, (0,0))
    else: s.fill((20,20,20))
    
    # Titre App
    t = SMALL.render("Spotify", True, tc)
    s.blit(t, (W//2 - t.get_width()//2, 30))
    
    # Art
    if art: s.blit(art, (W//2 - 150, 70))
    else: pygame.draw.rect(s, (50,50,50), (W//2-150, 70, 300, 300))
    
    # Infos
    ti_s = BIG.render(tit, True, tc)
    ar_s = FONT.render(art_n, True, tc)
    
    # Centrage simple (scroll si trop long à ajouter si besoin)
    s.blit(ti_s, (W//2 - ti_s.get_width()//2, 390))
    s.blit(ar_s, (W//2 - ar_s.get_width()//2, 430))
    
    # Barre
    pygame.draw.rect(s, (80,80,80), (50, 480, W-100, 6), border_radius=3)
    if d > 0:
        w_prog = int((W-100) * (p/d))
        pygame.draw.rect(s, (255,255,255), (50, 480, w_prog, 6), border_radius=3)
    
    s.blit(SMALL.render(ms_str(p), True, tc), (50, 495))
    end_s = SMALL.render(ms_str(d), True, tc)
    s.blit(end_s, (W-50-end_s.get_width(), 495))
    
    # Contrôles
    cy = 580
    s.blit(prev_icon, (W//2 - 140, cy))
    s.blit(pause_icon if play else play_icon, (W//2 - 32, cy))
    s.blit(next_icon, (W//2 + 76, cy))
    
    # Mode
    s.blit(mode_icon, (W//2 - 24, 700))

def render_stats(s):
    s.fill((10,10,15))
    with state_lock: m = state["metrics"]
    
    y = 100
    for label, key, unit in [("CPU Load", "cpu", "%"), ("CPU Temp", "temp_cpu", "°C"), ("GPU Load", "gpu", "%"), ("GPU Temp", "temp_gpu", "°C")]:
        val = m.get(key, "n/a")
        if val != "n/a": val = f"{val}{unit}"
        
        lbl = FONT.render(label, True, (150,150,150))
        v_s = BIG.render(str(val), True, (255,255,255))
        
        s.blit(lbl, (60, y))
        s.blit(v_s, (W-60-v_s.get_width(), y))
        pygame.draw.line(s, (50,50,60), (60, y+50), (W-60, y+50))
        y += 90
    
    s.blit(mode_icon, (W//2 - 24, 700))

# ================== LOOP ==================
def render_loop():
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT: sys.exit()
            if DEBUG and e.type == pygame.KEYDOWN:
                if e.key == pygame.K_m: 
                    with state_lock: state["mode"] = "STATS" if state["mode"]=="SPOTIFY" else "SPOTIFY"
                if e.key == pygame.K_UP: media_cmd("vol_up")
                if e.key == pygame.K_DOWN: media_cmd("vol_down")
        
        frame.fill((0,0,0))
        with state_lock: m = state["mode"]
        
        if m == "SPOTIFY": render_spotify(frame)
        else: render_stats(frame)
        
        blit_rot()
        pygame.display.flip()
        clock.tick(FPS)

if __name__ == "__main__":
    if not DEBUG: setup_gpio()
    threading.Thread(target=logic_loop, daemon=True).start()
    render_loop()