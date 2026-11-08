#!/usr/bin/env python3
"""Ephemeral keychain for the stable certificate-pinned macOS signing identity."""
import base64, binascii, contextlib, os, pathlib, re, secrets, shutil, subprocess, tempfile
import builder

COMMON_NAME = 'Sabo Lab Release Signing'
BUNDLE_ID = 'dev.minhvo.sabolab'
SECURITY = '/usr/bin/security'
CODESIGN = '/usr/bin/codesign'


def run(args, log, check=True, timeout=120):
    # argv carries keychain/p12 passwords: never logged. Output stays private.
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    log.write(result.stdout)
    log.flush()
    if check and result.returncode:
        raise builder.SafeError('macOS signing step failed; see PRIVATE diagnostic release.')
    return result.stdout.decode('utf-8', 'replace')


def search_list(log):
    out = run([SECURITY, 'list-keychains', '-d', 'user'], log)
    return [line.strip().strip('"') for line in out.splitlines() if line.strip()]


def find_identity(output):
    found = set(re.findall(r'^\s*\d+\)\s+([0-9A-F]{40})\s+"' + re.escape(COMMON_NAME) + r'"', output, re.M))
    if len(found) != 1:
        raise builder.SafeError('macOS signing identity not found.')
    return found.pop()


@contextlib.contextmanager
def keychain(log, env=None):
    """Yield the SHA-1 identity from a temporary keychain; always delete it."""
    env = os.environ if env is None else env
    encoded = re.sub(r'\s+', '', env.get('MACOS_SIGNING_P12_BASE64', ''))
    password = env.get('MACOS_SIGNING_P12_PASSWORD', '')
    if not encoded or not password:
        raise builder.SafeError('Missing macOS signing identity.')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise builder.SafeError('macOS signing identity unreadable.') from None
    folder = pathlib.Path(tempfile.mkdtemp(prefix='sabo-sign-'))
    path = str(folder / 'signing.keychain-db')
    p12 = folder / 'identity.p12'
    key = secrets.token_urlsafe(32)
    original = search_list(log)
    created = False
    try:
        with os.fdopen(os.open(p12, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as out:
            out.write(data)
        log.write(b'\nMACOS SIGNING KEYCHAIN\n')
        run([SECURITY, 'create-keychain', '-p', key, path], log)
        created = True
        run([SECURITY, 'set-keychain-settings', '-lut', '3600', path], log)
        run([SECURITY, 'unlock-keychain', '-p', key, path], log)
        run([SECURITY, 'import', str(p12), '-k', path, '-f', 'pkcs12', '-P', password, '-T', CODESIGN], log)
        p12.unlink()
        run([SECURITY, 'set-key-partition-list', '-S', 'apple-tool:,apple:,codesign:', '-s', '-k', key, path], log)
        run([SECURITY, 'list-keychains', '-d', 'user', '-s', path] + original, log)
        yield find_identity(run([SECURITY, 'find-identity', '-p', 'codesigning', path], log))
    finally:
        if created:
            run([SECURITY, 'list-keychains', '-d', 'user', '-s'] + original, log, check=False)
            run([SECURITY, 'delete-keychain', path], log, check=False)
        shutil.rmtree(folder, ignore_errors=True)


def parse_signature(details, requirement, identity):
    if re.search(r'adhoc', details, re.I):
        raise builder.SafeError('macOS app is ad-hoc signed.')
    if not re.search(r'^Authority=' + re.escape(COMMON_NAME) + r'\s*$', details, re.M):
        raise builder.SafeError('macOS app signer mismatch.')
    if not re.search(r'^Identifier=' + re.escape(BUNDLE_ID) + r'\s*$', details, re.M):
        raise builder.SafeError('macOS app identifier mismatch.')
    match = re.search(r'^\s*(?:#\s*)?designated =>\s*(.+)$', requirement, re.M)
    dr = re.sub(r'\s+', ' ', match[1]).strip() if match else ''
    # Only the exact certificate pin: cdhash or relaxed requirements change per build.
    pinned = r'identifier "' + re.escape(BUNDLE_ID) + r'" and (?:anchor|certificate (?:leaf|0|-1|root)) = H"' + identity + r'"'
    if 'cdhash' in dr.lower() or not re.fullmatch(pinned, dr, re.I):
        raise builder.SafeError('macOS designated requirement is not the stable certificate pin.')
    return {'authority': COMMON_NAME, 'identifier': BUNDLE_ID, 'certificateSha1': identity, 'designatedRequirement': dr}


def verify(app, identity, log):
    log.write(b'\nMACOS SIGNATURE VERIFY\n')
    run([CODESIGN, '--verify', '--deep', '--strict', str(app)], log)
    details = run([CODESIGN, '--display', '--verbose=4', str(app)], log)
    requirement = run([CODESIGN, '--display', '-r-', str(app)], log)
    return parse_signature(details, requirement, identity)
