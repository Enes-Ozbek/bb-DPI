# bb-DPI
 <img width="271" height="398" alt="Screenshot" src="https://github.com/user-attachments/assets/6ce6c05b-e7a8-42cf-956f-f001e2141639" />

A modern GUI for GoodbyeDPI with a minimalist and easy-to-use UI.

## Features
- **Modern UI**: Borderless window with custom shadows and sleek animations.
- **System Tray**: Runs in the background with a system tray icon for easy access.
- **One-Click Toggle**: Start and stop the background process easily.
- **Stand-Alone**: Can be compiled to a single executable.

## Prerequisites
- Windows 10 or 11
- Administrator rights (GoodbyeDPI loads a network driver). The built `.exe` asks for them on launch; `python app.py` shows a UAC prompt and restarts itself elevated.
- The GoodbyeDPI files (see [Setting up GoodbyeDPI](#setting-up-goodbyedpi))
- Python 3.10+ (if running from source)

## Installation & Running from Source

1. Clone the repository:
   ```bash
   git clone https://github.com/Enes-Ozbek/bb-DPI.git
   cd bb-DPI
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python app.py
   ```

## Building the Executable

To build a standalone executable that doesn't require Python to be installed:

1. Install PyInstaller:
   ```bash
   pip install pyinstaller
   ```
2. Build the app using the provided spec file:
   ```bash
   pyinstaller app.spec
   ```
3. The compiled `.exe` will be located in the `dist` folder.

## Setting up GoodbyeDPI
bb-DPI is a GUI wrapper and doesn't include GoodbyeDPI itself. Download a [GoodbyeDPI release](https://github.com/ValdikSS/GoodbyeDPI/releases) and put these three files from its `x86_64` folder next to `bb-DPI.exe` (or `app.py`):

- `goodbyedpi.exe`
- `WinDivert.dll`
- `WinDivert64.sys`

They can also stay in a subfolder: bb-DPI looks in `x86_64\`, `goodbyedpi\`, `goodbyedpi\x86_64\`, and any `goodbyedpi*\` / `goodbyedpi*\x86_64\` folder (so an extracted release folder works as-is).

## Configuration
On first run bb-DPI creates `config.ini` next to itself:

```ini
[Settings]
Arguments = -5
ModeText = Mode 5  ·  Alternative 2
```

- `Arguments` is passed to `goodbyedpi.exe` exactly as written, like in a `.cmd` file, so quoted paths work (e.g. `-5 --blacklist "C:\My Lists\blacklist.txt"`). Relative paths are resolved from the folder `goodbyedpi.exe` is in.
- `ModeText` is the label shown at the bottom of the window.

Restart bb-DPI after editing it.

## Troubleshooting
If the button turns red with **Failed**, the message under it is GoodbyeDPI's own error (hover over it for the full text). Everything GoodbyeDPI prints is also written to the log at `%APPDATA%\bb-DPI\app.log`; right-click the tray icon → **Open log**.

GoodbyeDPI waits 20 seconds before exiting when its driver fails to load (for example, when antivirus blocks WinDivert), so that kind of error can appear a little while after the button turned green.

If bb-DPI closes or crashes, Windows also stops the `goodbyedpi.exe` it started, so the bypass never keeps running without the app.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
