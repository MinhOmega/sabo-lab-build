"""Shared build policy and bounded private stage diagnostics."""
import datetime
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
           if not any(s in k.upper() for s in ('TOKEN', 'SECRET', 'PASSWORD', 'SIGNING_PRIVATE'))}
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
                result = subprocess.run(args, cwd=cwd, env=env, stdout=output,
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
