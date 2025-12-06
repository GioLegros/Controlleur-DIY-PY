from flask import Flask, jsonify, request
import threading, time, psutil, platform, keyboard, subprocess, os
import socket

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

# ================== GPU INIT (Optionnel) ==================
gpu_ok = False
try:
    import pynvml
    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    gpu_ok = True
except Exception:
    gpu_ok = False

app = Flask(__name__)

# ================== AUTO-DECOUVERTE (Broadcast) ==================
def broadcast_presence():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    server.settimeout(0.2)
    message = b"PI_HELPER_SERVER_HERE"
    
    print("[SYSTEM] Diffusion de la présence sur le port 5006...")
    while True:
        try:
            # Envoie à tout le réseau local
            server.sendto(message, ('<broadcast>', 5006))
        except Exception:
            pass
        time.sleep(3)

threading.Thread(target=broadcast_presence, daemon=True).start()

# ================== MONITORING CPU (WMI) ==================
temp_cpu_cache = "n/a"
def wmi_monitor_thread():
    global temp_cpu_cache
    if platform.system() != "Windows": return
    import pythoncom, importlib
    pythoncom.CoInitialize()
    
    while True:
        try:
            wmi = importlib.import_module("wmi")
            w = wmi.WMI(namespace=r"root\OpenHardwareMonitor")
            found = False
            for sensor in w.Sensor():
                if sensor.SensorType == "Temperature" and "CPU" in sensor.Name:
                    temp_cpu_cache = round(sensor.Value, 1)
                    found = True
                    break
            if not found:
                w_std = wmi.WMI(namespace=r"root\wmi")
                temps = w_std.MSAcpi_ThermalZoneTemperature()
                if temps:
                    temp_cpu_cache = round((temps[0].CurrentTemperature / 10.0) - 273.15, 1)
        except:
            temp_cpu_cache = "n/a"
        time.sleep(5)

threading.Thread(target=wmi_monitor_thread, daemon=True).start()

cpu_cache = 0.0
def cpu_monitor():
    global cpu_cache
    while True:
        cpu_cache = psutil.cpu_percent(interval=1)
threading.Thread(target=cpu_monitor, daemon=True).start()

# ================== ROUTES API ==================
@app.route("/media", methods=["POST"])
def media():
    data = request.get_json(force=True) or {}
    cmd = data.get("cmd", "").lower()
    
    # --- Commandes via simulation Clavier (Compatible VoiceMeeter) ---
    if cmd == "playpause":
        keyboard.send("play/pause media")
    elif cmd == "next":
        keyboard.send("next track")
    elif cmd == "prev":
        keyboard.send("previous track")
        
    # Volume & Mute
    elif cmd == "vol_up":
        keyboard.send("volume up")
    elif cmd == "vol_down":
        keyboard.send("volume down")
    elif cmd == "mute_toggle":
        keyboard.send("volume mute")
        
    return jsonify({"ok": True})

# --- LANCER LES APPS ---
@app.route("/launch", methods=["POST"])
def launch():
    try:
        data = request.get_json(force=True) or {}
        app_name = data.get("name", "")
        
        if app_name in APPS:
            path = APPS[app_name]
            print(f"[SYSTEM] Lancement de : {app_name}")
            subprocess.Popen(path, shell=True)
            return jsonify({"ok": True, "msg": f"Lancement de {app_name}"})
        else:
            return jsonify({"ok": False, "msg": "App inconnue"})
    except Exception as e:
        print(f"[ERROR] Launch: {e}")
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/metrics")
def metrics():
    temp_gpu = "n/a"
    load_gpu = 0
    if gpu_ok:
        try:
            temp_gpu = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            import subprocess, re
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                text=True, timeout=0.5
            )
            load_gpu = float(re.findall(r"\d+", out)[0])
        except: pass

    return jsonify({
        "cpu": round(cpu_cache, 1),
        "temp_cpu": temp_cpu_cache,
        "gpu": load_gpu,
        "temp_gpu": temp_gpu
    })

# ================== ROUTE LISTE APPS ==================
@app.route("/apps_list")
def apps_list():
    return jsonify(list(APPS.keys()))

if __name__ == "__main__":
    psutil.cpu_percent(interval=None) # Init CPU
    print("Ecoute sur le port 5005...")
    app.run(host="0.0.0.0", port=5005, debug=False)