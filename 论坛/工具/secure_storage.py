"""Windows account-bound storage for the forum deployment recipient and initial credentials."""
import ctypes
from ctypes import wintypes
import csv
import io
import os
from pathlib import Path
import subprocess


def protect(raw, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, target = Blob(len(raw), buffer), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(source), 'HushLeaf forum deployment', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ValueError('无法解锁论坛部署资料，请使用部署时的电脑和 Windows 账号。')
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


def private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    account = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], creationflags=flags).decode('utf-8', errors='replace')
    sid = next(csv.reader(io.StringIO(account.strip())))[-1]
    if not sid.startswith('S-1-'):
        raise ValueError('无法识别 Windows 账号。')
    subprocess.run(['icacls', str(path), '/inheritance:r', '/remove:g', '*S-1-3-4', '/grant:r', '*' + sid + ':(OI)(CI)F'],
                   capture_output=True, check=True, creationflags=flags)
    return path


def private_file(path):
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    account = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], creationflags=flags).decode('utf-8', errors='replace')
    sid = next(csv.reader(io.StringIO(account.strip())))[-1]
    if not sid.startswith('S-1-'):
        raise ValueError('无法识别 Windows 账号。')
    subprocess.run(['icacls', str(path), '/inheritance:r', '/remove:g', '*S-1-3-4', '/grant:r', '*' + sid + ':F'],
                   capture_output=True, check=True, creationflags=flags)

