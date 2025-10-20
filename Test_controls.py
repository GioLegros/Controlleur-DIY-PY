import RPi.GPIO as GPIO
import time

# -------------------------------
# Configuration GPIO
# -------------------------------
buttons = {
    17: "Bouton 1",
    27: "Bouton 2",
    22: "Bouton 3",
    5:  "Bouton 4"
}

encoder_clk = 6
encoder_dt = 13
encoder_sw = 19

GPIO.setmode(GPIO.BCM)

# Configure les boutons avec pull-up interne
for pin in buttons:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Configure l'encodeur
GPIO.setup(encoder_clk, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(encoder_dt, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(encoder_sw, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# -------------------------------
# Variables pour l'encodeur
# -------------------------------
last_clk = GPIO.input(encoder_clk)
counter = 0

print("=== Test des boutons et encodeur EC11 ===")
print("Appuie sur les boutons ou tourne la molette (CTRL+C pour quitter)\n")

try:
    while True:
        # --- Boutons ---
        for pin, name in buttons.items():
            if GPIO.input(pin) == GPIO.LOW:
                print(f"{name} pressé")
                time.sleep(0.2)

        # --- Encodeur rotatif ---
        clk_state = GPIO.input(encoder_clk)
        dt_state = GPIO.input(encoder_dt)

        if clk_state != last_clk:
            if dt_state == clk_state:
                counter += 1
                print(f"Rotation → sens horaire ({counter})")
            else:
                counter -= 1
                print(f"Rotation ← sens antihoraire ({counter})")
        last_clk = clk_state

        # --- Poussoir du codeur ---
        if GPIO.input(encoder_sw) == GPIO.LOW:
            print("Bouton du codeur pressé")
            time.sleep(0.3)

        time.sleep(0.01)

except KeyboardInterrupt:
    print("\nFin du test.")
    GPIO.cleanup()
