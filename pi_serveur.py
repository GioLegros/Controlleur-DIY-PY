from flask import Flask, jsonify, request
import threading, time, psutil
import keyboard

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
from ctypes import POINTER, cast
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

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

@app.route("/metrics")
def metrics():
    cpu = psutil.cpu_percent(interval=1)
    temp_cpu = "n/a"
    try:
        t = psutil.sensors_temperatures()
        if t:
            # heuristique courante sous Windows + util cartes mères
            for k,v in t.items():
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
        "cpu": round(cpu,1),
        "temp_cpu": temp_cpu,
        "gpu": gpu,
        "temp_gpu": temp_gpu
    })

if __name__ == "__main__":
    # Écoute sur toutes interfaces du PC (réseau local)
    app.run(host="0.0.0.0", port=5005, debug=False)
