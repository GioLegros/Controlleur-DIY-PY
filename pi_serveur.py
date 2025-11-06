from flask import Flask, jsonify, request
import threading, time, psutil, platform, keyboard, pythoncom
from ctypes import POINTER, cast
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
import subprocess, re

# GPU / NVML
gpu_ok = False
try:
    import pynvml
    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    gpu_ok = True
except Exception:
    gpu_ok = False

# Volume
sessions = AudioUtilities.GetSpeakers()
volume = cast(
    sessions.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
    POINTER(IAudioEndpointVolume)
)

app = Flask(__name__)

# ======================================================
#   THREAD WMI (pour température CPU)
# ======================================================
temp_cpu_cache = "n/a"

def wmi_monitor_thread():
    global temp_cpu_cache
    if platform.system() != "Windows":
        return
    pythoncom.CoInitialize()  # initialise COM avant d’importer wmi
    import importlib
    wmi = importlib.import_module("wmi")

    w = None
    while True:
        try:
            if w is None:
                try:
                    w = wmi.WMI(namespace=r"root\wmi")
                except Exception:
                    try:
                        w = wmi.WMI(namespace=r"root\OpenHardwareMonitor")
                    except Exception:
                        w = None
                        temp_cpu_cache = "n/a"
            if w:
                try:
                    temps = w.MSAcpi_ThermalZoneTemperature()
                    if temps:
                        temp_cpu_cache = round(
                            (temps[0].CurrentTemperature / 10.0) - 273.15, 1
                        )
                    else:
                        # fallback via OpenHardwareMonitor
                        for sensor in w.Sensor():
                            if sensor.SensorType == "Temperature" and "CPU" in sensor.Name:
                                temp_cpu_cache = round(sensor.Value, 1)
                                break
                except Exception:
                    temp_cpu_cache = "n/a"
        except Exception as e:
            print("[WMI thread]", e)
            temp_cpu_cache = "n/a"
        time.sleep(5)

# Lancer le thread au démarrage
threading.Thread(target=wmi_monitor_thread, daemon=True).start()

# ======================================================
#   ROUTES
# ======================================================
@app.route("/media", methods=["POST"])
def media():
    data = request.get_json(force=True) or {}
    cmd = data.get("cmd", "").lower()
    if cmd == "playpause":
        keyboard.send("play/pause media")
    elif cmd == "next":
        keyboard.send("next track")
    elif cmd == "prev":
        keyboard.send("previous track")
    elif cmd == "vol_up":
        cur = volume.GetMasterVolumeLevelScalar()
        volume.SetMasterVolumeLevelScalar(min(cur + 0.05, 1.0), None)
    elif cmd == "vol_down":
        cur = volume.GetMasterVolumeLevelScalar()
        volume.SetMasterVolumeLevelScalar(max(cur - 0.05, 0.0), None)
    elif cmd == "mute":
        volume.SetMute(1, None)
    elif cmd == "unmute":
        volume.SetMute(0, None)
    return jsonify({"ok": True})


cpu_cache = 0.0
def cpu_monitor():
    global cpu_cache
    while True:
        cpu_cache = psutil.cpu_percent(interval=1)
threading.Thread(target=cpu_monitor, daemon=True).start()

def read_gpu_load_smi():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            text=True, timeout=0.3
        )
        return float(re.findall(r"\d+", out)[0])
    except Exception:
        return 0.0

@app.route("/metrics")
def metrics():
    temp_cpu = temp_cpu_cache
    gpu = "n/a"
    gpu = max(0, read_gpu_load_smi() - 25)
    temp_gpu = "n/a"
    if gpu_ok:
        try:
            temp_gpu = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            pass

    return jsonify({
        "cpu": round(cpu_cache, 1),
        "temp_cpu": temp_cpu,
        "gpu": gpu,
        "temp_gpu": temp_gpu
    })


# ======================================================
#   LANCEMENT
# ======================================================
if __name__ == "__main__":
    print("Pi Helper Server sur 0.0.0.0:5005")
    print(" - GPU NVML :", gpu_ok)
    print(" - Système :", platform.system())
    app.run(host="0.0.0.0", port=5005, debug=False)
