#!/usr/bin/env python3
import os, io, time, threading, requests, sys, json, argparse, subprocess
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

# ================== CONFIGURATION ==================
CONFIG_PATH = Path(__file__).resolve().parent / "spotify_keys.json"
def load_config(path):
    if not path.exists():
        sys.exit(f"[ERROR] Fichier de configuration introuvable : {path}")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ["SPOTIFY_CLIENT_ID","SPOTIFY_CLIENT_SECRET","SPOTIFY_REDIRECT_URI"]:
        if k not in cfg or not cfg[k]:
            sys.exit(f"[ERROR] Clé manquante: {k}")
    if "SPOTIFY_SCOPE" not in cfg:
        cfg["SPOTIFY_SCOPE"] = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
    if "PC_HELPER_BASE" not in cfg:
        cfg["PC_HELPER_BASE"] = "http://192.168.0.103:5005"
    return cfg

cfg = load_config(CONFIG_PATH)
PC_HELPER_BASE = cfg["PC_HELPER_BASE"]
SPOTIFY_CLIENT_ID = cfg["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = cfg["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = cfg["SPOTIFY_REDIRECT_URI"]
SPOTIFY_SCOPE = cfg["SPOTIFY_SCOPE"]

# ================== HARDWARE PINS ==================
BTN_PINS = {17:"B1_PREV", 27:"B2_PLAY", 22:"B3_NEXT", 5:"B4_MODE"}
ENC_A, ENC_B, ENC_SW = 6, 13, 19

# UI CONSTANTS
W, H = 480, 800
FPS = 15
ICONS_PATH = str(Path(__file__).resolve().parent / "icons")
ROTATE_SCREEN = True

# TIMERS
HTTP_TIMEOUT_S = 0.5
ART_TIMEOUT_S  = 1.5
SPOTIFY_POLL_S = 1.0
METRICS_POLL_S = 2.0 # Rafraichissement plus rapide pour les graphes

# ================== PYGAME INIT ==================
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
    FONT_S = pygame.font.SysFont("Inter", 20)
    FONT_M = pygame.font.SysFont("Inter", 26)
    FONT_L = pygame.font.SysFont("Inter", 36, bold=True)
    FONT_XL = pygame.font.SysFont("Inter", 48, bold=True)
except:
    FONT_S = pygame.font.Font(None, 24)
    FONT_M = pygame.font.Font(None, 30)
    FONT_L = pygame.font.Font(None, 40)
    FONT_XL = pygame.font.Font(None, 50)

frame = pygame.Surface((W, H))
clock = pygame.time.Clock()

# ================== ASSETS ==================
def load_icon(name):
    path = os.path.join(ICONS_PATH, name)
    try: return pygame.image.load(path).convert_alpha()
    except:
        s = pygame.Surface((48,48), pygame.SRCALPHA)
        pygame.draw.circle(s, (100,100,100), (24,24), 20)
        return s

icon_prev = load_icon("prev.png")
icon_next = load_icon("next.png")
icon_play = load_icon("play.png")
icon_pause = load_icon("pause.png")
icon_mode = load_icon("mode.png")
icon_chart = load_icon("mode.png") # Tu pourras changer l'icone plus tard

# ================== ETAT GLOBAL ==================
state_lock = threading.Lock()
state = {
    "mode": "SPOTIFY",  # SPOTIFY, STATS, MENU
    # Spotify
    "title": "En attente...",
    "artist": "",
    "playing": False,
    "progress": 0,
    "duration": 1,
    "art_surf": None,
    "bg_surf": None,
    "track_id": None,
    "text_col": (255,255,255),
    
    # Metrics
    "metrics": {},
    "stats_view": "GAUGES", # ou "GRAPHS"
    "stats_history": [],    # Liste des X dernières mesures
    
    # Menu
    "menu_idx": 0,
    "menu_msg": "",
    "menu_items": [
        {"lbl": "Retour Spotify", "act": "BACK"},
        {"lbl": "Afficher IP",    "act": "SHOW_IP"},
        {"lbl": "Scan Wi-Fi",     "act": "WIFI"},
        {"lbl": "Redémarrer",     "act": "REBOOT"},
        {"lbl": "Éteindre",       "act": "SHUTDOWN"}
    ]
}

MAX_HISTORY = 60 # Nombre de points sur le graphique

# ================== FONCTIONS SYSTEME & API ==================
sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI, scope=SPOTIFY_SCOPE,
    open_browser=False, cache_path=str(Path(__file__).parent/".cache")
))

