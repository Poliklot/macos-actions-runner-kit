"""Read-only default-keychain query for the effective macOS user.

Use the same Security.framework API as `security default-keychain -d user`,
but distinguish errSecNoDefaultKeychain numerically, not by localized stderr.
No login, password, unlock, keychain creation or preference changes here.
"""
import ctypes
import os


def default_keychain() -> list[str]:
    security = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
    security.SecKeychainCopyDomainDefault.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    security.SecKeychainCopyDomainDefault.restype = ctypes.c_int32
    security.SecKeychainGetPath.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
    security.SecKeychainGetPath.restype = ctypes.c_int32
    core.CFRelease.argtypes = [ctypes.c_void_p]
    core.CFRelease.restype = None
    ref = ctypes.c_void_p()
    try:
        status = security.SecKeychainCopyDomainDefault(0, ctypes.byref(ref))  # user domain
        if status == -25307:  # errSecNoDefaultKeychain: normal for a fresh CI account.
            return []
        if status != 0:
            raise OSError(f'SecKeychainCopyDomainDefault: OSStatus {status}')
        if not ref.value:
            return []
        path = ctypes.create_string_buffer(4096)
        length = ctypes.c_uint32(len(path))
        status = security.SecKeychainGetPath(ref, ctypes.byref(length), path)
        if status != 0:
            raise OSError(f'SecKeychainGetPath: OSStatus {status}')
        if not 0 < length.value < len(path):
            raise OSError('Security.framework returned an invalid keychain path')
        return [os.fsdecode(path.raw[:length.value])]
    finally:
        if ref.value:
            core.CFRelease(ref)
