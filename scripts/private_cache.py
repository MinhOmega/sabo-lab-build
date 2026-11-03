#!/usr/bin/env python3
"""Optional dependency-only cache in the PRIVATE source repository.

Never use the public orchestration repository's Actions cache for private work.
No source tree, workspace crate output, bundle, signing file or credentials are
restored. Misses/corruption are cold builds, not a reason to weaken validation.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import traceback
import urllib.error
import urllib.parse
import urllib.request
import builder
import build_runtime

SCHEMA = 1
PART_SIZE = 128 * 1024**2
MAX_ARCHIVE_BY_PLATFORM = {'macos': 1024**3, 'windows': 2 * 1024**3}
MAX_EXPANDED = 6 * 1024**3
MAX_FILE = 512 * 1024**2
MAX_ENTRIES = 200000
MAX_MANIFEST = 128 * 1024
KEEP_PER_PLATFORM = 2
MARKER = 'Sabo private dependency cache v1'


def archive_limit(config):
    try:
        return MAX_ARCHIVE_BY_PLATFORM[config['platform']]
    except (KeyError, TypeError):
        raise builder.SafeError('CACHE_PLATFORM') from None


def part_limit(config):
    return (archive_limit(config) + PART_SIZE - 1) // PART_SIZE


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def workspace_names(source):
    names = set()
    for path in source.rglob('Cargo.toml'):
        if any(p in path.relative_to(source).parts for p in ('target', 'vendor', 'node_modules')):
            continue
        data = tomllib.loads(path.read_text(encoding='utf-8'))
        if name := data.get('package', {}).get('name'):
            names.add(name)
            names.add(name.replace('-', '_'))
        for entry in [data.get('lib', {})] + sum([data.get(k, []) for k in ('bin', 'test', 'bench', 'example')], []):
            if name := entry.get('name'):
                names.add(name)
                names.add(name.replace('-', '_'))
        for folder in ('src/bin', 'tests', 'benches', 'examples'):
            base = path.parent / folder
            if base.is_dir():
                for child in base.iterdir():
                    name = child.stem if child.is_file() and child.suffix == '.rs' else child.name if child.is_dir() and (child / 'main.rs').is_file() else ''
                    if name:
                        names.add(name)
                        names.add(name.replace('-', '_'))
    return sorted(names)


def dependency_config(source):
    result = {}
    for path in sorted(source.rglob('Cargo.lock')):
        if any(p in path.relative_to(source).parts for p in ('target', 'vendor', 'node_modules')):
            continue
        data = tomllib.loads(path.read_text(encoding='utf-8'))
        # Application version bumps must not evict third-party dependencies.
        result[str(path.relative_to(source))] = [p for p in data.get('package', []) if 'source' in p]
    for path in sorted(source.rglob('Cargo.toml')):
        if any(p in path.relative_to(source).parts for p in ('target', 'vendor', 'node_modules')):
            continue
        data = tomllib.loads(path.read_text(encoding='utf-8'))
        result[str(path.relative_to(source))] = {k: v for k, v in data.items()
            if k in ('dependencies', 'dev-dependencies', 'build-dependencies', 'target', 'features', 'profile', 'patch')}
    for relative in ('crates/sabo-bootstrap/native/frida-runtime.json',):
        path = source / relative
        if path.is_file():
            result[relative] = sha256(path)
    return result


def plan(root, platform):
    if platform not in ('windows', 'macos'):
        raise builder.SafeError('CACHE_PLATFORM')
    env = build_runtime.environment(root, platform)
    rust = subprocess.run(['rustc', '-Vv'], capture_output=True, text=True,
                          check=True, timeout=30, env=env).stdout
    target = 'x86_64-pc-windows-msvc' if platform == 'windows' else 'aarch64-apple-darwin'
    identity = {'schema': SCHEMA, 'platform': platform, 'target': target,
        'rustc': rust, 'imageOS': os.environ.get('ImageOS', ''),
        'imageVersion': os.environ.get('ImageVersion', ''),
        'root': str(root.resolve()), 'profile': build_runtime.PROFILE,
        'buildFlags': {k: env.get(k, '') for k in ('RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS', 'CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'MACOSX_DEPLOYMENT_TARGET', 'SDKROOT')},
        'dependencies': dependency_config(root / 'source'),
        'workspaceNames': workspace_names(root / 'source')}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {'schema': SCHEMA, 'key': key, 'platform': platform, 'target': target,
            'workspaceNames': identity['workspaceNames'],
            'tag': f'ci-deps-cache-v1-{platform}-{key[:32]}'}


def private_name(filename, names):
    filename = filename.casefold()
    for name in names:
        name = name.casefold()
        for prefix in (name, 'lib' + name):
            if filename == prefix or filename.startswith(prefix + '-') or filename.startswith(prefix + '.'):
                return True
    return False


def allowed(name, config):
    """Validate again when restoring; manifest cannot relax this policy."""
    if not name or '\\' in name or '\x00' in name or ':' in name or name.startswith('/'):
        return False
    parts = pathlib.PurePosixPath(name).parts
    if '/'.join(parts) != name or any(p in ('..', '.') or p.endswith((' ', '.')) or any(ord(c) < 32 for c in p) or p.split('.')[0].rstrip(' ').upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(n) for n in range(1,10)), *('LPT'+str(n) for n in range(1,10))} for p in parts):
        return False
    if not parts or parts[0] not in ('target', 'registry'):
        return False
    if parts[0] == 'registry':
        return len(parts) >= 3 and parts[1] in ('cache', 'index', 'src') and not any(
            p.casefold() in ('config', 'config.toml', 'credentials', 'credentials.toml', '.git') for p in parts[2:])
    i = 1
    if len(parts) > 1 and parts[1] == config['target']:
        i += 1
    if len(parts) <= i + 2 or parts[i] not in ('debug', 'release'):
        return False
    area = parts[i + 1]
    if area not in ('deps', '.fingerprint', 'build') or any(p in ('bundle', 'incremental', 'examples') for p in parts):
        return False
    # Every local/path package and custom lib/bin name is forbidden, regardless
    # of timestamps. A commit created while old CI compiles cannot reuse old app.
    return not private_name(parts[i + 2], config['workspaceNames'])


def cache_files(root, config):
    for prefix, base in [('target', root / 'target'), ('registry', root / 'cargo-home/registry')]:
        if not base.exists():
            continue
        for path in base.rglob('*'):
            if path.is_symlink() or not path.is_file():
                continue
            name = prefix + '/' + path.relative_to(base).as_posix()
            if allowed(name, config):
                yield path, name


class LimitedWriter:
    def __init__(self, output, limit):
        self.output, self.size, self.limit = output, 0, limit
    def write(self, data):
        self.size += len(data)
        if self.size > self.limit:
            raise builder.SafeError('CACHE_COMPRESSED_LIMIT')
        return self.output.write(data)
    def flush(self):
        return self.output.flush()


def pack(root, config, destination):
    count = total = 0
    with destination.open('xb') as raw:
        with gzip.GzipFile(fileobj=LimitedWriter(raw, archive_limit(config)), mode='wb', compresslevel=1, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|') as archive:
                for path, name in cache_files(root, config):
                    # Cargo can hardlink .rlib and .lib outputs on Windows.
                    # TarFile.gettarinfo deduplicates their inode as LNKTYPE,
                    # which our restore correctly rejects. Archive the bytes of
                    # each verified regular file; never admit a link entry.
                    before = path.lstat()
                    if (not stat.S_ISREG(before.st_mode) or
                            getattr(before, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)):
                        raise builder.SafeError('CACHE_FILE_CHANGED')
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
                    with os.fdopen(descriptor, 'rb') as stream:
                        current = os.fstat(stream.fileno())
                        if (not stat.S_ISREG(current.st_mode) or
                                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                                (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)):
                            raise builder.SafeError('CACHE_FILE_CHANGED')
                        count += 1
                        size = current.st_size
                        total += size
                        if count > MAX_ENTRIES or size > MAX_FILE or total > MAX_EXPANDED:
                            raise builder.SafeError('CACHE_EXPANDED_LIMIT')
                        item = tarfile.TarInfo(name)
                        item.size = size
                        item.mode = stat.S_IMODE(current.st_mode) & 0o777
                        item.mtime = current.st_mtime
                        archive.addfile(item, stream)
                        after = os.fstat(stream.fileno())
                        if (current.st_size, current.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            raise builder.SafeError('CACHE_FILE_CHANGED')
    return count, total


def extract(archive_path, destination, config):
    count = total = 0
    seen = set()
    destination.mkdir()
    with tarfile.open(archive_path, 'r|gz') as archive:
        for item in archive:
            count += 1
            total += item.size
            if (count > MAX_ENTRIES or item.size < 0 or item.size > MAX_FILE or
                    total > MAX_EXPANDED or not item.isfile() or not allowed(item.name, config) or item.name in seen):
                raise builder.SafeError('CACHE_ARCHIVE_POLICY')
            seen.add(item.name)
            path = destination.joinpath(*pathlib.PurePosixPath(item.name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as source, path.open('xb') as output:
                shutil.copyfileobj(source, output)
            os.chmod(path, item.mode & 0o777)
            os.utime(path, (item.mtime, item.mtime))
    return count, total


def download_asset(asset, destination, limit):
    if (not isinstance(asset.get('id'), int) or not isinstance(asset.get('size'), int)
            or not 0 < asset['size'] <= limit):
        raise builder.SafeError('CACHE_ASSET_LIMIT')
    request = urllib.request.Request(builder.API + '/repos/' + builder.REPOSITORY +
        '/releases/assets/' + str(asset['id']), headers={'Authorization': 'Bearer ' + builder.token(),
        'Accept': 'application/octet-stream', 'User-Agent': 'sabo-private-dependency-cache'})
    opener = urllib.request.build_opener(builder.NoRedirect())
    try:
        response = opener.open(request, timeout=60)
    except urllib.error.HTTPError as error:
        code = error.code
        location = error.headers.get('Location', '')
        error.close()
        if code != 302:
            raise builder.SafeError('CACHE_ASSET_HTTP') from None
        parsed = urllib.parse.urlsplit(location)
        if (parsed.scheme != 'https' or not parsed.hostname or
                not parsed.hostname.endswith('.githubusercontent.com') or
                parsed.username or parsed.password or parsed.port or parsed.fragment):
            raise builder.SafeError('CACHE_REDIRECT_HOST')
        # No PAT, cookies or API header is forwarded to the signed asset URL.
        response = opener.open(urllib.request.Request(location, headers={
            'User-Agent': 'sabo-private-dependency-cache'}), timeout=180)
    size = 0
    digest = hashlib.sha256()
    with response, destination.open('xb') as output:
        while data := response.read(1024 * 1024):
            size += len(data)
            if size > limit or size > asset['size']:
                raise builder.SafeError('CACHE_ASSET_LIMIT')
            output.write(data)
            digest.update(data)
    if size != asset['size']:
        raise builder.SafeError('CACHE_ASSET_TRUNCATED')
    actual = digest.hexdigest()
    if asset.get('digest') and asset['digest'] != 'sha256:' + actual:
        raise builder.SafeError('CACHE_ASSET_DIGEST')
    return actual


def restore(root, config):
    builder.ensure_private()
    release = builder.release_for(config['tag'])
    if not release:
        return 'miss'
    if not release.get('draft') or release.get('name') != MARKER:
        raise builder.SafeError('CACHE_RELEASE_POLICY')
    assets = {a['name']: a for a in release.get('assets', [])}
    manifest_asset = assets.get('cache-manifest.json')
    if not manifest_asset:
        return 'incomplete'
    with tempfile.TemporaryDirectory(prefix='sabo-cache-restore-', dir=root) as work:
        work = pathlib.Path(work)
        meta = work / 'manifest.json'
        download_asset(manifest_asset, meta, MAX_MANIFEST)
        data = json.loads(meta.read_text(encoding='utf-8'))
        if (data.get('schema') != SCHEMA or data.get('key') != config['key'] or
                data.get('platform') != config['platform'] or data.get('target') != config['target'] or
                data.get('workspaceNames') != config['workspaceNames'] or
                not re.fullmatch('[0-9a-f]{40}', data.get('sourceCommit', '')) or
                not isinstance(data.get('parts'), list) or not 1 <= len(data['parts']) <= part_limit(config) or
                not isinstance(data.get('archiveBytes'), int) or not 0 < data['archiveBytes'] <= archive_limit(config)):
            raise builder.SafeError('CACHE_MANIFEST_POLICY')
        archive = work / 'deps.tar.gz'
        size = 0
        with archive.open('xb') as output:
            for index, part in enumerate(data['parts']):
                name = f'deps-{index:02d}.part'
                asset = assets.get(name)
                if (part.get('name') != name or not asset or part.get('bytes') != asset.get('size')
                        or not re.fullmatch('[0-9a-f]{64}', part.get('sha256', ''))):
                    raise builder.SafeError('CACHE_PART_POLICY')
                path = work / name
                digest = download_asset(asset, path, PART_SIZE)
                if digest != part['sha256']:
                    raise builder.SafeError('CACHE_PART_DIGEST')
                size += path.stat().st_size
                if size > archive_limit(config):
                    raise builder.SafeError('CACHE_COMPRESSED_LIMIT')
                with path.open('rb') as stream:
                    shutil.copyfileobj(stream, output)
                path.unlink()
        if size != data.get('archiveBytes') or sha256(archive) != data.get('archiveSha256'):
            raise builder.SafeError('CACHE_ARCHIVE_DIGEST')
        stage = work / 'expanded'
        count, total = extract(archive, stage, config)
        if count != data.get('files') or total != data.get('expandedBytes'):
            raise builder.SafeError('CACHE_CONTENT_COUNT')
        destinations = [('target', root / 'target'), ('registry', root / 'cargo-home/registry')]
        if any(dst.exists() for _, dst in destinations):
            raise builder.SafeError('CACHE_DESTINATION_EXISTS')
        for name, dst in destinations:
            if (stage / name).exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(stage / name), str(dst))
    return 'hit'


def prune(config):
    prefix = 'ci-deps-cache-v1-' + config['platform'] + '-'
    rows = []
    for page in range(1, 11):
        batch = builder.api('/repos/' + builder.REPOSITORY + '/releases?per_page=100&page=' + str(page))
        rows.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise builder.SafeError('CACHE_RETENTION_QUERY_LIMIT')
    owned = sorted([r for r in rows if r.get('draft') and r.get('name') == MARKER
                    and r.get('tag_name', '').startswith(prefix)], key=lambda r: r.get('created_at', ''), reverse=True)
    for release in owned[KEEP_PER_PLATFORM:]:
        builder.api('/repos/' + builder.REPOSITORY + '/releases/' + str(release['id']), method='DELETE')


def save(root, config):
    status = json.loads((root / 'private-output/build-info.json').read_text(encoding='utf-8'))
    request = json.loads((root / 'request.json').read_text(encoding='utf-8'))
    if (status.get('status') != 'success' or status.get('sourceCommit') != request.get('sourceCommit')
            or not re.fullmatch('[0-9a-f]{40}', request.get('sourceCommit', ''))):
        raise builder.SafeError('CACHE_BUILD_NOT_SUCCESSFUL')
    builder.ensure_private()
    existing = builder.release_for(config['tag'])
    if existing and (not existing.get('draft') or existing.get('name') != MARKER):
        raise builder.SafeError('CACHE_RELEASE_POLICY')
    if existing and any(a['name'] == 'cache-manifest.json' for a in existing.get('assets', [])):
        return 'already-saved'
    if existing:
        # An interrupted cache is not a successful one. Rebuild it without
        # overwriting a completed immutable cache or any published release.
        created = datetime.datetime.fromisoformat(existing.get('created_at', '').replace('Z', '+00:00'))
        if (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds() < 7200:
            return 'save-in-progress'
        builder.api('/repos/' + builder.REPOSITORY + '/releases/' + str(existing['id']), method='DELETE')
    with tempfile.TemporaryDirectory(prefix='sabo-cache-save-', dir=root) as work:
        work = pathlib.Path(work)
        archive = work / 'deps.tar.gz'
        count, total = pack(root, config, archive)
        if not count:
            return 'empty'
        release = builder.api('/repos/' + builder.REPOSITORY + '/releases', method='POST', data={
            'tag_name': config['tag'], 'target_commitish': request['sourceCommit'], 'name': MARKER,
            'body': 'Private third-party compiler cache only. No source, workspace crates, bundles or signing keys.',
            'draft': True, 'prerelease': True})
        parts = []
        with archive.open('rb') as stream:
            for index in range(part_limit(config)):
                data = stream.read(PART_SIZE)
                if not data:
                    break
                path = work / f'deps-{index:02d}.part'
                path.write_bytes(data)
                parts.append({'name': path.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
                builder.upload_asset(release['id'], path)
                path.unlink()
            if stream.read(1):
                raise builder.SafeError('CACHE_COMPRESSED_LIMIT')
        manifest = {k: config[k] for k in ('schema', 'key', 'platform', 'target', 'workspaceNames')}
        manifest.update(sourceCommit=request['sourceCommit'], archiveBytes=archive.stat().st_size,
                        archiveSha256=sha256(archive), parts=parts, files=count, expandedBytes=total,
                        createdAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
        path = work / 'cache-manifest.json'
        builder.save_json(path, manifest)
        if path.stat().st_size > MAX_MANIFEST:
            raise builder.SafeError('CACHE_MANIFEST_LIMIT')
        builder.upload_asset(release['id'], path)  # Completion marker LAST.
    prune(config)
    return 'saved'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['restore', 'save'])
    p.add_argument('--root', type=pathlib.Path, required=True)
    p.add_argument('--platform', choices=['windows', 'macos'], required=True)
    a = p.parse_args()
    root = a.root.resolve()
    started = datetime.datetime.now(datetime.timezone.utc)
    outcome = 'unavailable'
    error_info = None
    try:
        config = plan(root, a.platform)
        outcome = restore(root, config) if a.action == 'restore' else save(root, config)
    except Exception as error:
        code = str(error) if isinstance(error, builder.SafeError) else ''
        safe_code = code if re.fullmatch('[A-Z][A-Z0-9_]{0,80}', code) else 'CACHE_SERVICE_UNAVAILABLE'
        error_info = {'code': safe_code, 'type': type(error).__name__,
            'frames': [{'file': pathlib.Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                       for frame in traceback.extract_tb(error.__traceback__)[-8:]]}
        if status := re.search(r'\bHTTP (\d{3})', code):error_info['httpStatus'] = int(status[1])
        # Cache availability is optional. Never print source paths, raw HTTP
        # failures, signed URLs, secrets or traceback into public Actions logs.
        outcome = 'unavailable-cold-build' if a.action == 'restore' else 'not-saved'
    elapsed = round((datetime.datetime.now(datetime.timezone.utc) - started).total_seconds(), 3)
    summary = {'action': a.action, 'outcome': outcome, 'durationSeconds': elapsed,
        'compressedLimitBytes': archive_limit({'platform': a.platform}),
        'expandedLimitBytes': MAX_EXPANDED, 'partBytes': PART_SIZE}
    if error_info:summary['error'] = error_info
    builder.save_json(root / ('cache-' + a.action + '.json'), summary)
    info = root / 'private-output/build-info.json'
    if info.is_file():
        status = json.loads(info.read_text(encoding='utf-8'))
        status['cache' + a.action.title()] = summary
        builder.save_json(info, status)
    print(f'Private dependency cache {a.action}: {outcome}; {elapsed}s. Workspace source is always rebuilt.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