def pc_cmd(cmd):
    try: requests.post(f"{PC_HELPER_BASE}/media", json={"cmd": cmd}, timeout=HTTP_TIMEOUT_S)
    except: pass

def get_ip():
    try: return subprocess.check_output(["hostname", "-I"], text=True).split()[0]
    except: return "Pas d'IP"

def get_wifi_list():
    try:
        out = subprocess.check_output("nmcli -f SSID dev wifi | tail -n +2", shell=True, text=True)
        return [line.strip() for line in out.split("\n") if line.strip()][:5]
    except: return ["Erreur nmcli", "Install NetworkMgr"]

def menu_action(act):
    with state_lock:
        if act == "BACK":
            state["mode"] = "SPOTIFY"
            state["menu_msg"] = ""
        elif act == "SHOW_IP":
            state["menu_msg"] = f"IP: {get_ip()}"
        elif act == "WIFI":
            state["menu_msg"] = "Scan en cours..."
            threading.Thread(target=async_wifi_scan).start()
        elif act == "REBOOT":
            state["menu_msg"] = "Redémarrage..."
            subprocess.run(["sudo", "reboot"])
        elif act == "SHUTDOWN":
            state["menu_msg"] = "Arrêt en cours..."
            subprocess.run(["sudo", "shutdown", "now"])

def async_wifi_scan():
    nets = get_wifi_list()
    with state_lock:
        state["menu_msg"] = "\n".join(nets) if nets else "Aucun réseau"

def fetch_art(url):
    try:
        d = requests.get(url, timeout=ART_TIMEOUT_S).content
        im = Image.open(io.BytesIO(d)).convert("RGB").resize((320,320))
        s_art = pygame.image.fromstring(im.tobytes(), im.size, im.mode)
        avg = im.resize((1,1)).getpixel((0,0))
        s_bg = pygame.Surface((W,H))
        for y in range(H):
            r = y/H
            c = tuple(int(x*(1-r*0.8)) for x in avg)
            pygame.draw.line(s_bg, c, (0,y), (W,y))
        lum = sum(avg)/3
        col = (255,255,255) if lum < 150 else (20,20,20)
        with state_lock:
            state["art_surf"] = s_art
            state["bg_surf"] = s_bg
            state["text_col"] = col
    except: pass

# ================== LOGIQUE THREADS ==================
def loop_spotify():
    last_t = 0
    last_m = 0
    while True:
        now = time.time()
        
        # --- SPOTIFY POLL ---
        if now - last_t > SPOTIFY_POLL_S:
            try:
                pb = sp.current_playback()
                if pb and pb.get("item"):
                    item = pb["item"]
                    is_play = pb["is_playing"]
                    prog = pb["progress_ms"]
                    dur = item["duration_ms"]
                    tid = item["id"]
                    with state_lock:
                        if tid != state["track_id"]:
                            state["track_id"] = tid
                            state["art_surf"] = None
                            imgs = item["album"]["images"]
                            if imgs: threading.Thread(target=fetch_art, args=(imgs[0]["url"],)).start()
                        state["title"] = item["name"]
                        state["artist"] = item["artists"][0]["name"]
                        state["playing"] = is_play
                        state["duration"] = dur
                        state["progress"] = prog
            except: pass
            last_t = now
            
        # --- METRICS POLL & HISTORY ---
        if now - last_m > METRICS_POLL_S:
            try:
                r = requests.get(f"{PC_HELPER_BASE}/metrics", timeout=0.5)
                data = r.json()
                with state_lock: 
                    state["metrics"] = data
                    # Ajout à l'historique pour les graphes
                    hist = state["stats_history"]
                    hist.append(data)
                    if len(hist) > MAX_HISTORY:
                        hist.pop(0)
            except: pass
            last_m = now
        
        # Smooth progress bar
        with state_lock:
            if state["playing"]:
                state["progress"] = min(state["progress"] + 200, state["duration"])
        
        time.sleep(0.2)

