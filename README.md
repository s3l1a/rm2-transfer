# rm2-transfer

A two-pane file browser for a reMarkable 2 and a Linux or Windows computer.
I want to move a PDF over a cable without involving a cloud account. This uses
SSH/SFTP for browsing, imports, and backups.

## Connect

On the tablet, open **Settings → Help → Copyrights and licenses → GPLv3
compliance** to find the SSH/root access information and password. Enable SSH
there if your firmware presents a switch. Use username `root`.

Connect a data-capable USB cable and use `10.11.99.1`. The tablet exposes a
USB Ethernet/RNDIS interface; the computer must recognize that network adapter.
On Windows you may need to enable/install the appropriate RNDIS driver.
For Wi-Fi, put both devices on the same network and enter the tablet's LAN IP
in the connection dialog. Guest-network isolation can prevent connections.

The first connection asks you to verify a SHA256 host-key fingerprint. Compare
it with a key obtained through a trusted connection to the tablet. Changed keys
are rejected. If you reinstall the tablet, verify the new key and use a new
profile name rather than blindly trusting the change.

Profiles save host, username, and host-key fingerprint in `profiles.json` under
Qt's per-user application configuration directory. Passwords are entered each
session and are never saved. Profiles can contain private network addresses;
keep them out of the repository.

## Moving files

- Double-click directories or type an absolute path in either address bar.
- Select a local PDF/EPUB and click **Import PDF / EPUB**. This creates a new
  UUID document with `.content` and `.metadata` files in the xochitl library.
  Repeated imports create separate documents.
- **Upload raw file** copies into the current tablet directory, refusing to
  overwrite an existing file. Raw PDF uploads alone do not create library entries.
- **Download file** saves the selected remote file. The save dialog asks before
  replacing a local file; failed downloads leave the existing file intact.
- **Notebook ZIP** packages all files and page directories sharing the selected
  notebook UUID. Select an entry directly in the library directory. These are
  native backups, not rendered PDFs; there is no archive-restore feature yet.
- **Restart tablet UI** runs `systemctl restart xochitl`, after confirmation.
  Close the current document on the tablet first. Restart is never automatic.

Transfers run in a worker thread with byte progress. File operations are serial;
wait for the current operation to finish before closing the app. Network
timeouts prevent most indefinite waits, but there is no cancel/resume yet.

## Run from source

Use Python 3.10 or newer supported by the pinned dependencies.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell instead.

## Build

Build on the target operating system. PyInstaller does not cross-compile a
Windows executable from Linux.

Linux:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
pyinstaller --clean --noconfirm linux.spec
dist/rm2-transfer/rm2-transfer
```

Windows, from PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
pyinstaller --clean --noconfirm windows.spec
dist\rm2-transfer\rm2-transfer.exe
```

Distribute the entire `dist/rm2-transfer` directory, not just the executable.
Linux builds depend on the host glibc baseline; build on the oldest Linux
distribution you intend to support. The build here used Fedora 44, glibc 2.43,
Python 3.14.3, PySide6 6.12.0, and PyInstaller 6.22.3.

## What I actually checked

The Linux commands above were run using a temporary virtual environment.
All 14 tests passed. Tests cover import metadata and archive contents,
cleanup after failed imports, overwrite protection, interrupted downloads,
host-key rejection, login/network/SFTP error messages, profile password
exclusion, malformed profiles, the connection dialog, stale directory selection,
and UI recovery after a worker failure.
SFTP storage is simulated in these tests; it is not a real server integration test.

Both source and packaged Linux builds passed this headless launch check:

```sh
QT_QPA_PLATFORM=offscreen python app.py --smoke-test
QT_QPA_PLATFORM=offscreen dist/rm2-transfer/rm2-transfer --smoke-test
```

That opens the main window and connection dialog, then exits. It does not prove
desktop rendering works on every Wayland/X11 setup. Windows builds have not been
run. No tablet responded to the USB SSH reachability check; Wi-Fi hardware testing
was not performed.

## Limits that matter

**Untested on device.** In particular, firmware-specific PDF/EPUB indexing,
cloud sync behavior, host-key negotiation with the tablet's SSH server, notebook
restoration, and the restart command still need hardware verification.
The importer writes the traditional xochitl document format. New firmware may
require different metadata; a successful SFTP copy alone is not proof that the
document appears in the library.

Backups taken while a notebook is being edited or synced are not atomic snapshots.
Close it and wait for sync to settle first. ZIP files contain native notebook
data and may contain personal notes. Symlinks in notebook archives are rejected.
There is no PDF rendering, folder import, recursive raw upload, or cloud API.
