"""
VACOL — API real
================
Roda a deteccao de TODAS as cameras cadastradas em config/cameras.json em
background (uma thread por camera) e expoe o estado atual via HTTP, no
mesmo formato que o mock (vacol-api-fake.php) e o app mobile ja esperam.

Rodar:
    python api_server.py
    python api_server.py --port 8000 --config config/cameras.json

Endpoints:
    GET  /api/health
    POST /api/login    (sem auth real ainda — so estrutura, igual o mock)
    GET  /api/spots?lat=-19.53&lng=-40.63&radius=450
"""

import argparse
import secrets
import threading
import time
from datetime import datetime, timezone
from math import atan2, cos, radians, sin, sqrt

import cv2
from flask import Flask, jsonify, request

from camera_config import (
    apply_camera_defaults,
    is_youtube_url,
    load_config,
    resolve_project_path,
    resolve_youtube_stream,
)
from test_local import build_pixel_mask, count_spot_pixels, load_spots

RECONNECT_DELAY_S = 5


class CameraWorker:
    """Le uma camera continuamente e mantem o estado atual de cada vaga em memoria."""

    def __init__(self, camera):
        self.camera = camera
        self.id = camera["id"]

        class Args:
            pass

        args = Args()
        apply_camera_defaults(args, camera)
        self.args = args

        self.spots = load_spots(camera["spots_file"])
        self.spot_states = {}   # spot_id -> bool (histerese, igual ao test_local.py)
        self.state = {}         # spot_id -> {"occupied": bool, "updated_at": iso}
        self.lock = threading.Lock()
        self.alive = False

    def resolve_source(self):
        src = self.camera["source"]
        if is_youtube_url(src):
            return resolve_youtube_stream(src)
        if isinstance(src, str) and src.isdigit():
            return int(src)
        if isinstance(src, str) and "://" not in src:
            return str(resolve_project_path(src))
        return src

    def run(self):
        """Fica reconectando pra sempre; nunca deve derrubar a thread."""
        while True:
            try:
                self._run_once()
            except Exception as e:
                print(f"[{self.id}] erro: {e}")
            self.alive = False
            time.sleep(RECONNECT_DELAY_S)

    def _run_once(self):
        src = self.resolve_source()
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            raise RuntimeError("nao consegui abrir a fonte")

        self.alive = True
        print(f"[{self.id}] conectado")
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("perdi o sinal do video")
                self._process_frame(frame)
        finally:
            cap.release()

    def _process_frame(self, frame):
        mask = build_pixel_mask(
            frame,
            self.args.adaptive_block,
            self.args.adaptive_c,
            self.args.blur,
            self.args.dilate,
        )
        now = datetime.now(timezone.utc).isoformat()

        with self.lock:
            for spot in self.spots:
                count, _area = count_spot_pixels(mask, spot["polygon"])
                previous = self.spot_states.get(spot["id"])

                if previous is None:
                    occupied = count >= self.args.pixel_threshold
                elif previous:
                    occupied = count > self.args.free_threshold
                else:
                    occupied = count >= self.args.pixel_threshold

                self.spot_states[spot["id"]] = occupied
                self.state[spot["id"]] = {"occupied": occupied, "updated_at": now}

    def snapshot(self):
        with self.lock:
            return dict(self.state)


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000.0
    d_lat = radians(lat2 - lat1)
    d_lon = radians(lon2 - lon1)
    a = sin(d_lat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(d_lon / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


app = Flask(__name__)
workers = {}


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


@app.route("/<path:_any>", methods=["OPTIONS"])
@app.route("/", methods=["OPTIONS"])
def cors_preflight(_any=None):
    return "", 204


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "VACOL API",
        "time": datetime.now(timezone.utc).isoformat(),
        "cameras": [{"id": cid, "online": w.alive} for cid, w in workers.items()],
    })


@app.route("/api/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    return jsonify({
        "ok": True,
        "token": "demo." + secrets.token_hex(8),
        "user": {"name": body.get("email", "Visitante"), "email": body.get("email")},
    })


@app.route("/api/spots", methods=["GET"])
def spots():
    lat = float(request.args.get("lat", -23.5613))
    lng = float(request.args.get("lng", -46.6565))
    radius = int(request.args.get("radius", 450))

    result = []
    for cid, worker in workers.items():
        state = worker.snapshot()
        for spot in worker.spots:
            if spot["latitude"] is None or spot["longitude"] is None:
                continue
            info = state.get(spot["id"])
            if info is None:
                continue  # ainda nao processou nenhum frame dessa vaga

            dist = haversine(lat, lng, spot["latitude"], spot["longitude"])
            if dist > radius:
                continue

            result.append({
                "id": spot["id"],
                "lat": spot["latitude"],
                "lng": spot["longitude"],
                "status": "occupied" if info["occupied"] else "free",
                "type": spot.get("tipo", "regular"),
                "street": worker.camera.get("rua") or worker.camera.get("nome", ""),
                # deteccao por pixel nao gera confianca real (isso so existe no modo --mode yolo).
                "confidence": 0.95,
                "camera_id": cid,
                "distance_m": int(round(dist)),
                "updated_at": info["updated_at"],
            })

    result.sort(key=lambda s: s["distance_m"])
    free = [s for s in result if s["status"] == "free"]

    return jsonify({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "center": {"lat": lat, "lng": lng},
        "radius_m": radius,
        "summary": {
            "total": len(result),
            "free": len(free),
            "occupied": len(result) - len(free),
        },
        "spots": result,
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/cameras.json")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    config = load_config(args.config)
    for camera in config.get("cameras", []):
        worker = CameraWorker(camera)
        workers[camera["id"]] = worker
        threading.Thread(target=worker.run, daemon=True).start()
        print(f"Camera {camera['id']} iniciada ({camera.get('rua', '')})")

    app.run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
