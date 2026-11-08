"""Windows: is a directory private to the current user? (review F-07)

The state directory holds the control token. In the user's profile the inherited ACL already limits it to the user,
SYSTEM and the administrators. Elsewhere (``--state-dir D:\\shared`` or ``TABRION_STUDIO_STATE_DIR``) Studio accepts
a directory only if it is owned by the user and every ACE that grants access names the user, SYSTEM, the
administrators, CREATOR OWNER or OWNER RIGHTS. A NULL DACL (everyone has full access) is never private.

The module is empty on other platforms; :func:`dacl_problem` is only called on Windows.
"""

from __future__ import annotations

from pathlib import Path
import sys

TRUSTED_SIDS = frozenset(
    {
        "S-1-5-18",  # SYSTEM
        "S-1-5-32-544",  # BUILTIN\\Administrators
        "S-1-3-0",  # CREATOR OWNER (inherit-only; becomes the creator of each new file)
        "S-1-3-4",  # OWNER RIGHTS (applies to the owner only, which is checked separately)
    }
)

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x1
    _DACL_SECURITY_INFORMATION = 0x4
    _ACL_SIZE_INFORMATION = 2
    _ACCESS_ALLOWED_ACE_TYPE = 0
    _ACCESS_DENIED_ACE_TYPE = 1
    _TOKEN_QUERY = 0x8
    _TOKEN_USER = 1

    class _AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _AceHeader(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _AccessAce(ctypes.Structure):
        # ACCESS_ALLOWED_ACE and ACCESS_DENIED_ACE share this layout; the SID starts at SidStart.
        _fields_ = [
            ("Header", _AceHeader),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    _advapi32.GetAclInformation.restype = wintypes.BOOL
    _advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    _advapi32.GetAce.restype = wintypes.BOOL
    _advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = []
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    def _winerror(what: str) -> OSError:
        code = ctypes.get_last_error()
        return OSError(code, f"{what} failed: {ctypes.FormatError(code)}")

    def _sid_string(sid: int | None) -> str:
        text = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise _winerror("ConvertSidToStringSidW")
        try:
            return str(text.value)
        finally:
            _kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))

    def current_user_sid() -> str:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(_kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
            raise _winerror("OpenProcessToken")
        try:
            size = wintypes.DWORD()
            _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
                raise _winerror("GetTokenInformation")
            # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first member is the PSID.
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            return _sid_string(sid)
        finally:
            _kernel32.CloseHandle(token)

    def dacl_problem(path: Path) -> str | None:
        """Why ``path`` is not private to the current user, or None if it is."""
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        error = _advapi32.GetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if error:
            raise OSError(error, f"GetNamedSecurityInfoW failed: {ctypes.FormatError(error)}")
        try:
            user = current_user_sid()
            trusted = TRUSTED_SIDS | {user}
            owner_sid = _sid_string(owner.value)
            if owner_sid not in (user, "S-1-5-32-544"):
                return f"it is owned by {owner_sid}, not by the current user"
            if not dacl.value:
                return "it has no access control list (everyone has full access)"
            size = _AclSizeInformation()
            if not _advapi32.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), _ACL_SIZE_INFORMATION):
                raise _winerror("GetAclInformation")
            for index in range(size.AceCount):
                ace_pointer = ctypes.c_void_p()
                if not _advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)):
                    raise _winerror("GetAce")
                header = ctypes.cast(ace_pointer, ctypes.POINTER(_AceHeader)).contents
                if header.AceType == _ACCESS_DENIED_ACE_TYPE:
                    continue  # denying access never makes a directory less private
                if header.AceType != _ACCESS_ALLOWED_ACE_TYPE:
                    return f"it has an access entry of type {header.AceType} that Studio cannot check"
                ace = ctypes.cast(ace_pointer, ctypes.POINTER(_AccessAce)).contents
                sid = _sid_string(ctypes.addressof(ace) + _AccessAce.SidStart.offset)
                if sid not in trusted:
                    return f"it grants access to {sid}"
            return None
        finally:
            _kernel32.LocalFree(descriptor)
