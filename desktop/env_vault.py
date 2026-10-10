"""
A recoverable copy of production.env, so deleting it is no longer fatal.

Why this exists
---------------
production.env holds the password of the `tenderradar_app` MySQL account. That
password cannot be re-derived: it was generated once and only MySQL knows it. So
if the file is deleted, the launcher cannot simply write a new one — it has to
ask for the MySQL *root* password and mint fresh credentials.

On a customer machine that is often a dead end. A fresh install generates the
root password itself, writes it to config\\mysql_root_password.txt, and tells the
user to move it into a password manager and delete the file. A user who followed
that advice and then loses production.env has no way back in.

This module keeps a copy somewhere the application folder cannot reach, so the
launcher can restore the file silently and start normally.

Where and how
-------------
    %LOCALAPPDATA%\\AJi Group\\TenderRadar\\production.env.dpapi

Encrypted with Windows DPAPI under the current user, with an application-specific
entropy value. That is strictly better than a second plaintext copy: the bytes on
disk are useless to any other user account, and to any other program that does not
know the entropy. It is *not* protection against that same user reading it — it
cannot be, because the launcher has to decrypt it unattended.

The copy lives outside the install folder on purpose. A copy inside it would be
removed by the very actions this is meant to survive: deleting production.env,
uninstalling, or wiping the folder.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

APP_DIR_NAME = 'TenderRadar'
PUBLISHER = 'AJi Group'
VAULT_NAME = 'production.env.dpapi'

# Application-specific entropy. Not a secret — it scopes the ciphertext to this
# application so another program running as the same user cannot decrypt it by
# calling CryptUnprotectData with no entropy.
_ENTROPY = b'TenderRadar/production.env/v1'


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD),
                ('pbData', ctypes.POINTER(ctypes.c_char))]


_crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

_crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]


def _to_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf                      # buf returned so it outlives the call


def _from_blob(blob: _Blob) -> bytes:
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        _kernel32.LocalFree(blob.pbData)


def protect(data: bytes) -> bytes:
    src, _keep = _to_blob(data)
    ent, _keep2 = _to_blob(_ENTROPY)
    out = _Blob()
    ok = _crypt32.CryptProtectData(ctypes.byref(src), 'TenderRadar',
                                   ctypes.byref(ent), None, None, 0,
                                   ctypes.byref(out))
    if not ok:
        raise OSError(ctypes.get_last_error(), 'CryptProtectData failed')
    return _from_blob(out)


def unprotect(data: bytes) -> bytes:
    src, _keep = _to_blob(data)
    ent, _keep2 = _to_blob(_ENTROPY)
    out = _Blob()
    ok = _crypt32.CryptUnprotectData(ctypes.byref(src), None,
                                     ctypes.byref(ent), None, None, 0,
                                     ctypes.byref(out))
    if not ok:
        raise OSError(ctypes.get_last_error(), 'CryptUnprotectData failed')
    return _from_blob(out)


def vault_path() -> Path:
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    return Path(base) / PUBLISHER / APP_DIR_NAME / VAULT_NAME


def has_backup() -> bool:
    try:
        return vault_path().is_file() and vault_path().stat().st_size > 0
    except OSError:
        return False


def _looks_usable(text: str) -> bool:
    """A file worth keeping: real credentials, not the first-run placeholder."""
    if 'DB_PASSWORD' not in text:
        return False
    for line in text.splitlines():
        if line.strip().startswith('DB_PASSWORD'):
            _, _, value = line.partition('=')
            value = value.strip()
            return bool(value) and '__GENERATED_ON_FIRST_RUN__' not in value
    return False


def backup(env_path: Path, log=None) -> bool:
    """
    Store an encrypted copy of `env_path`. Returns True if it wrote one.

    Rewrites only when the contents changed, so a password rotation is captured
    but an ordinary start costs one decrypt and no disk write.
    """
    try:
        if not env_path.is_file():
            return False
        text = env_path.read_text(encoding='utf-8')
        if not _looks_usable(text):
            return False
        if has_backup():
            try:
                if unprotect(vault_path().read_bytes()).decode('utf-8') == text:
                    return False              # already current
            except OSError:
                pass                          # unreadable — overwrite it
        target = vault_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix('.tmp')
        tmp.write_bytes(protect(text.encode('utf-8')))
        tmp.replace(target)
        if log:
            log(f'credentials backed up to {target}')
        return True
    except Exception as exc:                  # never block startup over a backup
        if log:
            log(f'credential backup skipped ({type(exc).__name__}: {exc})')
        return False


def restore(env_path: Path, log=None) -> bool:
    """
    Recreate `env_path` from the encrypted copy. Returns True if it did.

    Refuses to overwrite an existing file — this only ever fills a gap.
    """
    try:
        if env_path.is_file():
            return False
        if not has_backup():
            return False
        text = unprotect(vault_path().read_bytes()).decode('utf-8')
        if not _looks_usable(text):
            return False
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text(text, encoding='utf-8')
        if log:
            log('production.env was missing — restored from the encrypted backup')
        return True
    except Exception as exc:
        if log:
            log(f'credential restore failed ({type(exc).__name__}: {exc})')
        return False


def forget(log=None) -> bool:
    """Delete the stored copy. Used by the uninstaller."""
    try:
        p = vault_path()
        if p.is_file():
            p.unlink()
            if log:
                log('stored credentials removed')
            return True
    except OSError:
        pass
    return False
