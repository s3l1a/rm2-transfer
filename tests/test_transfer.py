import json
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import paramiko
import pytest

from transfer import Tablet, LIBRARY, PinnedHost, UnknownHost, TransferError, fingerprint, friendly_error


class LocalSFTP:
    """Model remote storage without needing tablet credentials."""
    def __init__(self, root):
        self.root = root
        self.fail_metadata = False

    def path(self, path):
        return self.root / path.lstrip('/')

    def put(self, source, target, callback):
        data = Path(source).read_bytes()
        self.path(target).write_bytes(data)
        callback(len(data), len(data))

    def file(self, path, mode):
        return self.path(path).open(mode if 'b' in mode else mode + 'b')

    def rename(self, source, target):
        if self.fail_metadata and target.endswith('.metadata'):
            raise OSError('simulated publish failure')
        if self.path(target).exists():
            raise FileExistsError()
        self.path(source).rename(self.path(target))

    def remove(self, path):
        self.path(path).unlink()

    def listdir(self, path):
        return [p.name for p in self.path(path).iterdir()]

    def lstat(self, path):
        return self.path(path).lstat()


@pytest.fixture
def tablet(tmp_path):
    tablet = Tablet()
    tablet.sftp = LocalSFTP(tmp_path / 'remote')
    tablet.sftp.path(LIBRARY).mkdir(parents=True)
    return tablet


def test_import_and_archive_roundtrip(tablet, tmp_path):
    source = tmp_path / 'sample.pdf'
    source.write_bytes(b'%PDF-1.4\nfixture\n')
    progress = Mock()
    identifier = tablet.upload_document(source, progress)
    metadata = json.loads(tablet.sftp.path(f'{LIBRARY}/{identifier}.metadata').read_text())
    assert metadata['visibleName'] == 'sample'
    assert metadata['type'] == 'DocumentType'
    pages = tablet.sftp.path(f'{LIBRARY}/{identifier}')
    pages.mkdir()
    (pages / 'page.rm').write_bytes(b'notebook fixture')
    destination = tmp_path / 'backup.zip'
    tablet.download_notebook(f'{LIBRARY}/{identifier}.metadata', destination, progress)
    with zipfile.ZipFile(destination) as archive:
        assert archive.read(identifier + '.pdf') == source.read_bytes()
        assert archive.read(identifier + '/page.rm') == b'notebook fixture'
        assert len(archive.namelist()) == 4


def test_failed_publish_cleans_partial_document(tablet, tmp_path):
    source = tmp_path / 'sample.epub'
    source.write_bytes(b'fixture')
    tablet.sftp.fail_metadata = True
    with pytest.raises(OSError):
        tablet.upload_document(source, Mock())
    assert tablet.sftp.listdir(LIBRARY) == []


def test_raw_upload_preserves_existing_file(tablet, tmp_path):
    source = tmp_path / 'existing.pdf'
    source.write_bytes(b'new')
    target = tablet.sftp.path(LIBRARY + '/existing.pdf')
    target.write_bytes(b'old')
    with pytest.raises(FileExistsError):
        tablet.upload_file(source, LIBRARY, Mock())
    assert target.read_bytes() == b'old'
    assert tablet.sftp.listdir(LIBRARY) == ['existing.pdf']


def test_failed_download_preserves_local_file(tmp_path):
    tablet = Tablet()
    tablet.sftp = Mock()
    def fail(source, destination, callback):
        Path(destination).write_bytes(b'partial')
        raise OSError('connection lost')
    tablet.sftp.get.side_effect = fail
    target = tmp_path / 'saved.pdf'
    target.write_bytes(b'original')
    with pytest.raises(OSError):
        tablet.download_file('/document.pdf', target, Mock())
    assert target.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [target]


def test_host_key_requires_consent_and_rejects_change():
    key = SimpleNamespace(asbytes=lambda: b'test public key')
    with pytest.raises(UnknownHost):
        PinnedHost('').missing_host_key(None, 'tablet', key)
    PinnedHost(fingerprint(key)).missing_host_key(None, 'tablet', key)
    with pytest.raises(TransferError, match='changed'):
        PinnedHost('different').missing_host_key(None, 'tablet', key)


@pytest.mark.parametrize('error, text', [
    (paramiko.AuthenticationException(), 'login failed'),
    (socket.timeout(), 'unreachable'),
    (OSError(), 'SFTP operation failed'),
    (paramiko.SSHException(), 'SSH connection failed'),
])
def test_errors_are_actionable(error, text):
    assert text in friendly_error(error)


def test_failed_sftp_setup_closes_connection(monkeypatch):
    client = Mock()
    client.open_sftp.side_effect = paramiko.SSHException()
    monkeypatch.setattr(paramiko, 'SSHClient', lambda: client)
    tablet = Tablet()
    with pytest.raises(paramiko.SSHException):
        tablet.connect('tablet.invalid', 'root', '')
    client.close.assert_called_once()
    assert tablet.sftp is None
