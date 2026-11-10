"""Launch outside the calling terminal's Windows job, with no inherited handles."""
import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys


class STARTUPINFO(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR),
                ('lpDesktop', wintypes.LPWSTR), ('lpTitle', wintypes.LPWSTR),
                ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
                ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD),
                ('dwXCountChars', wintypes.DWORD), ('dwYCountChars', wintypes.DWORD),
                ('dwFillAttribute', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
                ('lpReserved2', ctypes.POINTER(ctypes.c_byte)),
                ('hStdInput', wintypes.HANDLE), ('hStdOutput', wintypes.HANDLE),
                ('hStdError', wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE),
                ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--log-prefix', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    command = subprocess.list2cmdline([sys.executable, '-u', str(root / 'run_server.py'),
                                       '--port', str(args.port), '--log-prefix', args.log_prefix])
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFO), ctypes.POINTER(PROCESS_INFORMATION)]
    kernel.CreateProcessW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    startup = STARTUPINFO()
    startup.cb = ctypes.sizeof(startup)
    startup.dwFlags = 1  # STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = PROCESS_INFORMATION()
    flags = 0x01000000 | 0x00000008 | 0x00000200  # BREAKAWAY_FROM_JOB | DETACHED_PROCESS | NEW_PROCESS_GROUP
    if not kernel.CreateProcessW(None, ctypes.create_unicode_buffer(command), None, None,
                                  False, flags, None, str(root), ctypes.byref(startup), ctypes.byref(process)):
        raise ctypes.WinError(ctypes.get_last_error())
    print(process.dwProcessId)
    kernel.CloseHandle(process.hThread)
    kernel.CloseHandle(process.hProcess)


if __name__ == '__main__':
    main()
