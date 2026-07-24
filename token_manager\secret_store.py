from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


DPAPI_MAGIC = b"GROK_TOOL_DPAPI_V1\0"
_ENTROPY = b"GrokTool:master-key:v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _input_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _windows_api():
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is unavailable on this platform")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def protect_for_current_user(data: bytes) -> bytes:
    crypt32, kernel32 = _windows_api()
    input_blob, input_buffer = _input_blob(data)
    entropy_blob, entropy_buffer = _input_blob(_ENTROPY)
    output_blob = _DataBlob()
    try:
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "Grok Tool Master Key",
            ctypes.byref(entropy_blob),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return DPAPI_MAGIC + encrypted
    finally:
        _ = input_buffer, entropy_buffer
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)


def unprotect_for_current_user(payload: bytes) -> bytes:
    if not payload.startswith(DPAPI_MAGIC):
        raise RuntimeError("unsupported encrypted master-key format")
    crypt32, kernel32 = _windows_api()
    encrypted = payload[len(DPAPI_MAGIC) :]
    input_blob, input_buffer = _input_blob(encrypted)
    entropy_blob, entropy_buffer = _input_blob(_ENTROPY)
    output_blob = _DataBlob()
    description = wintypes.LPWSTR()
    try:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            ctypes.byref(description),
            ctypes.byref(entropy_blob),
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        )
        if not ok:
            raise RuntimeError(
                "cannot decrypt master key for this Windows user or machine"
            ) from ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        _ = input_buffer, entropy_buffer
        if description:
            kernel32.LocalFree(description)
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)
