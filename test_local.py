"""
VACOL — Teste local da detecção (sem câmera, sem placa)
=======================================================
Roda um vídeo/stream e mostra as vagas livres/ocupadas.

O modo padrão imita o projeto antigo apivagas: cria uma máscara binária do
frame, conta pixels brancos dentro de cada vaga e usa esse valor para decidir
se a vaga está ocupada. Para câmera fixa de estacionamento visto de cima, isso
é mais estável do que usar YOLO genérico.

Instale:
    pip install opencv-python numpy
    pip install ultralytics shapely   # só se quiser testar --mode yolo
    pip install yt-dlp        # só se for testar com live do YouTube

Exemplos:
    python test_local.py
    python test_local.py --camera cam_001
    python test_local.py --spots spots_polygons.json
    python test_local.py --spots spots_polygons.json --pixel-threshold 1500
    python test_local.py --spots spots_polygons.json --pixel-threshold 1000 --free-threshold 300
    python test_local.py --spots spots_polygons.json --show-mask
    python test_local.py --source meu_video.mp4
    python test_local.py --source 0               # webcam do PC
    python test_local.py --source "rtsp://ip:554/stream1"   # câmera IP
    python test_local.py --source "https://www.youtube.com/watch?v=XXXX"  # live do YouTube
    python test_local.py --save saida.mp4         # salva vídeo anotado (PC sem tela)
    python test_local.py --mode yolo --vehicle-classes-only

Tecla Q fecha a janela.
"""

import argparse
import json
import sys

import cv2
import numpy as np

from camera_config import (
    apply_camera_defaults,
    find_camera,
    is_youtube_url,
    load_config,
    resolve_project_path,
    resolve_youtube_stream,
)

VEHICLE_CLASSES = {2, 3, 5, 7}   # car, motorcycle, bus, truck (COCO)


def resolve_source(src):
    """Aceita arquivo, webcam (número), RTSP/HTTP ou link do YouTube."""
    if is_youtube_url(src):
        try:
            return resolve_youtube_stream(src)
        except Exception as e:
            print("Falha ao resolver YouTube:", e)
            sys.exit(1)
    if isinstance(src, str) and src.isdigit():
        return int(src)
    if isinstance(src, str) and "://" not in src:
        return str(resolve_project_path(src))
    return src


def load_spots(path):
    if not path:
        return []

    with open(resolve_project_path(path), encoding="utf-8") as file:
        data = json.load(file)

    spots = []
    for index, spot in enumerate(data, start=1):
        polygon = np.array(spot["polygon"], dtype=np.int32)
        spots.append({
            "id": spot.get("id", f"vaga_{index:03d}"),
            "nome": spot.get("nome", f"Vaga {index:03d}"),
            "tipo": spot.get("tipo", "regular"),
            "latitude": spot.get("latitude"),
            "longitude": spot.get("longitude"),
            "polygon": polygon,
        })
    return spots


def ensure_odd(value):
    value = max(3, int(value))
    return value if value % 2 == 1 else value + 1


def build_pixel_mask(frame, block_size, adaptive_c, blur_size, dilate_size):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    threshold = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        ensure_odd(block_size),
        adaptive_c,
    )
    blurred = cv2.medianBlur(threshold, ensure_odd(blur_size))
    kernel = np.ones((max(1, dilate_size), max(1, dilate_size)), np.uint8)
    return cv2.dilate(blurred, kernel)


def count_spot_pixels(mask, polygon):
    spot_mask = np.zeros(mask.shape[:2], dtype=np.uint8)
    cv2.fillPoly(spot_mask, [polygon], 255)
    masked = cv2.bitwise_and(mask, mask, mask=spot_mask)
    return cv2.countNonZero(masked), cv2.countNonZero(spot_mask)


SPOT_TYPE_TAGS = {"pcd": "[PCD] ", "idoso": "[IDOSO] ", "carga": "[CARGA] "}
SPOT_TYPE_BADGE_COLORS = {"pcd": (255, 0, 0), "idoso": (0, 165, 255)}  # BGR: azul, laranja


