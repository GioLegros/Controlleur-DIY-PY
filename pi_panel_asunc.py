#!/usr/bin/env python3
import os, asyncio, io, sys, json, aiohttp, pygame
from pathlib import Path
from PIL import Image
from gpiozero import RotaryEncoder, Button
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth

# ==================== CONFIG ====================
CONFIG_PATH = Path(__file__).resolve().parent / "spotify_keys.json"
if not CONFIG_PATH.exists():
    sys.exit(f"❌ Fichier {CONFIG_PATH} introuvable")

cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

SPOTIFY_CLIENT_ID = cfg["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = cfg["SPOTIFY_CLIENT_SECRET"]
SPOTIFY_REDIRECT_URI = cfg["SPOTIFY_REDIRECT_URI"]
SPOTIFY_SCOPE = cfg.get(
    "SPOTIFY_SCOPE",
    "user-read-playback-state user-modify-playback-state user-read-currently-playing"
)
PC_HELPER_BASE = cfg.get("PC_HELPER_BASE", "http://192.168.0.102:5005")

# ==================== ENVIRONNEMENT SDL ====================
os.environ["SDL_FBDEV"] = "/dev/fb0"
os.environ["SDL_MOUSEDRV"] = "TSLIB"
os.environ["SDL_MOUSEDEV"] = "/dev/input/touchscreen"

for driver in ["fbcon", "directfb", "kmsdrm", "x11"]:
    try:
        os.environ["SDL_VIDEODRIVER"] = driver
        pygame.display.init()
        print(f"✅ Driver SDL utilisé : {driver}")
        break
    except pygame.error:
        print(f"Driver SDL non disponible : {driver}")
else:
    sys.exit("Aucun driver SDL compatible trouvé.")

# ==================== UI / PYGAME ====================
pygame.init()
W, H = 480, 800
screen = pygame.display.set_mode((W, H), pygame.FULLSCREEN)
pygame.mouse.set_visible(False)
pygame.display.set_caption("PiPanel Async")

FONT = pygame.font.SysFont("Inter", 26)
BIG = pygame.font.SysFont("Inter", 34, bold=True)
SMALL = pygame.font.SysFont("Inter", 22)
ROTATE_SCREEN = True
FPS = 20

frame = pygame.Surface((W, H))
clock = pygame.time.Clock()

# ==================== ETAT GLOBAL ====================
state = {
    "mode": "SPOTIFY",
    "title": "—",
    "artist": "—",
    "progress": 0,
    "duration": 1,
    "playing": False,
    "metrics": {},
    "art_surface": None,
    "bg_color": (20, 20, 20),
    "text_color": (255, 255, 255)
}

state_lock = asyncio.Lock()

# ==================== SPOTIFY ====================
CACHE_DIR = Path(__file__).resolve().parent
sp = Spotify(auth_manager=SpotifyOAuth(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    scope=SPOTIFY_SCOPE,
    open_browser=False,
    cache_path=str(CACHE_DIR / ".cache")
))

async def fetch_art(url, session):
    try:
        async with session.get(url, timeout=5) as resp:
            data = await resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img = img.resize((300, 300), Image.LANCZOS)
        surface = pygame.image.fromstring(img.tobytes(), img.size, img.mode)

        avg_color = img.resize((1, 1)).getpixel((0, 0))
        dark = (0.299*avg_color[0] + 0.587*avg_color[1] + 0.114*avg_color[2]) < 128
        text_color = (255, 255, 255) if dark else (20, 20, 20)

        async with state_lock:
            state["art_surface"] = surface
            state["bg_color"] = avg_color
            state["text_color"] = text_color

    except Exception as e:
        print("Erreur chargement art:", e)

async def spotify_loop(session):
    print("🎵 Thread Spotify async démarré")
    last_track = None
    while True:
        try:
            cur = sp.current_playback() or {}
            item = cur.get("item") or {}
            async with state_lock:
                state["title"] = item.get("name", "—")
                state["artist"] = ", ".join([a["name"] for a in item.get("artists", [])]) or "—"
                state["duration"] = item.get("duration_ms", 1)
                state["progress"] = cur.get("progress_ms", 0)
                state["playing"] = cur.get("is_playing", False)

            track_id = item.get("id")
            if track_id and track_id != last_track:
                images = item.get("album", {}).get("images") or []
                if images:
                    await fetch_art(images[0]["url"], session)
                last_track = track_id
        except Exception as e:
            print("Spotify loop erreur:", e)
        await asyncio.sleep(2)

# ==================== METRICS ====================
async def metrics_loop(session):
    print("📈 Thread Metrics async démarré")
    while True:
        try:
            async with session.get(f"{PC_HELPER_BASE}/metrics", timeout=2) as r:
                data = await r.json()
            async with state_lock:
                state["metrics"] = data
        except Exception:
            pass
        await asyncio.sleep(5)

# ==================== GPIO ====================
BTN_PINS = {17:"B1_PREV", 27:"B2_PLAY", 22:"B3_NEXT", 5:"B4_MODE"}
ENC_A, ENC_B, ENC_SW = 6, 13, 19
encoder = RotaryEncoder(a=ENC_A, b=ENC_B, max_steps=0)
encoder_button = Button(ENC_SW, pull_up=True, bounce_time=0.2)
buttons = {pin: Button(pin, pull_up=True, bounce_time=0.2) for pin in BTN_PINS}

def media_cmd(cmd):
    try:
        asyncio.create_task(_post_media(cmd))
    except RuntimeError:
        pass  # ignore si event loop non prête

async def _post_media(cmd):
    async with aiohttp.ClientSession() as s:
        try:
            await s.post(f"{PC_HELPER_BASE}/media", json={"cmd": cmd}, timeout=0.5)
        except:
            pass

def on_rotate():
    delta = encoder.steps
    encoder.steps = 0
    if delta > 0:
        media_cmd("vol_down")
    elif delta < 0:
        media_cmd("vol_up")

def on_click():
    media_cmd("playpause")

def on_button(pin):
    name = BTN_PINS[pin]
    if name == "B1_PREV": media_cmd("prev")
    elif name == "B2_PLAY": media_cmd("playpause")
    elif name == "B3_NEXT": media_cmd("next")
    elif name == "B4_MODE":
        asyncio.create_task(toggle_mode())

async def toggle_mode():
    async with state_lock:
        state["mode"] = "STATS" if state["mode"] == "SPOTIFY" else "SPOTIFY"

encoder.when_rotated = on_rotate
encoder_button.when_pressed = on_click
for pin, btn in buttons.items():
    btn.when_pressed = lambda b=pin: on_button(b)

# ==================== RENDU PYGAME ====================
def blit_rotated():
    if ROTATE_SCREEN:
        rotated = pygame.transform.rotate(frame, 90)
        rect = rotated.get_rect(center=screen.get_rect().center)
        screen.blit(rotated, rect.topleft)
    else:
        screen.blit(frame, (0, 0))

async def render_loop():
    print("🖥️  Thread graphique async démarré")
    while True:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                pygame.quit(); sys.exit()

        async with state_lock:
            mode = state["mode"]
            bg_color = state["bg_color"]
            text_color = state["text_color"]
            frame.fill(bg_color)

            if mode == "SPOTIFY":
                render_spotify(state, text_color)
            else:
                render_stats(state, text_color)

        screen.fill((0, 0, 0))
        blit_rotated()
        pygame.display.update()
        clock.tick(FPS)
        await asyncio.sleep(0)  # libère le CPU

def render_spotify(st, color):
    y = 40
    t = SMALL.render("Spotify", True, color)
    frame.blit(t, (W//2 - t.get_width()//2, y))
    if st["art_surface"]:
        frame.blit(st["art_surface"], (W//2 - 150, 100))
    title = BIG.render(st["title"], True, color)
    artist = FONT.render(st["artist"], True, color)
    frame.blit(title, (W//2 - title.get_width()//2, 440))
    frame.blit(artist, (W//2 - artist.get_width()//2, 480))

def render_stats(st, color):
    frame.fill((20, 20, 25))
    y = 100
    for label, key in [("CPU", "cpu"), ("GPU", "gpu"), ("Temp CPU", "temp_cpu"), ("Temp GPU", "temp_gpu")]:
        val = str(st["metrics"].get(key, "—"))
        lbl = BIG.render(label, True, color)
        valr = BIG.render(val, True, color)
        frame.blit(lbl, (60, y))
        frame.blit(valr, (W-60 - valr.get_width(), y))
        y += 60

# ==================== MAIN ====================
async def main():
    async with aiohttp.ClientSession() as session:
        tasks = [
            asyncio.create_task(spotify_loop(session)),
            asyncio.create_task(metrics_loop(session)),
            asyncio.create_task(render_loop()),
        ]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pygame.quit()
