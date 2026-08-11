![Screen Timer for Windows](assets/banner.png)

# Screen Timer for Windows

My phone tells me I average 6 hours a day on it. Rough, but at least I know.

Then it hit me: nothing was telling me what I do on my laptop. Which is where
I actually spend most of my day.

So I went looking. Everything I found wanted an account, or a subscription, or
wanted to send my activity off to someone's server. Nah.

Made my own instead.

It sits in your taskbar, tracks which apps you actually use, and nudges you to
take breaks. Free, open source, no account, no telemetry. Nothing ever leaves
your machine.

<table>
<tr>
<td><img src="assets/screenshot-dark.png" width="360" alt="Dark mode"></td>
<td><img src="assets/screenshot-light.png" width="360" alt="Light mode"></td>
</tr>
</table>

## Install

1. Download **`ScreenTimer.exe`** from [Releases](../../releases/latest)
2. Double-click it

No installer, no Python, nothing to configure. It sets itself up on first
launch and starts tracking right away.

> **Blue "Windows protected your PC" popup?** Click **More info** →
> **Run anyway**. It just means the app isn't signed with a paid certificate.
> Every small indie app hits this. All the source is right here if you'd
> rather build it yourself.

## Using it

| Action | How |
|---|---|
| Open it | Click the orange icon in your taskbar |
| Close the window | Hit X, it keeps tracking in the background |
| Quit properly | Right-click the tray icon → Quit |
| Switch theme | Button in the top right |

**Settings:** how often you get break reminders, your daily limit, and
per-app limits (click the icon next to any app).

**Stop it auto-starting:** Task Manager → Startup apps → disable it. It stays
off.

**Uninstall:** quit it, disable the startup entry, delete `ScreenTimer.exe`,
then delete `%APPDATA%\ScreenTimer`. Nothing is left behind.

## How it counts

Fair question if you're used to a laptop: you've got fifteen things open at
once, so what does "1 hour" even mean?

Every second, it checks which window you're actually in, and gives that one
second to that one app. That's the whole rule.

So having a pile of windows open doesn't inflate anything. Only one window can
be focused at a time, so a second only ever goes to one app, and your daily
total is always exactly the sum of your apps. Never more.

It also stops counting after 60 seconds of no keyboard or mouse, so going for
lunch doesn't quietly rack up screen time.

Worth knowing this measures attention, not what's running. Music playing in the
background while you work counts as whatever you're working in, and a download
chugging away for three hours counts as nothing at all. Your phone does it the
same way.

## Your data

Everything lives in `%APPDATA%\ScreenTimer\` and never leaves your machine.
Delete that folder to wipe your history.

Windows system stuff like the lock screen, Start menu and search is ignored, so
your list is only apps you actually chose to open. It doesn't count itself
either.

## Building it yourself

Needs Python 3.11+ on Windows.

```bash
git clone https://github.com/arihantslittlecave/screen-timer-for-windows.git
cd screen-timer-for-windows
pip install -r requirements.txt
pyinstaller screen-timer.spec
```

Your `.exe` lands in `dist/`. Or run it straight from source with
`python main.py`.

<details>
<summary>How it's put together</summary>

| File | What it does |
|---|---|
| `main.py` | Tray icon, window, tracking loop, notifications |
| `api.py` | Bridge between the UI and Python |
| `storage.py` | Reads and writes the JSON, handles day rollover |
| `active_window.py` | Which app has focus |
| `idle.py` | Time since you last touched the keyboard or mouse |
| `icons.py` | Pulls real icons out of `.exe` files |
| `icon_art.py` | Draws the app's tray badge |
| `autostart.py` | The startup registry entry |
| `first_run.py` | One-time setup on first launch |
| `paths.py` | Bundled files vs. your user data |
| `ui/` | The interface, plain HTML, CSS and JavaScript |

The UI is a local HTML page rendered through
[pywebview](https://pywebview.flowrl.com/) in Windows' built-in WebView2. No
bundled browser.

</details>

## License

MIT, do whatever you want with it. See [LICENSE](LICENSE).

## Something not working?

This has been built and tested on one machine, mine. Windows being Windows,
there's a decent chance something behaves differently on yours.

If it doesn't work, or the tray icon won't show up, or it just does something
weird, **[open an issue](../../issues/new)** and tell me what happened. I'll
fix it. Genuinely, I want to know.

It helps a lot if you paste in `%APPDATA%\ScreenTimer\screen-timer.log`, which
is where the app writes what it was doing when things went sideways.

---

Built by [arihant](https://github.com/arihantslittlecave).
