"""Shared build policy and bounded private stage diagnostics."""
import datetime
import json
import sys
import os
import pathlib
import subprocess
import tempfile
import time

PROFILE = {
    'CARGO_PROFILE_RELEASE_LTO': 'thin',
    'CARGO_PROFILE_RELEASE_CODEGEN_UNITS': '16',
    # Compiler diagnostics keep source locations; CI test binaries need no PDB/DWARF.
    'CARGO_PROFILE_DEV_DEBUG': '0',
    'CARGO_PROFILE_TEST_DEBUG': '0',
}


def environment(root, platform, revision=None):
    env = {k: v for k, v in os.environ.items()
           if not any(s in k.upper() for s in ('TOKEN', 'SECRET', 'PASSWORD', 'SIGNING_PRIVATE', 'P12', 'CERTIFICATE'))}
    jobs = min(os.cpu_count() or 1, 3 if platform == 'windows' else 2)
    env.update(PROFILE, CARGO_TARGET_DIR=str(root / 'target'),
               CARGO_HOME=str(root / 'cargo-home'), CARGO_INCREMENTAL='0',
               CARGO_BUILD_JOBS=str(max(1, jobs)), PYTHONUTF8='1')
    for key in ('SABO_FAKE_AGENT', 'SABO_AGENT_SCRIPT'):
        env.pop(key, None)
    if revision:
        env['SABO_BUILD_REVISION'] = revision
    return env


def policy(platform):
    return {'cargoJobs': min(os.cpu_count() or 1, 3 if platform == 'windows' else 2),
            'releaseLto': 'thin', 'releaseCodegenUnits': 16,
            'ciDebugSymbols': False, 'incremental': False,
            'localPackagesRestoredFromCache': False}


def execute(args, cwd, env, stdout, stderr, timeout):
    if os.name != 'nt':
        return subprocess.run(args, cwd=cwd, env=env, stdout=stdout, stderr=stderr, timeout=timeout)
    # A compiler server can outlive Cargo and retain its cwd. Scope cleanup to
    # descendants we own; never taskkill global SDK processes on a shared host.
    job = WindowsStageJob()
    process = None
    try:
        process = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).with_name('stage_exec.py'))],
            stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, env=env, close_fds=True)
        job.assign(process.pid)
        # The trusted wrapper has started no child until it receives this JSON.
        process.stdin.write(json.dumps({'args': args, 'cwd': str(cwd)}).encode())
        process.stdin.close()
        code = process.wait(timeout=timeout)
        return subprocess.CompletedProcess(args, code)
    finally:
        job.close()
        if process is not None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


class WindowsStageJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('processTime', ctypes.c_longlong), ('jobTime', ctypes.c_longlong),
                ('flags', w.DWORD), ('minimumWorkingSet', ctypes.c_size_t),
                ('maximumWorkingSet', ctypes.c_size_t), ('activeProcessLimit', w.DWORD),
                ('affinity', ctypes.c_size_t), ('priorityClass', w.DWORD), ('schedulingClass', w.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(key, ctypes.c_ulonglong) for key in ('readCount', 'writeCount', 'otherCount', 'readBytes', 'writeBytes', 'otherBytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', Io), ('processMemory', ctypes.c_size_t),
                ('jobMemory', ctypes.c_size_t), ('peakProcessMemory', ctypes.c_size_t), ('peakJobMemory', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.kernel.SetInformationJobObject.restype = w.BOOL
        self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        self.kernel.OpenProcess.restype = w.HANDLE
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = w.BOOL
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.kernel.CloseHandle.restype = w.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError('Cannot create owned Windows stage job.')
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError('Cannot configure owned Windows stage job.')

    def assign(self, pid):
        process = self.kernel.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise OSError('Cannot access owned Windows stage process.')
        try:
            if not self.kernel.AssignProcessToJobObject(self.handle, process):
                raise OSError('Cannot assign owned Windows stage process.')
        finally:
            self.kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def run_stage(args, cwd, env, log, timeout, label):
    """Only the parent owns the aggregate log.

    Compiler servers can outlive cl/cargo while inheriting stdout on Windows.
    Give each stage a separate private temporary file outside the source tree;
    copy its completed output into the aggregate, then remove it if unlocked.
    A compiler cannot keep build-detail.txt open and fail source cleanup.
    """
    started = time.monotonic()
    began = datetime.datetime.now(datetime.timezone.utc).isoformat()
    code = None
    failure = None
    fd, name = tempfile.mkstemp(prefix='sabo-private-stage-', suffix='.log')
    path = pathlib.Path(name)
    try:
        with os.fdopen(fd, 'wb') as output:
            try:
                result = execute(args, cwd=cwd, env=env, stdout=output,
                                        stderr=subprocess.STDOUT, timeout=timeout)
                code = result.returncode
            except subprocess.TimeoutExpired:
                failure = 'timeout'
            except OSError:
                failure = 'process-start'
        with path.open('rb') as output:
            while block := output.read(1024 * 1024):
                log.write(block)
        log.flush()
    finally:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            # No source/signing key is stored here. The exclusive hosted VM is
            # destroyed after this job; never turn a successful test into a
            # failure because an SDK compiler server inherited its log handle.
            log.write(b'\nPrivate stage log cleanup deferred to isolated runner teardown.\n')
    return {'name': label, 'startedAt': began,
            'durationSeconds': round(time.monotonic() - started, 3),
            'returnCode': code, 'failure': failure,
            'success': code == 0 and failure is None}
