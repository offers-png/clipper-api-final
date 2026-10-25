"""Read-only Windows RAM/process counters. Does not alter OS settings or other apps."""
import ctypes
from ctypes import wintypes
import os


class MemoryStatus(ctypes.Structure):
    _fields_=[('length',wintypes.DWORD),('load',wintypes.DWORD)]+[(name,ctypes.c_ulonglong) for name in (
        'total_physical','available_physical','total_commit','available_commit','total_virtual','available_virtual','extended_virtual')]


def available():
    if os.name!='nt':return None
    data=MemoryStatus();data.length=ctypes.sizeof(data)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(data)):
        raise RuntimeError('Could not read local memory status; AI startup was not attempted')
    return data.available_physical


class ProcessMemory(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in (
        'peak_working_set','working_set','peak_paged_pool','paged_pool','peak_nonpaged_pool','nonpaged_pool',
        'commit','peak_commit','private')]


def working_set(pid):
    if os.name!='nt':return None
    kernel=ctypes.windll.kernel32
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000|0x0010,False,pid)
    if not handle:return None
    try:
        data=ProcessMemory();data.cb=ctypes.sizeof(data)
        fn=ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessMemory),wintypes.DWORD]
        if fn(handle,ctypes.byref(data),data.cb):return data.working_set
        return None
    finally:kernel.CloseHandle(handle)