def draw_spot(frame, polygon, spot_id, occupied, count, area, tipo="regular"):
    color = (0, 0, 230) if occupied else (0, 179, 0)
    cv2.polylines(frame, [polygon], True, color, 2)

    moments = cv2.moments(polygon)
    if moments["m00"]:
        cx = int(moments["m10"] / moments["m00"])
        cy = int(moments["m01"] / moments["m00"])
    else:
        x, y, w, h = cv2.boundingRect(polygon)
        cx, cy = x + w // 2, y + h // 2

    ratio = count / area if area else 0
    label = f"{SPOT_TYPE_TAGS.get(tipo, '')}{spot_id}: {count} ({ratio:.2f})"

    (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.43, 1)
    cv2.rectangle(frame, (cx - text_w // 2 - 6, cy - text_h - 10), (cx + text_w // 2 + 6, cy + 8), (0, 0, 0), -1)
    cv2.putText(frame, label, (cx - text_w // 2, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (255, 255, 255), 1)

    badge_color = SPOT_TYPE_BADGE_COLORS.get(tipo)
    if badge_color:
        x, y, w, h = cv2.boundingRect(polygon)
        cv2.circle(frame, (x + 10, y + 10), 9, badge_color, -1)
        cv2.circle(frame, (x + 10, y + 10), 9, (255, 255, 255), 1)


def process_pixels(frame, spots, args, spot_states):
    mask = build_pixel_mask(
        frame,
        args.adaptive_block,
        args.adaptive_c,
        args.blur,
        args.dilate,
    )

    free = 0
    for spot in spots:
        count, area = count_spot_pixels(mask, spot["polygon"])
        previous = spot_states.get(spot["id"])

        if previous is None:
            occupied = count >= args.pixel_threshold
        elif previous:
            occupied = count > args.free_threshold
        else:
            occupied = count >= args.pixel_threshold

        spot_states[spot["id"]] = occupied
        if not occupied:
            free += 1
        draw_spot(frame, spot["polygon"], spot["id"], occupied, count, area, spot.get("tipo", "regular"))

    return free, mask


def process_yolo(frame, spots, args, model):
    from shapely.geometry import Polygon, box

    res = model(frame, verbose=False, conf=args.conf, imgsz=args.imgsz)[0]

    occupancy_boxes = []
    for b in res.boxes:
        cls_id = int(b.cls[0])
        if args.vehicle_classes_only and cls_id not in VEHICLE_CLASSES:
            continue

        x1, y1, x2, y2 = map(int, b.xyxy[0].tolist())
        conf = float(b.conf[0])
        label = model.names.get(cls_id, str(cls_id))
        occupancy_boxes.append(box(x1, y1, x2, y2))

        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 255), 2)
        cv2.putText(
            frame,
            f"{label} {conf:.2f}",
            (x1, max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 200, 255),
            1,
        )

    free = 0
    for spot in spots:
        poly = Polygon(spot["polygon"])
        occupied = any(
            poly.area > 0 and poly.intersection(vb).area / poly.area >= args.cover
            for vb in occupancy_boxes
        )
        if not occupied:
            free += 1
        color = (0, 0, 230) if occupied else (0, 179, 0)
        cv2.polylines(frame, [spot["polygon"]], True, color, 2)

    return free


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/cameras.json")
    ap.add_argument("--camera", default=None, help="id da câmera cadastrada. Ex: cam_001")
    ap.add_argument("--source", default=None)
    ap.add_argument("--spots", default=None, help="JSON com os polígonos das vagas (opcional)")
    ap.add_argument("--mode", choices=("pixels", "yolo"), default="pixels")
    ap.add_argument("--model", default="yolov8n.pt")
    ap.add_argument("--save", default=None, help="salva vídeo anotado, ex: saida.mp4")
    ap.add_argument("--pixel-threshold", type=int, default=None, help="pixels brancos para considerar ocupada")
    ap.add_argument(
        "--free-threshold",
        type=int,
        default=None,
        help="pixels brancos para uma vaga ocupada voltar a livre",
    )
    ap.add_argument("--adaptive-block", type=int, default=None)
    ap.add_argument("--adaptive-c", type=int, default=None)
    ap.add_argument("--blur", type=int, default=None)
    ap.add_argument("--dilate", type=int, default=None)
    ap.add_argument("--show-mask", action="store_true", help="mostra a máscara processada")
    ap.add_argument("--conf", type=float, default=0.10)
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--cover", type=float, default=0.15, help="fração da vaga coberta p/ contar como ocupada")
    ap.add_argument(
        "--vehicle-classes-only",
        action="store_true",
        help="usa apenas classes COCO de veículos; melhor para câmera comum, pior para câmera aérea",
    )
    args = ap.parse_args()

    config = load_config(args.config)
    camera = find_camera(config, args.camera)
    apply_camera_defaults(args, camera)
    print(f"Camera: {camera['id']} - {camera.get('rua', camera.get('nome', ''))}")

    model = None
    if args.mode == "yolo":
        from ultralytics import YOLO

        print("Carregando o modelo (baixa o yolov8n.pt na primeira vez)...")
        model = YOLO(args.model)

    spots = load_spots(args.spots)
    if spots:
        print(f"{len(spots)} vagas carregadas de {args.spots}")
    elif args.mode == "pixels":
        print("Nenhum arquivo de vagas informado. Use: --spots spots_polygons.json")

    src = resolve_source(args.source)
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print("Não consegui abrir a fonte:", args.source)
        sys.exit(1)

    writer = None
    if args.save:
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 20
        writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))

    spot_states = {}

    while True:
        ok, frame = cap.read()
        if not ok:
            # arquivo acabou? volta pro começo (loop). Stream ao vivo: encerra.
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
            if not ok:
                break

        mask = None
        if args.mode == "pixels":
            free, mask = process_pixels(frame, spots, args, spot_states)
        else:
            free = process_yolo(frame, spots, args, model)

        # Painel de status
        txt = f"Livres: {free}/{len(spots)}" if spots else "Sem vagas"
        cv2.rectangle(frame, (10, 10), (18 + 13 * len(txt), 46), (0, 0, 0), -1)
        cv2.putText(frame, txt, (18, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

        if writer:
            writer.write(frame)
        cv2.imshow("VACOL - teste (Q p/ sair)", frame)
        if args.show_mask and mask is not None:
            cv2.imshow("VACOL - mascara processada", mask)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    if writer:
        writer.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
