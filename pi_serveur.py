from flask import Flask, jsonify, request
import threading, time, psutil
import keyboard
import platform
from ctypes import POINTER, cast
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume




# ---- GPU / Temp (NVIDIA via NVML si dispo) ----
gpu_ok = False
try:
    import pynvml
    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    gpu_ok = True
except Exception:
    gpu_ok = False

# ---- Volume (Windows, via pycaw) ----


sessions = AudioUtilities.GetSpeakers()
volume = cast(sessions.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))

app = Flask(__name__)

def clamp(v, a, b): return max(a, min(b, v))

@app.route("/media", methods=["POST"])
def media():
    data = request.get_json(force=True) or {}
    cmd  = data.get("cmd","").lower()
    if cmd == "playpause":
        keyboard.send("play/pause media")
    elif cmd == "next":
        keyboard.send("next track")
    elif cmd == "prev":
        keyboard.send("previous track")
    elif cmd == "seek0":
        # Pas d'événement clavier standard → on laisse le Pi faire via l’API Spotify
        pass
    elif cmd == "vol_up":
        cur = volume.GetMasterVolumeLevelScalar()
        volume.SetMasterVolumeLevelScalar(clamp(cur + 0.05, 0.0, 1.0), None)
        keyboard.send("volume up")
    elif cmd == "vol_down":
        cur = volume.GetMasterVolumeLevelScalar()
        volume.SetMasterVolumeLevelScalar(clamp(cur - 0.05, 0.0, 1.0), None)
        keyboard.send("volume down")
    elif cmd == "mute":
        volume.SetMute(1, None)
    elif cmd == "unmute":
        volume.SetMute(0, None)
    return jsonify({"ok": True})



cpu_value = 0.0

def update_cpu():
    global cpu_value
    while True:
        cpu_value = psutil.cpu_percent(interval=1)
        time.sleep(1)

threading.Thread(target=update_cpu, daemon=True).start()

@app.route("/metrics")
def metrics():
    if platform.system() == "Windows":
        import wmi
        w = wmi.WMI(namespace="root\\wmi")

    temp_cpu = "n/a"
    try:
        if platform.system() == "Windows":
            temperature_info = w.MSAcpi_ThermalZoneTemperature()
            if temperature_info:
                temp_cpu = round((temperature_info[0].CurrentTemperature / 10.0) - 273.15, 1)
        else:
            t = psutil.sensors_temperatures()
            if t:
                for k, v in t.items():
                    if v:
                        temp_cpu = v[0].current
                        break
    except Exception:
        pass

    gpu = "n/a"; temp_gpu = "n/a"
    if gpu_ok:
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            gpu = util.gpu
            temp_gpu = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
        except Exception:
            pass

    return jsonify({
        "cpu": round(cpu_value,1),
        "temp_cpu": temp_cpu,
        "gpu": gpu,
        "temp_gpu": temp_gpu
    })

if __name__ == "__main__":
    # Écoute sur toutes interfaces du PC (réseau local)
    app.run(host="0.0.0.0", port=5005, debug=False)
