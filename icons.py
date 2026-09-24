"""Finds each app's real icon and real name, from its .exe or, for Microsoft
Store apps, from the package the .exe ships in."""

import base64
import ctypes
import glob
import io
import os
import re

import win32api
import win32gui
import win32ui
from PIL import Image

# Twice the size the window draws them at, so they stay sharp on a scaled
# (125%, 150%) display instead of being blown up from 32px.
ICON_SIZE = 64
_icon_cache = {}
_name_cache = {}

# Descriptions that name the platform rather than the app.
_GENERIC_NAMES = {"electron", "chromium", "python", "java(tm) platform se binary", "application"}


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


def _extract_sized(exe_path):
    """The icon at ICON_SIZE, picked by Windows from the sizes the .exe ships,
    rather than the 32px one ExtractIconEx is limited to."""
    hicon = ctypes.c_void_p()
    icon_id = ctypes.c_uint()
    found = ctypes.windll.user32.PrivateExtractIconsW(
        exe_path, 0, ICON_SIZE, ICON_SIZE, ctypes.byref(hicon), ctypes.byref(icon_id), 1, 0
    )
    if found != 1 or not hicon.value:
        return None
    try:
        return _image_from_hicon(hicon.value)
    finally:
        win32gui.DestroyIcon(hicon.value)


def _extract_small(exe_path):
    large, small = win32gui.ExtractIconEx(exe_path, 0)
    handles = list(large) + list(small)
    try:
        for hicon in handles:
            img = _image_from_hicon(hicon)
            if img:
                return img
        return None
    finally:
        for hicon in handles:
            win32gui.DestroyIcon(hicon)


def _package(exe_path):
    """(package folder, AppxManifest.xml text) for a Store app, else None."""
    folder = os.path.dirname(exe_path)
    while "WindowsApps" in folder and os.path.basename(folder) != "WindowsApps":
        manifest = os.path.join(folder, "AppxManifest.xml")
        if os.path.isfile(manifest):
            with open(manifest, encoding="utf8") as f:
                return folder, f.read()
        folder = os.path.dirname(folder)
    return None


def _package_logo(exe_path):
    """Store apps often have no icon inside the .exe at all; the one you see
    in the Start menu is a PNG in the package. Prefers the 'unplated'
    variant, which has no coloured square behind it."""
    package = _package(exe_path)
    if not package:
        return None
    folder, manifest = package
    match = re.search(r'Square44x44Logo="([^"]+)"', manifest)
    if not match:
        return None
    stem = os.path.join(folder, os.path.splitext(match.group(1))[0])

    def target_size(path):
        found = re.search(r"targetsize-(\d+)", path)
        return int(found.group(1)) if found else 0

    for pattern in ("{}.targetsize-*_altform-unplated.png", "{}.targetsize-*.png"):
        sizes = sorted(
            (p for p in glob.glob(pattern.format(glob.escape(stem))) if "lightunplated" not in p),
            key=target_size,
        )
        big_enough = [p for p in sizes if target_size(p) >= ICON_SIZE]
        if big_enough or sizes:
            return Image.open((big_enough or sizes)[-1 if not big_enough else 0]).convert("RGBA")
    for scale in ("200", "400", "150", "100"):
        path = f"{stem}.scale-{scale}.png"
        if os.path.isfile(path):
            return Image.open(path).convert("RGBA")
    return None


def get_icon_data_uri(exe_path):
    """Returns a `data:image/png;base64,...` string, or None if extraction fails."""
    if exe_path in _icon_cache:
        return _icon_cache[exe_path]

    result = None
    try:
        img = None
        if os.path.isfile(exe_path):
            for find in (_extract_sized, _package_logo, _extract_small):
                try:
                    img = find(exe_path)
                except Exception:
                    img = None
                if img:
                    break
        if img:
            img = img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format="PNG", optimize=True)
            encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
            result = f"data:image/png;base64,{encoded}"
    except Exception:
        result = None

    _icon_cache[exe_path] = result
    return result


def _version_description(exe_path):
    lang, codepage = win32api.GetFileVersionInfo(exe_path, "\\VarFileInfo\\Translation")[0]
    key = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
    return win32api.GetFileVersionInfo(exe_path, key)


def _package_display_name(exe_path):
    package = _package(exe_path)
    if not package:
        return None
    folder, manifest = package
    match = re.search(r'VisualElements[^>]*?DisplayName="([^"]+)"', manifest)
    if not match:
        return None
    name = match.group(1)
    if not name.startswith("ms-resource:"):
        return name
    # A pointer into the package's resource table, resolved the same way the
    # Start menu does it.
    full_name = os.path.basename(folder)
    resource = name[len("ms-resource:"):]
    if not resource.startswith("/"):
        resource = "resources/" + resource
    source = f"@{{{full_name}?ms-resource://{full_name.split('_')[0]}/{resource.lstrip('/')}}}"
    out = ctypes.create_unicode_buffer(256)
    if ctypes.windll.shlwapi.SHLoadIndirectString(source, out, len(out), None) == 0:
        return out.value
    return None


def get_display_name(exe_path):
    """The name the app gives itself ("Microsoft Edge", "WhatsApp"), or None
    when it doesn't give a usable one and the caller should fall back."""
    if not exe_path:
        return None
    if exe_path in _name_cache:
        return _name_cache[exe_path]

    name = None
    if os.path.isfile(exe_path):
        for find in (_package_display_name, _version_description):
            try:
                candidate = (find(exe_path) or "").strip()
            except Exception:
                candidate = ""
            if 1 < len(candidate) <= 32 and candidate.lower() not in _GENERIC_NAMES:
                name = candidate
                break

    _name_cache[exe_path] = name
    return name
