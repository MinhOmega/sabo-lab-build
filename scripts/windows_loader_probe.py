"""Failure-only, bounded Windows import/export diagnostics; never publishes binaries."""
import argparse
import ctypes
import json
import os
import pathlib
import re
import struct
import sys

MAX_PE_BYTES = 256 * 1024 * 1024
MAX_DLLS = 128
MAX_IMPORTS = 32768


def pe_imports(path):
    path = pathlib.Path(path)
    if path.stat().st_size > MAX_PE_BYTES:
        raise ValueError('PE exceeds diagnostic bound')
    data = path.read_bytes()
    def unpack(fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError('Truncated PE')
        return struct.unpack_from(fmt, data, offset)
    if data[:2] != b'MZ':
        raise ValueError('Not PE')
    pe, = unpack('<I', 0x3c)
    if data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Bad PE signature')
    sections, = unpack('<H', pe+6)
    optional_size, = unpack('<H', pe+20)
    if sections > 128:
        raise ValueError('Too many PE sections')
    optional = pe+24
    magic, = unpack('<H', optional)
    if magic not in (0x10b, 0x20b):
        raise ValueError('Unsupported PE format')
    width = 8 if magic == 0x20b else 4
    directories = optional+(112 if width == 8 else 96)
    import_rva, import_size = unpack('<II', directories+8)
    table = []
    for i in range(sections):
        section = optional+optional_size+40*i
        virtual_size, virtual_rva, raw_size, raw_offset = unpack('<IIII', section+8)
        table.append((virtual_rva, max(virtual_size, raw_size), raw_offset, raw_size))
    def offset(rva):
        for base, size, raw, raw_size in table:
            if base <= rva < base+size and rva-base < raw_size:
                return raw+rva-base
        if rva < optional+optional_size:
            return rva
        raise ValueError('Unmapped PE RVA')
    def text(rva):
        start = offset(rva)
        end = data.find(b'\0', start, min(start+512, len(data)))
        if end < 0:
            raise ValueError('Unbounded import name')
        return data[start:end].decode('ascii', errors='strict')
    if not import_rva:
        return []
    imports = []
    count = 0
    for i in range(min(MAX_DLLS, import_size//20+1)):
        original, _, _, name, first = unpack('<IIIII', offset(import_rva)+20*i)
        if not any((original, name, first)):
            return imports
        dll = text(name)
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', dll):
            raise ValueError('Invalid import DLL name')
        thunk = offset(original or first)
        symbols = []
        for j in range(MAX_IMPORTS-count):
            entry, = unpack('<Q' if width == 8 else '<I', thunk+j*width)
            if entry == 0:
                break
            symbols.append(entry & 0xffff if entry & (1 << (width*8-1)) else text(entry+2))
            count += 1
        else:
            raise ValueError('Too many PE imports')
        imports.append((dll, symbols))
    raise ValueError('Too many import DLLs')


def pe_exports(path):
    """Read literal-file exports, unaffected by the Python process's SxS context."""
    path=pathlib.Path(path)
    if path.stat().st_size>MAX_PE_BYTES:raise ValueError('PE exceeds diagnostic bound')
    data=path.read_bytes()
    def read(fmt,at):
        if at<0 or at+struct.calcsize(fmt)>len(data):raise ValueError('Truncated PE')
        return struct.unpack_from(fmt,data,at)
    if data[:2]!=b'MZ':raise ValueError('Not PE')
    pe,=read('<I',0x3c)
    if data[pe:pe+4]!=b'PE\0\0':raise ValueError('Bad PE signature')
    count,=read('<H',pe+6);size,=read('<H',pe+20);optional=pe+24;magic,=read('<H',optional)
    if count>128 or magic not in (0x10b,0x20b):raise ValueError('Unsupported PE')
    sections=[read('<IIII',optional+size+40*i+8) for i in range(count)]
    def offset(rva):
        for virtual_size,base,raw_size,raw in sections:
            if base<=rva<base+max(virtual_size,raw_size) and rva-base<raw_size:return raw+rva-base
        raise ValueError('Unmapped export RVA')
    directory=optional+(112 if magic==0x20b else 96);rva,_=read('<II',directory)
    if not rva:return set()
    table=offset(rva);base,functions,names,addresses,name_table,ordinals=read('<IIIIII',table+16)
    if functions>MAX_IMPORTS or names>MAX_IMPORTS:raise ValueError('Too many exports')
    exports=set()
    for i in range(functions):
        value,=read('<I',offset(addresses)+4*i)
        if value:exports.add(base+i)
    for i in range(names):
        name_rva,=read('<I',offset(name_table)+4*i);start=offset(name_rva)
        end=data.find(b'\0',start,min(start+512,len(data)))
        if end<0:raise ValueError('Unbounded export name')
        ordinal,=read('<H',offset(ordinals)+2*i)
        if ordinal>=functions:raise ValueError('Bad export ordinal')
        exports.add(data[start:end].decode('ascii',errors='strict'))
    return exports


def probe(path):
    if os.name != 'nt':
        return {'available': False, 'reason': 'Windows only'}
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetErrorMode(0x8003)  # No unattended loader dialog/error box.
    kernel.LoadLibraryExW.argtypes = [w.LPCWSTR, w.HANDLE, w.DWORD]
    kernel.LoadLibraryExW.restype = w.HMODULE
    kernel.GetProcAddress.argtypes = [w.HMODULE, ctypes.c_void_p]
    kernel.GetProcAddress.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [w.HMODULE, w.LPWSTR, w.DWORD]
    kernel.GetModuleFileNameW.restype = w.DWORD
    kernel.FreeLibrary.argtypes = [w.HMODULE]
    kernel.FreeLibrary.restype = w.BOOL
    path = pathlib.Path(path).resolve()
    # The test executable may lack the v6 common-controls activation manifest
    # embedded in the release GUI. Report the resource without executing it.
    kernel.FindResourceW.argtypes=[w.HMODULE,ctypes.c_void_p,ctypes.c_void_p]
    kernel.FindResourceW.restype=ctypes.c_void_p
    kernel.SizeofResource.argtypes=[w.HMODULE,ctypes.c_void_p];kernel.SizeofResource.restype=w.DWORD
    kernel.LoadResource.argtypes=[w.HMODULE,ctypes.c_void_p];kernel.LoadResource.restype=ctypes.c_void_p
    kernel.LockResource.argtypes=[ctypes.c_void_p];kernel.LockResource.restype=ctypes.c_void_p
    image=kernel.LoadLibraryExW(str(path),None,2)
    if image:
        resource=kernel.FindResourceW(image,1,24)  # CREATEPROCESS_MANIFEST_RESOURCE_ID / RT_MANIFEST
        size=kernel.SizeofResource(image,resource) if resource else 0
        address=kernel.LockResource(kernel.LoadResource(image,resource)) if 0<size<65536 else None
        manifest=ctypes.string_at(address,size).decode('utf-8',errors='replace') if address else ''
        print(json.dumps({'embeddedManifest':bool(resource),'commonControlsV6':'Microsoft.Windows.Common-Controls' in manifest and '6.0.0.0' in manifest}),flush=True)
        kernel.FreeLibrary(image)
    print(json.dumps({'probe': 'windows-loader-imports', 'binary': path.name, 'windows': sys.getwindowsversion().build}), flush=True)
    results = []
    for dll, symbols in pe_imports(path):
        if dll.lower()=='comctl32.dll':
            literal=pathlib.Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/dll
            try:
                exports=pe_exports(literal)
                print(json.dumps({'literalSystemDll':str(literal),'imports':symbols,'missingFromLiteralFile':[symbol for symbol in symbols if symbol not in exports],'note':'Static file exports; probe process may have a different SxS activation context from test executable.'}),flush=True)
            except (OSError,ValueError,struct.error):
                print(json.dumps({'literalSystemDll':dll,'reason':'Static export check unavailable'}),flush=True)
        # Use Windows' loader/API-set resolution. No DLL search/PATH environment is dumped.
        local = path.parent/dll
        target = str(local) if local.is_file() else dll
        flags = 0x1100 if local.is_file() else 0x1000
        print(json.dumps({'loadingDll': dll, 'importCount': len(symbols)}), flush=True)
        handle = kernel.LoadLibraryExW(target, None, flags)
        result = {'dll': dll, 'importCount': len(symbols)}
        if not handle:
            result['loadError'] = ctypes.get_last_error()
        else:
            filename = ctypes.create_unicode_buffer(32768)
            if kernel.GetModuleFileNameW(handle, filename, len(filename)):
                result['resolvedPath'] = filename.value
            missing = []
            for symbol in symbols:
                pointer = symbol if isinstance(symbol, int) else ctypes.cast(ctypes.c_char_p(symbol.encode('ascii')), ctypes.c_void_p)
                if not kernel.GetProcAddress(handle, pointer):
                    missing.append({'symbol': symbol, 'winError': ctypes.get_last_error()})
            result['missing'] = missing[:100]
            kernel.FreeLibrary(handle)
        results.append(result)
        print(json.dumps(result), flush=True)
    return {'available': True, 'dllCount': len(results), 'problemCount': sum(bool(r.get('loadError') or r.get('missing')) for r in results)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('target_dir', type=pathlib.Path)
    args = parser.parse_args()
    try:
        candidates = sorted((args.target_dir/'debug/deps').glob('desktop_lib-*.exe'), key=lambda p:p.stat().st_mtime, reverse=True)
        if not candidates:
            print(json.dumps({'available': False, 'reason': 'No desktop test executable'}))
        else:
            print(json.dumps(probe(candidates[0])))
    except (OSError, ValueError, struct.error):
        print(json.dumps({'available': False, 'reason': 'Import probe could not parse or load the binary'}))
