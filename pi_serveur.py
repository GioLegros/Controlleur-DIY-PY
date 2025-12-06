from flask import Flask, jsonify, request
import threading, time, psutil, platform, keyboard
import socket

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
# Le PC crie "Je suis là" toutes les 3 sec pour que le Pi le trouve
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
            # Essai via OpenHardwareMonitor (souvent plus fiable pour les Ryzen/Intel récents)
            w = wmi.WMI(namespace=r"root\OpenHardwareMonitor")
            found = False
            for sensor in w.Sensor():
                if sensor.SensorType == "Temperature" and "CPU" in sensor.Name:
                    temp_cpu_cache = round(sensor.Value, 1)
                    found = True
                    break
            # Fallback standard si OHM n'est pas installé
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
        keyboard.send("volume mute") # Bascule Mute/Unmute
        
    return jsonify({"ok": True})

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

if __name__ == "__main__":
    print("--- Pi Helper Server (VoiceMeeter Edition) ---")
    print("Ecoute sur le port 5005...")
    app.run(host="0.0.0.0", port=5005, debug=False)