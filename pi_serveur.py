from flask import Flask, jsonify, request
import threading, time, psutil, platform, keyboard, subprocess, os
import socket

try: 
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    import pythoncom
    AUDIO_OK = True
except:
    AUDIO_OK = False

# ================== CONFIGURATION DES APPS ==================
# Astuce: Pour jeux Steam, "steam://rungameid/ID_DU_JEU"
APPS = {
    "Steam": r"C:\Program Files (x86)\Steam\steam.exe",
    "Gestionnaire Tâches": "taskmgr.exe",
    "Discord": r"C:\Users\Giovanni\AppData\Local\Discord\app-1.0.9217\Discord.exe",
    "Opera": r"C:\Users\Giovanni\AppData\Local\Programs\Opera GX\opera.exe",
    "Spotify": "explorer.exe spotify:",
    "VSCode": r"C:\Users\Giovanni\AppData\Local\Programs\Microsoft VS Code\Code.exe",
    # Exemple : "Cyberpunk": r"D:\Games\Cyberpunk 2077\bin\x64\Cyberpunk2077.exe"
}

# ================== GLOBALES & INIT ==================
gpu_ok = False
nvml_handle = None
cache_cpu_load = 0.0
cache_gpu_load = 0
cache_cpu_temp = "n/a"

def init_gpu():
    global gpu_ok, nvml_handle
    try:
        import pynvml
        pynvml.nvmlInit()
        nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        gpu_ok = True
    except: pass

app = Flask(__name__)

# ================== THREADS DE MONITORING (LISSAGE) ==================

# 1. Thread "Rapide" : Calcule la charge CPU/GPU sur 1 seconde (Moyenne stable)
def performance_thread():
    global cache_cpu_load, cache_gpu_load
    
    while True:
        try:
            cache_cpu_load = psutil.cpu_percent(interval=1.0)

            # GPU : On lit juste après
            if gpu_ok:
                import pynvml
                u = pynvml.nvmlDeviceGetUtilizationRates(nvml_handle)
                cache_gpu_load = u.gpu
            else:
                cache_gpu_load = 0
                
        except Exception:
            pass

def temp_thread():
    global cache_cpu_temp
    if platform.system() != "Windows": return
    try:
        import pythoncom
        pythoncom.CoInitialize()
        import wmi
    except: return

    while True:
        try:
            w = wmi.WMI(namespace=r"root\OpenHardwareMonitor")
            found = False
            for sensor in w.Sensor():
                if sensor.SensorType == "Temperature" and "CPU" in sensor.Name:
                    cache_cpu_temp = round(sensor.Value, 1)
                    found = True
                    break 
            if not found: cache_cpu_temp = "n/a"
        except:
            cache_cpu_temp = "n/a"
        time.sleep(2)


def broadcast_presence():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try: server.sendto(b"PI_HELPER_SERVER_HERE", ('<broadcast>', 5006))
        except: pass
        time.sleep(5)

# ================== ROUTES API ==================
@app.route("/metrics")
def metrics():
    temp_gpu = "n/a"
    if gpu_ok:
        try:
            import pynvml
            temp_gpu = pynvml.nvmlDeviceGetTemperature(nvml_handle, pynvml.NVML_TEMPERATURE_GPU)
        except: pass
        
    return jsonify({
        "cpu": cache_cpu_load, 
        "temp_cpu": cache_cpu_temp,
        "gpu": cache_gpu_load, 
        "temp_gpu": temp_gpu
    })

@app.route("/media", methods=["POST"])
def media():
    try:
        data = request.get_json(force=True) or {}
        cmd = data.get("cmd", "").lower()
        if cmd == "playpause": keyboard.send("play/pause media")
        elif cmd == "next": keyboard.send("next track")
        elif cmd == "prev": keyboard.send("previous track")
        elif cmd == "vol_up": keyboard.send("volume up")
        elif cmd == "vol_down": keyboard.send("volume down")
        elif cmd == "mute_toggle": keyboard.send("volume mute")
        return jsonify({"ok": True})
    except: return jsonify({"ok": False})

@app.route("/launch", methods=["POST"])
def launch():
    try:
        data = request.get_json(force=True) or {}
        app_name = data.get("name", "")
        if app_name in APPS:
            subprocess.Popen(APPS[app_name], shell=True)
            return jsonify({"ok": True, "msg": f"Lancement {app_name}"})
        return jsonify({"ok": False, "msg": "Inconnu"})
    except Exception as e: return jsonify({"ok": False, "msg": str(e)})

@app.route("/mixer/list")
def mixer_list():
    if not AUDIO_OK: return jsonify([])
    sessions_list = []
    seen_names = set()
    
    try:
        pythoncom.CoInitialize()
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            name = None
            if session.Process and session.Process.name():
                name = session.Process.name().replace(".exe", "")
            if not name:
                try: 
                    name = session.DisplayName
                    if name and "@" in name: name = None 
                except: pass
            if name and name.lower() not in ["system", "idle", "", "shell experience host"]:
                if name not in seen_names:
                    vol = session.SimpleAudioVolume.GetMasterVolume()
                    sessions_list.append({"name": name, "vol": int(vol * 100)})
                    seen_names.add(name)
                    print(f"[MIXER] Trouvé: {name} - {int(vol*100)}%")

    except Exception as e:
        print(f"[ERROR] Mixer: {e}")
    
    sessions_list.sort(key=lambda x: x["name"])
    return jsonify(sessions_list)

@app.route("/mixer/set", methods=["POST"])
def mixer_set():
    if not AUDIO_OK: return jsonify({"ok": False})
    try:
        data = request.get_json(force=True)
        target_name = data.get("name")
        change = data.get("change", 0)
        
        pythoncom.CoInitialize()
        sessions = AudioUtilities.GetAllSessions()
        for session in sessions:
            if session.Process and session.Process.name().replace(".exe", "") == target_name:
                vol = session.SimpleAudioVolume.GetMasterVolume()
                new_vol = max(0.0, min(1.0, vol + (change / 100.0)))
                session.SimpleAudioVolume.SetMasterVolume(new_vol, None)
                return jsonify({"ok": True, "new_vol": int(new_vol*100)})
    except: pass
    return jsonify({"ok": False})

@app.route("/apps_list")
def apps_list():
    return jsonify(list(APPS.keys()))

# ================== MAIN ==================
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  
    init_gpu()
    # Démarrage des lisseurs usage CPU/GPU et Température
    threading.Thread(target=broadcast_presence, daemon=True).start()
    threading.Thread(target=temp_thread, daemon=True).start()
    threading.Thread(target=performance_thread, daemon=True).start()

    try:
        app.run(host="0.0.0.0", port=5005, threaded=True, debug=False)
    except Exception as e:
        print(f"Erreur: {e}")