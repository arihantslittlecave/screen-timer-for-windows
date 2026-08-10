# Screen Timer

A screen-time tracker for Windows — the desktop equivalent of Android's Digital
Wellbeing. It sits in your tray, records which apps you actually use, and nudges
you to take breaks.

Free, open source, no account, no telemetry. Nothing leaves your machine.

---

## Install

1. Download **`ScreenTimer.exe`** from
   [Releases](../../releases/latest).
2. Double-click it.

That's it. No installer, no Python, no dependencies.

On first launch it sets itself up: starts tracking, adds itself to your startup
apps, and pins its icon to the taskbar corner. Nothing to configure.

### "Windows protected your PC"

You'll probably see a blue SmartScreen warning. That happens to every app from a
developer who hasn't bought a code-signing certificate (they cost hundreds a
year), not because anything is wrong with the file.

Click **More info** → **Run anyway**.

If you'd rather not take that on faith, the entire source is in this repo and
you can build the `.exe` yourself — see [Building](#building).

---

## Using it

| | |
|---|---|
| **Open it** | Click the orange icon in the taskbar corner |
| **Close the window** | Click X — it keeps tracking in the background |
| **Quit properly** | Right-click the tray icon → Quit |
| **Switch light/dark** | The button in the title bar |

The window shows today's total, a per-app breakdown with real app icons, and 7-
or 30-day history. Click any day in the chart to see that day's breakdown.

### Settings

- **Break every** — how long before it reminds you to take a break
- **Daily limit** — your target; the dial fills up as you approach it, and the
  line under the total shows how much of it you've used
- **Per-app limits** — click the small icon at the right of any app row

### Stop it starting automatically

Task Manager → **Startup apps** → Screen Timer → Disable. It won't turn itself
back on.

---

## What it records, and where

Everything stays local, in `%APPDATA%\ScreenTimer\`:

| File | What |
|---|---|
| `data.json` | Per-day totals and per-app seconds |
| `settings.json` | Your break interval, daily limit, app limits |
| `app_paths.json` | Where each app's `.exe` lives, used to read its icon |
| `error.log` | Written only if something goes wrong |

To delete your history, delete that folder.

It records the app you're actively using, and stops counting after 60 seconds
with no keyboard or mouse input, so time away from the machine isn't counted.
Windows shell surfaces — the lock screen, Start menu, search — are ignored, so
your list is only apps you actually chose to use.

---

## Building

Requires Python 3.11+ on Windows.

```bash
git clone https://github.com/YOUR-USERNAME/screen-timer.git
cd screen-timer
pip install -r requirements.txt
pyinstaller screen-timer.spec
```

The `.exe` lands in `dist/`. To run from source without packaging:

```bash
python main.py
```

---

## How it works

| File | Role |
|---|---|
| `main.py` | Tray icon, window, tracking loop, notifications |
| `api.py` | The bridge the UI calls into |
| `storage.py` | Reading and writing the JSON, day rollover |
| `active_window.py` | Which app currently has focus |
| `idle.py` | How long since the last input |
| `icons.py` | Pulls real icons out of `.exe` files |
| `icon_art.py` | Draws the app's own tray badge |
| `autostart.py` | The startup registry entry |
| `first_run.py` | One-time setup on first launch |
| `paths.py` | Bundled resources vs. user data locations |
| `ui/` | The interface — plain HTML, CSS, and JavaScript |

The UI is a local HTML page rendered by [pywebview](https://pywebview.flowrl.com/)
in Windows' built-in WebView2, not a bundled browser.

---

## License

MIT — see [LICENSE](LICENSE).
