# rm2-transfer

A two-pane file browser for a reMarkable 2 and a Linux or Windows computer.
I want to move a PDF over a cable without involving a cloud account. This uses
SSH/SFTP for browsing, imports, and backups.

## Windows download

Grab the latest `rm2-transfer-<version>-windows.zip` from the
[Releases](../../releases) page, unzip it anywhere, and run
`rm2-transfer.exe` from the unzipped folder. Keep the whole folder together;
the exe needs the DLLs next to it. Windows SmartScreen may warn about an
unsigned exe — that's expected, there's no code signing here.

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
distribution you intend to support. The Windows zip on the Releases page is
built by the GitHub Actions workflow in `.github/workflows/`, so you don't need
a Windows box to get one.

A heads up on what's actually been tried: the Linux build, tests, and headless
launch all run fine here. I don't have a tablet in front of me right now, so
the device side — library indexing on current firmware, sync behavior, notebook
restore — is unverified. Back up before trusting an import, and close the
notebook before zipping it so you don't grab a half-written snapshot.
