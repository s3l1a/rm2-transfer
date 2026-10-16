"""SFTP operations. No Qt dependencies, and no credentials written to disk."""
import base64
import hashlib
import json
import os
from pathlib import Path
import posixpath
import socket
import stat
import time
import uuid
import zipfile

import paramiko

LIBRARY = '/home/root/.local/share/remarkable/xochitl'


class TransferError(Exception):
    pass


class UnknownHost(TransferError):
    def __init__(self, fingerprint):
        self.fingerprint = fingerprint
        super().__init__('Unrecognized tablet host key: ' + fingerprint)


def fingerprint(key):
    digest = hashlib.sha256(key.asbytes()).digest()
    return 'SHA256:' + base64.b64encode(digest).decode().rstrip('=')


class PinnedHost(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        actual = fingerprint(key)
        if not self.expected:
            raise UnknownHost(actual)
        if actual != self.expected:
            raise TransferError('Tablet host key changed. Verify it before replacing the saved profile.')


def friendly_error(error):
    if isinstance(error, paramiko.AuthenticationException):
        return 'SSH login failed. Check the username and tablet root password.'
    if isinstance(error, (socket.timeout, TimeoutError, paramiko.ssh_exception.NoValidConnectionsError, socket.gaierror)):
        return 'Tablet unreachable. Check the IP, USB network connection or Wi-Fi, and that SSH is enabled.'
    if isinstance(error, TransferError):
        return str(error)
    if isinstance(error, paramiko.SSHException):
        return 'SSH connection failed or was interrupted. Reconnect and try again.'
    if isinstance(error, PermissionError):
        return 'Permission denied. Check access to the selected file or directory.'
    if isinstance(error, OSError):
        return 'File or SFTP operation failed. Check the path, free space, and connection.'
    return 'Operation failed (' + type(error).__name__ + ').'


class Tablet:
    def __init__(self):
        self.client = None
        self.sftp = None

    def close(self):
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()
        self.client = self.sftp = None

    def connect(self, host, username, password, pinned=''):
        self.close()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedHost(pinned))
        try:
            client.connect(host, username=username, password=password,
                           look_for_keys=False, allow_agent=False,
                           timeout=8, auth_timeout=8, banner_timeout=8)
            sftp = client.open_sftp()
            sftp.get_channel().settimeout(30)
        except Exception:
            client.close()
            raise
        self.client, self.sftp = client, sftp

    def browse(self, path):
        return sorted(self.sftp.listdir_attr(path),
                      key=lambda entry: (not stat.S_ISDIR(entry.st_mode), entry.filename.casefold()))

    def upload_document(self, source, progress):
        """Publish metadata last, so a partial upload isn't a library entry."""
        source = Path(source)
        extension = source.suffix.lower()
        if extension not in ('.pdf', '.epub'):
            raise TransferError('Library import accepts PDF and EPUB files only.')
        identifier = str(uuid.uuid4())
        base = posixpath.join(LIBRARY, identifier)
        content = {'fileType': extension[1:], 'pageCount': 0,
                   'dummyDocument': False, 'extraMetadata': {},
                   'lastOpenedPage': 0, 'lineHeight': -1,
                   'margins': 100, 'orientation': 'portrait', 'textScale': 1,
                   'transform': {f'm{i}{j}': int(i == j) for i in range(1, 4) for j in range(1, 4)}}
        metadata = {'deleted': False, 'lastModified': str(int(time.time() * 1000)),
                    'metadatamodified': True, 'modified': True, 'parent': '',
                    'pinned': False, 'synced': False, 'type': 'DocumentType',
                    'version': 1, 'visibleName': source.stem}
        created = []
        try:
            created.append(base + extension)
            self.sftp.put(str(source), base + extension, callback=progress)
            for suffix, data in (('.content', content), ('.metadata', metadata)):
                temporary = base + suffix + '.part'
                created.append(temporary)
                with self.sftp.file(temporary, 'w') as stream:
                    stream.write(json.dumps(data).encode())
                self.sftp.rename(temporary, base + suffix)
                created.append(base + suffix)
        except Exception:
            for path in reversed(created):
                try:
                    self.sftp.remove(path)
                except OSError:
                    pass
            raise
        return identifier

    def upload_file(self, source, directory, progress):
        target = posixpath.join(directory, Path(source).name)
        temporary = target + '.' + uuid.uuid4().hex + '.part'
        try:
            self.sftp.put(str(source), temporary, callback=progress)
            # Standard SFTP rename fails if the destination already exists.
            self.sftp.rename(temporary, target)
        finally:
            try:
                self.sftp.remove(temporary)
            except OSError:
                pass

    def download_file(self, source, destination, progress):
        destination = Path(destination)
        temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.part')
        try:
            self.sftp.get(source, str(temporary), callback=progress)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def download_notebook(self, selected, destination, progress):
        """Archive all UUID siblings, including page data and metadata."""
        identifier = posixpath.basename(selected).split('.')[0]
        try:
            uuid.UUID(identifier)
        except ValueError:
            raise TransferError('Select a UUID-named notebook file or directory in the library.') from None
        files = []

        def walk(path, name):
            info = self.sftp.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                raise TransferError('Archive contains a symbolic link; refusing to follow it.')
            if stat.S_ISDIR(info.st_mode):
                for child in self.sftp.listdir(path):
                    if child in ('.', '..') or '/' in child or '\\' in child:
                        raise TransferError('Invalid remote filename.')
                    walk(posixpath.join(path, child), posixpath.join(name, child))
            elif stat.S_ISREG(info.st_mode):
                files.append((path, name, info.st_size))

        for name in self.sftp.listdir(LIBRARY):
            if name == identifier or name.startswith(identifier + '.'):
                walk(posixpath.join(LIBRARY, name), name)
        if not files:
            raise TransferError('No notebook files found in the library.')
        total = sum(size for _, _, size in files)
        done = 0
        destination = Path(destination)
        temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.part')
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
                for remote, name, _ in files:
                    with self.sftp.file(remote, 'rb') as src, archive.open(name, 'w', force_zip64=True) as dst:
                        while chunk := src.read(256 * 1024):
                            dst.write(chunk)
                            done += len(chunk)
                            progress(done, total)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def restart_ui(self):
        _, stdout, _ = self.client.exec_command('systemctl restart xochitl', timeout=30)
        if stdout.channel.recv_exit_status() != 0:
            raise TransferError('Restart failed. Check that the SSH account can restart xochitl.')
