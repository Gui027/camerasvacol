"""
Ferramenta simples para desenhar vagas em cima de um frame do video.

Uso:
    python calibrate.py --camera cam_001
    python calibrate.py carPark.mp4
    python calibrate.py carPark.mp4 --output spots_polygons.json

Controles:
    Clique esquerdo: adiciona ponto
    N: fecha a vaga atual e comeca a proxima
    U: desfaz o ultimo ponto
    R: limpa tudo
    S: salva
    Q ou Esc: sai
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from camera_config import (
    find_camera,
    is_youtube_url,
    load_config,
    resolve_project_path,
    resolve_youtube_stream,
)


WINDOW = "VACOL - calibracao de vagas"


def load_frame(source, frame_index):
    if is_youtube_url(source):
        try:
            src = resolve_youtube_stream(source)
        except Exception as e:
            print("Falha ao resolver YouTube:", e)
            sys.exit(1)
    elif str(source).isdigit():
        src = int(source)
    elif isinstance(source, str) and "://" not in source:
        src = str(resolve_project_path(source))
    else:
        src = source

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print("Nao consegui abrir a fonte:", source)
        sys.exit(1)

    if frame_index > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)

    ok, frame = cap.read()
    cap.release()

    if not ok:
        print("Nao consegui ler um frame da fonte:", source)
        sys.exit(1)

    return frame


def draw_overlay(base_frame, spots, current_points):
    frame = base_frame.copy()

    for index, polygon in enumerate(spots, start=1):
        pts = np.array(polygon, dtype=np.int32)
        cv2.polylines(frame, [pts], True, (0, 179, 0), 2)
        cv2.fillPoly(frame, [pts], (0, 80, 0))
        cv2.putText(
            frame,
            str(index),
            tuple(pts[0]),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (255, 255, 255),
            2,
        )

    if current_points:
        pts = np.array(current_points, dtype=np.int32)
        for point in current_points:
            cv2.circle(frame, point, 4, (0, 0, 255), -1)
        if len(current_points) > 1:
            cv2.polylines(frame, [pts], False, (0, 0, 255), 2)

    help_lines = [
        "Clique: ponto | N: fechar vaga | U: desfazer | R: limpar | S: salvar | Q/Esc: sair",
        f"Vagas prontas: {len(spots)} | Pontos na vaga atual: {len(current_points)}",
    ]
    y = 28
    for line in help_lines:
        cv2.rectangle(frame, (10, y - 22), (18 + 9 * len(line), y + 8), (0, 0, 0), -1)
        cv2.putText(frame, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        y += 30

    return frame


def save_spots(path, spots, camera_id=None, existing_metadata=None):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    prefix = f"{camera_id}_" if camera_id else ""
    existing_metadata = existing_metadata or []
    data = []

    for index, polygon in enumerate(spots, start=1):
        previous = existing_metadata[index - 1] if index <= len(existing_metadata) else {}
        data.append({
            "id": previous.get("id", f"{prefix}vaga_{index:03d}"),
            "nome": previous.get("nome", f"Vaga {index:03d}"),
            "tipo": previous.get("tipo", "regular"),
            "latitude": previous.get("latitude"),
            "longitude": previous.get("longitude"),
            "polygon": [[int(x), int(y)] for x, y in polygon],
        })

    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    print(f"Salvo: {path} ({len(spots)} vagas)")


def load_existing_spots(path):
    if not path or not Path(path).exists():
        return [], []

    with open(path, encoding="utf-8") as file:
        data = json.load(file)

    spots = [
        [(int(x), int(y)) for x, y in spot["polygon"]]
        for spot in data
    ]
    metadata = [
        {
            "id": spot.get("id"),
            "nome": spot.get("nome"),
            "tipo": spot.get("tipo", "regular"),
            "latitude": spot.get("latitude"),
            "longitude": spot.get("longitude"),
        }
        for spot in data
    ]
    return spots, metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", default=None, help="video, imagem ou webcam. Ex: carPark.mp4")
    parser.add_argument("--config", default="config/cameras.json")
    parser.add_argument("--camera", default=None, help="id da câmera cadastrada. Ex: cam_001")
    parser.add_argument("--output", default=None)
    parser.add_argument("--frame", type=int, default=0, help="numero do frame usado como base")
    args = parser.parse_args()

    camera_id = None
    if args.camera or not args.source:
        config = load_config(args.config)
        camera = find_camera(config, args.camera)
        camera_id = camera["id"]
        args.source = args.source or camera.get("source")
        args.output = args.output or camera.get("spots_file")
        print(f"Camera: {camera['id']} - {camera.get('rua', camera.get('nome', ''))}")

    args.output = args.output or "spots_polygons.json"
    output_path = resolve_project_path(args.output)

    frame = load_frame(args.source, args.frame)
    spots, existing_metadata = load_existing_spots(output_path)
    current_points = []
    if spots:
        print(f"{len(spots)} vagas existentes carregadas de {output_path}")

    def on_mouse(event, x, y, flags, userdata):
        if event == cv2.EVENT_LBUTTONDOWN:
            current_points.append((x, y))

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW, on_mouse)

    while True:
        cv2.imshow(WINDOW, draw_overlay(frame, spots, current_points))
        key = cv2.waitKey(20) & 0xFF

        if key in (ord("q"), 27):
            break
        if key == ord("u") and current_points:
            current_points.pop()
        elif key == ord("r"):
            spots.clear()
            current_points.clear()
        elif key == ord("n"):
            if len(current_points) < 3:
                print("Uma vaga precisa de pelo menos 3 pontos.")
            else:
                spots.append(current_points.copy())
                current_points.clear()
        elif key == ord("s"):
            if current_points:
                if len(current_points) < 3:
                    print("A vaga atual tem menos de 3 pontos e nao foi salva.")
                else:
                    spots.append(current_points.copy())
                    current_points.clear()
            save_spots(output_path, spots, camera_id, existing_metadata)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
