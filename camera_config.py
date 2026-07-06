import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "cameras.json"


def resolve_project_path(path):
    if path is None:
        return None

    resolved = Path(path)
    if resolved.is_absolute():
        return resolved
    return PROJECT_ROOT / resolved


def is_youtube_url(source):
    return isinstance(source, str) and ("youtube.com" in source or "youtu.be" in source)


def resolve_youtube_stream(url):
    """Troca um link do YouTube (live ou video) pela URL real do stream (HLS/mp4).

    nocheckcertificate=True porque, nesta maquina, a cadeia de certificados do
    Windows/OpenSSL 1.1.1 do venv nao valida os certificados da API do YouTube
    (erro CERTIFICATE_VERIFY_FAILED), mesmo com certifi atualizado.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        print("Falta instalar o yt-dlp: pip install yt-dlp")
        sys.exit(1)

    opts = {"quiet": True, "format": "best[ext=mp4]/best", "nocheckcertificate": True}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info["url"]


def load_config(path=None):
    config_path = resolve_project_path(path) if path else DEFAULT_CONFIG_PATH
    with open(config_path, encoding="utf-8") as file:
        return json.load(file)


def find_camera(config, camera_id=None):
    selected_id = camera_id or config.get("default_camera_id")
    for camera in config.get("cameras", []):
        if camera.get("id") == selected_id:
            return camera
    available = ", ".join(camera.get("id", "?") for camera in config.get("cameras", []))
    raise ValueError(f"Camera '{selected_id}' nao encontrada. Disponiveis: {available}")


def apply_camera_defaults(args, camera):
    processing = camera.get("processing", {})

    defaults = {
        "source": camera.get("source"),
        "spots": camera.get("spots_file"),
        "pixel_threshold": camera.get("pixel_threshold", 1500),
        "free_threshold": camera.get("free_threshold", 300),
        "adaptive_block": processing.get("adaptive_block", 25),
        "adaptive_c": processing.get("adaptive_c", 16),
        "blur": processing.get("blur", 5),
        "dilate": processing.get("dilate", 3),
    }

    for name, value in defaults.items():
        if getattr(args, name, None) is None:
            setattr(args, name, value)

    return args
