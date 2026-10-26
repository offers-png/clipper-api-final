"""Read-only Windows RAM/process counters. Does not alter OS settings or other apps."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path

def release_model_cache(*paths):
    """Advise away clean pages of our closed model files, never global caches.

    Linux charges recently used, unmapped weights to the container's active
    file cache. Release those after serial model phases so the next model's
    conservative startup check is not blocked by unused predecessor weights.
    Windows has no such call and remains unchanged.
    """
    advised=0
    if not hasattr(os,'posix_fadvise'):return advised
    for value in paths:
        try:
            path=Path(value)
            with path.open('rb') as stream:
                os.posix_fadvise(stream.fileno(),0,0,os.POSIX_FADV_DONTNEED)
                advised+=path.stat().st_size
        except OSError:pass
    return advised


def container_limit():
    """Read the existing cgroup allocation; never infer capacity from host RAM."""
    for limit,used in (('/sys/fs/cgroup/memory.max','/sys/fs/cgroup/memory.current'),
                       ('/sys/fs/cgroup/memory/memory.limit_in_bytes','/sys/fs/cgroup/memory/memory.usage_in_bytes')):
        try:
            ceiling=int(Path(limit).read_text().strip());current=int(Path(used).read_text().strip())
            if 0<ceiling<2**60:
                # Inactive file cache is reclaimable by the kernel. Counting
                # freshly downloaded model files as anonymous usage otherwise
                # falsely reports a full container before any inference starts.
                inactive=0
                try:
                    values=dict(line.split() for line in Path(limit).with_name('memory.stat').read_text().splitlines() if len(line.split())==2)
                    inactive=int(values.get('total_inactive_file',values.get('inactive_file','0')))
                except (OSError,ValueError):pass
                return ceiling,max(0,ceiling-current+min(current,max(0,inactive)))
        except (OSError,ValueError):pass
    return None,None


class MemoryStatus(ctypes.Structure):
    _fields_=[('length',wintypes.DWORD),('load',wintypes.DWORD)]+[(name,ctypes.c_ulonglong) for name in (
        'total_physical','available_physical','total_commit','available_commit','total_virtual','available_virtual','extended_virtual')]


def available():
    if os.name!='nt':
        _,free=container_limit()
        try:
            host=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
            return min(host,free) if free is not None else host
        except (OSError,ValueError,StopIteration):return free
    data=MemoryStatus();data.length=ctypes.sizeof(data)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(data)):
        raise RuntimeError('Could not read local memory status; AI startup was not attempted')
    return data.available_physical


class ProcessMemory(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in (
        'peak_working_set','working_set','peak_paged_pool','paged_pool','peak_nonpaged_pool','nonpaged_pool',
        'commit','peak_commit','private')]


def working_set(pid):
    if os.name!='nt':
        try:return int(next(line.split()[1] for line in Path(f'/proc/{int(pid)}/status').read_text().splitlines() if line.startswith('VmRSS:')))*1024
        except (OSError,ValueError,StopIteration):return None
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
