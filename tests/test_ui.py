import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from PySide6.QtCore import QEventLoop, QTimer
import pytest

import app


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication([])


def test_connection_dialog_and_password_not_saved(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'profile_path', lambda: tmp_path / 'profiles.json')
    profiles = {'USB': {'host': '10.11.99.1', 'username': 'root', 'fingerprint': '', 'password': 'test-only'}}
    app.save_profiles(profiles)
    assert 'password' not in (tmp_path / 'profiles.json').read_text()
    dialog = app.ConnectionDialog(app.load_profiles())
    dialog.show()
    assert dialog.host.text() == '10.11.99.1'
    assert dialog.password.text() == ''
    dialog.validate()
    assert dialog.result() == QDialog.Accepted


def test_window_worker_error_recovers_controls(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'profile_path', lambda: tmp_path / 'profiles.json')
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    window = app.Window()
    window.show()
    loop = QEventLoop()
    def fail(_):
        raise TimeoutError()
    window.run_job(fail)
    window.job.finished.connect(loop.quit)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert window.job is None
    assert window.controls.isEnabled()
    assert 'unreachable' in warnings[0]
    window.close()


def test_invalid_profile_field_is_rejected(qt, tmp_path, monkeypatch):
    path = tmp_path / 'profiles.json'
    monkeypatch.setattr(app, 'profile_path', lambda: path)
    path.write_text('{"USB": {"host": 123}}')
    with pytest.raises(ValueError):
        app.load_profiles()


def test_failed_remote_navigation_clears_old_selection(qt, tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'profile_path', lambda: tmp_path / 'profiles.json')
    window = app.Window()
    window.remote_list.addItem('old document')
    window.remote_list.setCurrentRow(0)
    window.remote_path = '/missing-directory'
    monkeypatch.setattr(window, 'connected', lambda: True)
    monkeypatch.setattr(window, 'run_job', lambda *args: None)
    window.refresh_remote()
    assert window.remote_list.count() == 0
    assert window.remote_address.text() == '/missing-directory'
    window.close()
