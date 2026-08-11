# Screen Timer for Windows

Ever wonder where your whole day went? Yeah, same. This little app sits quietly
in your taskbar, tracks which apps you're actually using, and nudges you to
take a break every now and then. Basically Android's Digital Wellbeing, but
for your PC.

It's free, it's open source, and it doesn't phone home. Everything stays on
your machine. No account, no signup, no ads, none of that.

---

## Getting started

1. Grab **`ScreenTimer.exe`** from [Releases](../../releases/latest).
2. Double-click it.

That's genuinely it. No installer, no Python, nothing else to set up.

The first time it opens, it quietly turns on start-on-login and pins itself
to your taskbar so you never have to think about it again. Just download and
go.

### "Windows protected your PC" popup

You'll probably get a blue warning screen the first time you run it. Don't
worry, nothing's wrong. It just means the app isn't signed with a paid
certificate (those cost real money every year, and this is a free hobby
project). Every small indie app hits this.

Click **More info**, then **Run anyway**, and you're set.

If you want to be extra careful, the full source is right here in this repo.
Read it, build it yourself, whatever makes you comfortable. See
[Building](#building) below.

---

## How to use it

| | |
|---|---|
| **Open the app** | Click the little orange icon in your taskbar |
| **Close the window** | Hit X, it keeps tracking quietly in the background |
| **Actually quit it** | Right-click the tray icon, then Quit |
| **Switch theme** | Button in the top right of the window |

The window shows your total for today, a breakdown of which apps ate your
time (with their real icons), and 7 or 30 day history. Click any day on the
chart to see what that day looked like.

### Settings

- **Break every** sets how often it reminds you to step away
- **Daily limit** is your target for the day. The dial fills up as you get
  closer, and the line under your total shows the percentage
- **Per-app limits** are set by clicking the little icon next to any app

### Turning off auto-start

Changed your mind? Open Task Manager, go to **Startup apps**, find Screen
Timer, and disable it. It stays off, it won't sneak back on.

### Uninstalling

No installer means no uninstaller either, just a couple of manual steps:

1. Right-click the tray icon and hit **Quit**
2. Task Manager, **Startup apps**, disable Screen Timer
3. Delete `ScreenTimer.exe`
4. Delete the `%APPDATA%\ScreenTimer` folder (paste that into File Explorer's
   address bar, that's where your history lives)

Nothing gets left behind, nothing hides in Program Files.

---

## What it tracks, and where it lives

Everything is stored locally in `%APPDATA%\ScreenTimer\`:

| File | What's in it |
|---|---|
| `data.json` | Your daily totals and per-app time |
| `settings.json` | Break interval, daily limit, per-app limits |
| `app_paths.json` | Where each app lives, so it can grab its icon |
| `screen-timer.log` | Startup logs and anything worth reporting if it breaks |

Want to wipe your history? Just delete that folder.

It only counts the app you're actively using, and stops counting after 60
seconds of no keyboard or mouse activity, so stepping away doesn't rack up
fake time. Windows system stuff like the lock screen, Start menu, and search
gets ignored too, so your list is just the apps you actually chose to open.

---

## Building it yourself

You'll need Python 3.11 or newer on Windows.

```bash
git clone https://github.com/YOUR-USERNAME/screen-timer-for-windows.git
cd screen-timer-for-windows
pip install -r requirements.txt
pyinstaller screen-timer.spec
```

Your `.exe` shows up in `dist/`. Or just run it straight from source without
packaging anything:

```bash
python main.py
```

---

## How it's put together

| File | What it does |
|---|---|
| `main.py` | Tray icon, window, tracking loop, notifications |
| `api.py` | The bridge between the UI and Python |
| `storage.py` | Reads and writes the JSON, handles day rollover |
| `active_window.py` | Figures out which app has focus |
| `idle.py` | Tracks how long since you last touched the keyboard or mouse |
| `icons.py` | Pulls real icons out of `.exe` files |
| `icon_art.py` | Draws the app's own tray badge |
| `autostart.py` | Handles the startup registry entry |
| `first_run.py` | One-time setup that runs on first launch |
| `paths.py` | Sorts out bundled files vs. your actual user data |
| `ui/` | The interface itself, plain HTML, CSS, and JavaScript |

The UI is just a local HTML page, rendered through
[pywebview](https://pywebview.flowrl.com/) using Windows' built-in WebView2.
No bundled browser, no extra bloat.

---

## License

MIT. Do whatever you want with it, see [LICENSE](LICENSE) for the fine print.
