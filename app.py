import json
from pathlib import Path
import posixpath
import stat
import sys

from PySide6.QtCore import QThread, Signal, QStandardPaths, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFormLayout,
    QLineEdit, QComboBox, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QListWidgetItem, QFileDialog, QMessageBox,
    QProgressBar, QLabel, QSplitter)
from PySide6.QtCore import Qt

from transfer import Tablet, LIBRARY, UnknownHost, friendly_error


def profile_path():
    return Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)) / 'profiles.json'


def load_profiles():
    try:
        data = json.loads(profile_path().read_text())
        if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
            raise ValueError()
        for profile in data.values():
            if any(not isinstance(profile.get(key, ''), str)
                   for key in ('host', 'username', 'fingerprint')):
                raise ValueError('Invalid profile field')
        return data
    except FileNotFoundError:
        return {}


def save_profiles(profiles):
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Explicit whitelist: passwords never enter the profile file.
    clean = {name: {k: value[k] for k in ('host', 'username', 'fingerprint')} for name, value in profiles.items()}
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(clean, indent=2))
    temporary.replace(path)


class ConnectionDialog(QDialog):
    def __init__(self, profiles, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Connect to reMarkable')
        self.profiles = profiles
        layout = QFormLayout(self)
        self.name = QComboBox()
        self.name.setEditable(True)
        self.name.addItems(['USB'] + [n for n in profiles if n != 'USB'])
        self.host = QLineEdit('10.11.99.1')
        self.username = QLineEdit('root')
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.key = QLineEdit()
        self.key.setReadOnly(True)
        for label, field in [('Profile', self.name), ('IP / hostname', self.host),
                             ('Username', self.username), ('Password (not saved)', self.password),
                             ('Saved host key', self.key)]:
            layout.addRow(label, field)
        layout.addRow(QLabel('USB: 10.11.99.1. Wi-Fi: enter the tablet’s LAN IP.\n'
                            'SSH password: Settings → Help → Copyrights and licenses\n'
                            '→ GPLv3 compliance.'))
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.name.currentTextChanged.connect(self.select_profile)
        self.select_profile(self.name.currentText())

    def select_profile(self, name):
        p = self.profiles.get(name, {})
        self.host.setText(p.get('host', '10.11.99.1'))
        self.username.setText(p.get('username', 'root'))
        self.key.setText(p.get('fingerprint', ''))
        self.password.clear()

    def validate(self):
        if not all((self.name.currentText().strip(), self.host.text().strip(), self.username.text().strip())):
            QMessageBox.warning(self, 'Missing fields', 'Enter a profile name, host, and username.')
            return
        self.accept()


class Job(QThread):
    result = Signal(object)
    failed = Signal(object)
    progress = Signal(int)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.result.emit(self.operation(lambda done, total: self.progress.emit(min(100, int(100 * done / total)) if total else 0)))
        except Exception as error:
            self.failed.emit(error)
        finally:
            self.operation = None


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('rm2-transfer')
        self.resize(1050, 650)
        self.tablet = Tablet()
        self.job = None
        self.local_path = Path.home()
        self.remote_path = LIBRARY
        self.profiles = {}
        self.profile_error = None
        try:
            self.profiles = load_profiles()
        except (OSError, ValueError, TypeError):
            self.profile_error = 'Cannot read connection profiles. Repair or move the profiles.json file in the app config directory.'
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        self.controls = QWidget()
        tools = QHBoxLayout(self.controls)
        actions = [('Connect', self.connect_dialog), ('Import PDF / EPUB', self.import_document),
                   ('Upload raw file', self.upload_raw), ('Download file', self.download),
                   ('Notebook ZIP', self.notebook_zip), ('Restart tablet UI', self.restart)]
        for name, action in actions:
            button = QPushButton(name)
            button.clicked.connect(action)
            tools.addWidget(button)
        layout.addWidget(self.controls)
        self.panes = QSplitter()
        self.local_address, self.local_list = self.pane('Local', self.local_up, self.refresh_local)
        self.remote_address, self.remote_list = self.pane('Tablet', self.remote_up, self.refresh_remote)
        layout.addWidget(self.panes)
        self.local_list.itemDoubleClicked.connect(self.open_local)
        self.remote_list.itemDoubleClicked.connect(self.open_remote)
        self.local_address.returnPressed.connect(self.local_go)
        self.remote_address.returnPressed.connect(self.remote_go)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.status = QLabel('Not connected')
        layout.addWidget(self.status)
        self.refresh_local()
        self.remote_address.setText(LIBRARY)

    def pane(self, title, up, refresh):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel(title))
        address = QLineEdit()
        layout.addWidget(address)
        row = QHBoxLayout()
        for name, callback in [('Up', up), ('Refresh', refresh)]:
            button = QPushButton(name)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        listing = QListWidget()
        layout.addWidget(listing)
        self.panes.addWidget(widget)
        return address, listing

    def run_job(self, operation, success=None, failure=None):
        if self.job is not None:
            return
        self.controls.setEnabled(False)
        self.panes.setEnabled(False)
        self.progress.setValue(0)
        self.status.setText('Working…')
        job = Job(operation, self)
        self.job = job
        outcome = []
        job.result.connect(lambda result: outcome.append((True, result)))
        job.failed.connect(lambda error: outcome.append((False, error)))
        job.progress.connect(self.progress.setValue)

        def finished():
            self.job = None
            self.controls.setEnabled(True)
            self.panes.setEnabled(True)
            job.deleteLater()
            ok, value = outcome[0]
            if ok:
                self.progress.setValue(100)
                self.status.setText('Done')
                if success:
                    success(value)
            elif failure:
                failure(value)
            else:
                self.error(value)
        job.finished.connect(finished)
        job.start()

    def error(self, error):
        message = friendly_error(error)
        self.status.setText(message)
        QMessageBox.warning(self, 'Operation failed', message)

    def connected(self):
        if self.tablet.sftp is None:
            QMessageBox.information(self, 'Connect first', 'Connect to the tablet first.')
            return False
        return True

    def connect_dialog(self):
        if self.profile_error:
            QMessageBox.warning(self, 'Profiles unavailable', self.profile_error)
            return
        dialog = ConnectionDialog(self.profiles, self)
        if dialog.exec() != QDialog.Accepted:
            return
        name = dialog.name.currentText().strip()
        p = {'host': dialog.host.text().strip(), 'username': dialog.username.text().strip(),
             'fingerprint': dialog.key.text()}
        password = dialog.password.text()
        dialog.password.clear()

        def attempt():
            self.run_job(lambda _: self.tablet.connect(p['host'], p['username'], password, p['fingerprint']), ready, failed)

        def ready(_):
            self.profiles[name] = p
            try:
                save_profiles(self.profiles)
            except OSError as error:
                self.error(error)
            self.remote_path = LIBRARY
            self.refresh_remote()

        def failed(error):
            if isinstance(error, UnknownHost):
                answer = QMessageBox.question(self, 'Trust tablet host key?',
                    'Verify this fingerprint with your tablet before trusting it:\n\n' + error.fingerprint,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer == QMessageBox.Yes:
                    p['fingerprint'] = error.fingerprint
                    attempt()
                    return
            self.error(error)
        attempt()

    def refresh_local(self):
        try:
            entries = sorted(self.local_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold()))
            self.local_list.clear()
            self.local_address.setText(str(self.local_path))
            for path in entries:
                item = QListWidgetItem(('[dir] ' if path.is_dir() else '') + path.name)
                item.setData(Qt.UserRole, str(path))
                self.local_list.addItem(item)
        except OSError as error:
            self.error(error)

    def refresh_remote(self):
        if not self.connected():
            return
        path = self.remote_path
        # A failed navigation must not leave selectable entries from another path.
        self.remote_list.clear()
        self.remote_address.setText(path)

        def show(entries):
            self.remote_list.clear()
            self.remote_address.setText(path)
            for entry in entries:
                directory = stat.S_ISDIR(entry.st_mode)
                item = QListWidgetItem(('[dir] ' if directory else '') + entry.filename)
                item.setData(Qt.UserRole, (posixpath.join(path, entry.filename), directory))
                self.remote_list.addItem(item)
        self.run_job(lambda _: self.tablet.browse(path), show)

    def local_go(self):
        self.local_path = Path(self.local_address.text()).expanduser().absolute()
        self.refresh_local()

    def remote_go(self):
        path = self.remote_address.text()
        if not path.startswith('/'):
            QMessageBox.warning(self, 'Invalid path', 'Enter an absolute tablet path starting with /.')
            return
        self.remote_path = posixpath.normpath(path)
        self.refresh_remote()

    def local_up(self):
        self.local_path = self.local_path.parent
        self.refresh_local()

    def remote_up(self):
        self.remote_path = posixpath.dirname(self.remote_path) or '/'
        self.refresh_remote()

    def open_local(self, item):
        path = Path(item.data(Qt.UserRole))
        if path.is_dir():
            self.local_path = path
            self.refresh_local()

    def open_remote(self, item):
        path, directory = item.data(Qt.UserRole)
        if directory:
            self.remote_path = path
            self.refresh_remote()

    def local_file(self):
        item = self.local_list.currentItem()
        if item and Path(item.data(Qt.UserRole)).is_file():
            return item.data(Qt.UserRole)
        QMessageBox.information(self, 'Select a file', 'Select a local file first.')

    def import_document(self):
        if self.connected() and (source := self.local_file()):
            self.run_job(lambda progress: self.tablet.upload_document(source, progress),
                         lambda _: self.refresh_remote())

    def upload_raw(self):
        if self.connected() and (source := self.local_file()):
            self.run_job(lambda progress: self.tablet.upload_file(source, self.remote_path, progress),
                         lambda _: self.refresh_remote())

    def download(self):
        item = self.remote_list.currentItem()
        if not self.connected() or not item:
            return
        path, directory = item.data(Qt.UserRole)
        if directory:
            QMessageBox.information(self, 'Select a file', 'Use Notebook ZIP for a notebook directory.')
            return
        destination, _ = QFileDialog.getSaveFileName(self, 'Save file', str(self.local_path / posixpath.basename(path)))
        if destination:
            self.run_job(lambda progress: self.tablet.download_file(path, destination, progress), lambda _: self.refresh_local())

    def notebook_zip(self):
        item = self.remote_list.currentItem()
        if not self.connected() or not item:
            return
        path, _ = item.data(Qt.UserRole)
        if posixpath.dirname(path) != LIBRARY:
            QMessageBox.information(self, 'Select a notebook', 'Select a notebook entry directly inside the xochitl library directory.')
            return
        name = posixpath.basename(path).split('.')[0] + '.zip'
        destination, _ = QFileDialog.getSaveFileName(self, 'Save notebook archive', str(self.local_path / name), 'ZIP (*.zip)')
        if destination:
            self.run_job(lambda progress: self.tablet.download_notebook(path, destination, progress), lambda _: self.refresh_local())

    def restart(self):
        if self.connected() and QMessageBox.question(self, 'Restart tablet UI?',
            'Close the current document on the tablet first. Restart xochitl now?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.run_job(lambda _: self.tablet.restart_ui())

    def closeEvent(self, event):
        if self.job is not None:
            QMessageBox.information(self, 'Operation running', 'Wait for the current operation to finish before closing.')
            event.ignore()
            return
        self.tablet.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('rm2-transfer')
    window = Window()
    window.show()
    if '--smoke-test' in sys.argv:
        dialog = ConnectionDialog({}, window)
        dialog.show()
        QTimer.singleShot(300, app.quit)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
