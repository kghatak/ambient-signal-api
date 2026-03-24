#!/usr/bin/env python3
"""
Inject dummy ambient signal data to the API
"""
import requests
import random
import time
import math
from datetime import datetime

API_URL = "https://ambient-signal-api.onrender.com/signals/batch"

# Simulate realistic sensor patterns
def get_temperature():
    # Base temp with sine wave (day/night cycle simulation) + noise
    base = 24
    noise = random.uniform(-1, 1)
    return round(base + noise, 1)

def get_humidity():
    # Humidity inversely related to temp + noise
    base = 55
    noise = random.uniform(-5, 5)
    return round(base + noise, 1)

def get_light():
    # Light level with more variance
    base = 400
    noise = random.uniform(-100, 100)
    return round(max(0, base + noise), 1)

def get_noise():
    # Ambient noise level
    base = 35
    noise = random.uniform(-5, 10)
    return round(base + noise, 1)

def send_batch(device_id="phone1"):
    payload = {
        "device_id": device_id,
        "readings": [
            {"signal_type": "temperature", "value": get_temperature(), "unit": "celsius"},
            {"signal_type": "humidity", "value": get_humidity(), "unit": "percent"},
            {"signal_type": "light", "value": get_light(), "unit": "lux"},
            {"signal_type": "noise", "value": get_noise(), "unit": "db"},
        ]
    }

    try:
        response = requests.post(API_URL, json=payload, timeout=5)
        return response.json()
    except Exception as e:
        return {"error": str(e)}

def main():
    print("Starting data injection...")
    print(f"API: {API_URL}")
    print("Press Ctrl+C to stop\n")

    count = 0
    while True:
        count += 1
        result = send_batch()
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] Batch #{count}: {result}")
        time.sleep(1)  # Send every 1 second

if __name__ == "__main__":
    main()