# ================== GPIO INPUT ==================
def loop_gpio():
    if DEBUG: return
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for p in BTN_PINS: GPIO.setup(p, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(ENC_A, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(ENC_B, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(ENC_SW, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    last_btn = {p:1 for p in BTN_PINS}
    last_sw = 1
    last_clk = GPIO.input(ENC_A)
    
    while True:
        # --- ENCODEUR ---
        clk = GPIO.input(ENC_A)
        if clk != last_clk:
            dt = GPIO.input(ENC_B)
            direction = 1 if dt != clk else -1
            
            with state_lock: curr_mode = state["mode"]
            
            if curr_mode == "MENU":
                with state_lock:
                    idx = state["menu_idx"] + direction
                    state["menu_idx"] = max(0, min(idx, len(state["menu_items"])-1))
            elif curr_mode == "STATS":
                pass # Molette ne fait rien en mode Stats (ou scroll graphe futur ?)
            else:
                if direction > 0: pc_cmd("vol_up")
                else: pc_cmd("vol_down")
            
            last_clk = clk
            time.sleep(0.002)

        # --- CLIC ENCODEUR ---
        sw = GPIO.input(ENC_SW)
        if sw == 0 and last_sw == 1:
            action_to_do = None
            with state_lock: 
                curr_mode = state["mode"]
                if curr_mode == "MENU":
                    action_to_do = state["menu_items"][state["menu_idx"]]["act"]
            
            if action_to_do: menu_action(action_to_do)
            elif curr_mode == "SPOTIFY": pc_cmd("mute_toggle")
            # En mode STATS, clic ne fait rien (ou screenshot ?)
            time.sleep(0.3)
        last_sw = sw
        
        # --- BOUTONS ---
        for pin, name in BTN_PINS.items():
            val = GPIO.input(pin)
            if val == 0 and last_btn[pin] == 1:
                
                cmd_pc = None
                action_menu = None
                change_mode = False
                toggle_stats = False
                
                with state_lock: 
                    curr_mode = state["mode"]
                    
                    if name == "B4_MODE":
                        change_mode = True
                    
                    elif curr_mode == "MENU":
                        if name == "B1_PREV": state["menu_idx"] = max(0, state["menu_idx"] - 1)
                        elif name == "B3_NEXT": state["menu_idx"] = min(len(state["menu_items"])-1, state["menu_idx"] + 1)
                        elif name == "B2_PLAY": action_menu = state["menu_items"][state["menu_idx"]]["act"]
                    
                    elif curr_mode == "STATS":
                        # BOUTONS DÉCONNECTÉS DE SPOTIFY ICI
                        if name == "B2_PLAY":
                            # Bascule Jauges <-> Graphes
                            toggle_stats = True
                        # B1 et B3 ne font rien ici pour l'instant
                        
                    else: # Mode SPOTIFY
                        if name == "B1_PREV": cmd_pc = "prev"
                        elif name == "B2_PLAY": cmd_pc = "playpause"
                        elif name == "B3_NEXT": cmd_pc = "next"

                # Actions hors Lock
                if change_mode:
                    with state_lock:
                        if state["mode"] == "SPOTIFY": state["mode"] = "STATS"
                        elif state["mode"] == "STATS": state["mode"] = "MENU"
                        else: state["mode"] = "SPOTIFY"
                        state["menu_msg"] = "" 
                
                if toggle_stats:
                    with state_lock:
                        if state["stats_view"] == "GAUGES": state["stats_view"] = "GRAPHS"
                        else: state["stats_view"] = "GAUGES"

                if action_menu: menu_action(action_menu)
                if cmd_pc: pc_cmd(cmd_pc)

            last_btn[pin] = val
        time.sleep(0.005)

# ================== RENDU GRAPHIQUE ==================
def render_text_centered(s, text, font, col, y):
    surf = font.render(text, True, col)
    rect = surf.get_rect(center=(W//2, y))
    s.blit(surf, rect)

def render_spotify_ui(s):
    with state_lock:
        bg, art = state["bg_surf"], state["art_surf"]
        tit, art_name = state["title"], state["artist"]
        col = state["text_col"]
        prog, dur, playing = state["progress"], state["duration"], state["playing"]
    
    if bg: s.blit(bg, (0,0))
    else: s.fill((20,20,20))
    
    if art: 
        r = art.get_rect(center=(W//2, 250))
        s.blit(art, r)
        pygame.draw.rect(s, (255,255,255), r, 2)
    
    render_text_centered(s, tit, FONT_L, col, 450)
    render_text_centered(s, art_name, FONT_M, col, 500)
    
    bar_w, bar_h = 360, 8
    bar_x = (W - bar_w)//2
    ratio = max(0, min(1, prog/dur))
    pygame.draw.rect(s, (80,80,80), (bar_x, 540, bar_w, bar_h), border_radius=4)
    pygame.draw.rect(s, col, (bar_x, 540, int(bar_w*ratio), bar_h), border_radius=4)
    
    btn_y = 620
    s.blit(icon_prev, (W//2 - 140, btn_y))
    s.blit(icon_pause if playing else icon_play, (W//2 - 32, btn_y))
    s.blit(icon_next, (W//2 + 76, btn_y))
    s.blit(icon_mode, (W//2 - 24, 720))

def draw_chart(s, x, y, w, h, data_points, color, label, max_val=100):
    # Fond du graphe
    pygame.draw.rect(s, (20,20,30), (x, y, w, h))
    pygame.draw.rect(s, (60,60,70), (x, y, w, h), 1)
    
    # Label
    lbl = FONT_S.render(label, True, color)
    s.blit(lbl, (x + 5, y + 5))
    
    if len(data_points) < 2: return
    
    points = []
    step_x = w / (MAX_HISTORY - 1)
    
    for i, val in enumerate(data_points):
        try: v = float(val)
        except: v = 0
        px = x + (i * step_x)
        # Inversion Y (0 en bas)
        py = y + h - ((v / max_val) * h)
        points.append((px, py))
    
    if len(points) > 1:
        pygame.draw.lines(s, color, False, points, 2)
        # Point final
        pygame.draw.circle(s, color, (int(points[-1][0]), int(points[-1][1])), 4)
        # Valeur actuelle texte
        curr_val = FONT_M.render(f"{data_points[-1]}", True, (255,255,255))
        s.blit(curr_val, (x + w - 45, y + 5))

def render_stats_ui(s):
    s.fill((10,10,15))
    render_text_centered(s, "PC MONITOR", FONT_XL, (0,255,200), 40)
    
    with state_lock: 
        view = state["stats_view"]
        mets = state["metrics"]
        hist = list(state["stats_history"]) # Copie pour thread safety
    
    if view == "GAUGES":
        # --- VUE JAUGES (ANCIENNE) ---
        y = 130
        for label, key, unit in [("CPU Load", "cpu", "%"), ("CPU Temp", "temp_cpu", "°C"),
                                 ("GPU Load", "gpu", "%"), ("GPU Temp", "temp_gpu", "°C")]:
            val = mets.get(key, "--")
            pygame.draw.rect(s, (30,30,40), (40, y+35, 400, 20), border_radius=10)
            try:
                v_float = float(val)
                col_bar = (50, 255, 50)
                if v_float > 60: col_bar = (255, 200, 0)
                if v_float > 80: col_bar = (255, 50, 50)
                w_bar = int((v_float/100)*400)
                pygame.draw.rect(s, col_bar, (40, y+35, w_bar, 20), border_radius=10)
            except: pass
            
            lbl_surf = FONT_L.render(label, True, (220,220,220))
            val_surf = FONT_L.render(f"{val}{unit}", True, (255,255,255))
            s.blit(lbl_surf, (40, y))
            s.blit(val_surf, (W - 40 - val_surf.get_width(), y))
            y += 100
            
        # Indication bouton
        hint = FONT_S.render("[PLAY] -> Voir Graphiques", True, (100,100,100))
        s.blit(hint, (W//2 - hint.get_width()//2, 600))
        
    else:
        # --- VUE GRAPHIQUES (NOUVELLE) ---
        # Préparation des données
        cpu_loads = [d.get("cpu",0) for d in hist]
        gpu_loads = [d.get("gpu",0) for d in hist]
        cpu_temps = [d.get("temp_cpu",0) for d in hist]
        gpu_temps = [d.get("temp_gpu",0) for d in hist]
        
        # Graphe 1: CHARGE (Load)
        draw_chart(s, 20, 100, W-40, 200, cpu_loads, (0, 200, 255), "CPU Load (%)")
        draw_chart(s, 20, 100, W-40, 200, gpu_loads, (0, 255, 100), "GPU Load (%) - Vert", 100) # Superposé c'est moche, on sépare
        
        # On refait proprement : 3 Zones
        
        # Zone 1 : CPU
        draw_chart(s, 20, 100, W-40, 180, cpu_loads, (0, 200, 255), "CPU Load (%)")
        
        # Zone 2 : GPU
        draw_chart(s, 20, 300, W-40, 180, gpu_loads, (0, 255, 100), "GPU Load (%)")
        
        # Zone 3 : Temperatures (CPU en rouge, GPU en orange sur le même graphe ou séparé)
        # Faisons séparé pour clarté
        draw_chart(s, 20, 500, (W-50)//2, 150, cpu_temps, (255, 100, 100), "CPU Temp", 100)
        draw_chart(s, W//2 + 5, 500, (W-50)//2, 150, gpu_temps, (255, 180, 50), "GPU Temp", 100)
        
        hint = FONT_S.render("[PLAY] -> Voir Jauges", True, (100,100,100))
        s.blit(hint, (W//2 - hint.get_width()//2, 680))

    s.blit(icon_mode, (W//2 - 24, 720))

def render_menu_ui(s):
    s.fill((30, 30, 35))
    render_text_centered(s, "SYSTEM MENU", FONT_L, (255, 200, 0), 50)
    pygame.draw.line(s, (255,200,0), (40, 80), (W-40, 80), 2)
    
    with state_lock:
        items = state["menu_items"]
        idx = state["menu_idx"]
        msg = state["menu_msg"]
    
    y = 120
    for i, item in enumerate(items):
        is_sel = (i == idx)
        col = (0, 0, 0) if is_sel else (200, 200, 200)
        bg_col = (255, 200, 0) if is_sel else None
        
        txt = FONT_M.render(f"  {item['lbl']}  ", True, col)
        if bg_col:
            rect = txt.get_rect(center=(W//2, y))
            pygame.draw.rect(s, bg_col, rect.inflate(20, 10), border_radius=5)
        render_text_centered(s, item['lbl'], FONT_M, col, y)
        y += 60
        
    if msg:
        pygame.draw.rect(s, (10,10,10), (20, 500, W-40, 200), border_radius=8)
        pygame.draw.rect(s, (100,100,100), (20, 500, W-40, 200), 2, border_radius=8)
        lines = msg.split('\n')
        my = 520
        for l in lines:
            ts = FONT_S.render(l, True, (200,255,200))
            s.blit(ts, (40, my))
            my += 25

    inst = FONT_S.render("[PREV/NEXT] Naviguer  -  [PLAY] Valider", True, (100,100,100))
    s.blit(inst, (W//2 - inst.get_width()//2, 760))

# ================== MAIN LOOP ==================
if __name__ == "__main__":
    threading.Thread(target=loop_spotify, daemon=True).start()
    threading.Thread(target=loop_gpio, daemon=True).start()
    print("[INFO] Démarrage PiPanel...")
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT: sys.exit()
            if DEBUG and e.type == pygame.KEYDOWN:
                if e.key == pygame.K_m: 
                    with state_lock: 
                        if state["mode"]=="SPOTIFY": state["mode"]="STATS"
                        elif state["mode"]=="STATS": state["mode"]="MENU"
                        else: state["mode"]="SPOTIFY"
                if state["mode"] == "STATS" and e.key == pygame.K_SPACE:
                     with state_lock:
                        if state["stats_view"] == "GAUGES": state["stats_view"] = "GRAPHS"
                        else: state["stats_view"] = "GAUGES"
                        
        with state_lock: m = state["mode"]
        frame.fill((0,0,0))
        if m == "SPOTIFY": render_spotify_ui(frame)
        elif m == "STATS": render_stats_ui(frame)
        elif m == "MENU": render_menu_ui(frame)
        
        if ROTATE_SCREEN and not DEBUG:
            rot = pygame.transform.rotate(frame, -90)
            screen.blit(rot, rot.get_rect(center=screen.get_rect().center))
        else: screen.blit(frame, (0,0))
        pygame.display.flip()
        clock.tick(FPS)