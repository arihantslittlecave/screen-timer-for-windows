"""Extracts application icons from .exe files as base64 PNG data URIs."""

import base64
import io

import win32gui
import win32ui
from PIL import Image

ICON_SIZE = 32
_cache = {}


def _image_from_hicon(hicon):
    """Reads an icon's 32-bit color bitmap directly so its alpha channel survives."""
    info = win32gui.GetIconInfo(hicon)
    hbm_mask, hbm_color = info[3], info[4]
    try:
        if not hbm_color:
            return None
        bitmap = win32ui.CreateBitmapFromHandle(hbm_color)
        details = bitmap.GetInfo()
        width, height = details["bmWidth"], details["bmHeight"]
        if details["bmBitsPixel"] != 32:
            return None

        bits = bitmap.GetBitmapBits(True)
        img = Image.frombuffer("RGBA", (width, height), bits, "raw", "BGRA", 0, 1)

        # Some icons ship a fully transparent alpha channel; those are unusable.
        if img.getchannel("A").getextrema()[1] == 0:
            return None
        return img
    finally:
        for handle in (hbm_mask, hbm_color):
            if handle:
                win32gui.DeleteObject(handle)


def _extract(exe_path):
    large, small = win32gui.ExtractIconEx(exe_path, 0)
    handles = list(large) + list(small)
    if not handles:
        return None
    try:
        for hicon in handles:
            img = _image_from_hicon(hicon)
            if img:
                return img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
        return None
    finally:
        for hicon in handles:
            win32gui.DestroyIcon(hicon)


def get_icon_data_uri(exe_path):
    """Returns a `data:image/png;base64,...` string, or None if extraction fails."""
    if exe_path in _cache:
        return _cache[exe_path]

    result = None
    try:
        img = _extract(exe_path)
        if img:
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            result = f"data:image/png;base64,{encoded}"
    except Exception:
        result = None

    _cache[exe_path] = result
    return result
