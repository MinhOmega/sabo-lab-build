import importlib.util
import pathlib
import struct
import tempfile
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('loader_probe',ROOT/'scripts/windows_loader_probe.py')
loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)


def image(width=8):
 data=bytearray(2048);data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,0x80);data[0x80:0x84]=b'PE\0\0'
 optsize=240 if width==8 else 224
 struct.pack_into('<H',data,0x86,1);struct.pack_into('<H',data,0x94,optsize)
 opt=0x98;struct.pack_into('<H',data,opt,0x20b if width==8 else 0x10b)
 directories=opt+(112 if width==8 else 96);struct.pack_into('<II',data,directories+8,0x1000,40)
 section=opt+optsize;struct.pack_into('<IIII',data,section+8,1024,0x1000,1024,512)
 struct.pack_into('<IIIII',data,512,0x1080,0,0,0x1040,0x1080)
 data[576:589]=b'COMCTL32.dll\0';struct.pack_into('<H',data,512+0xc0,0);data[512+0xc2:512+0xc2+19]=b'TaskDialogIndirect\0'
 fmt='<Q' if width==8 else '<I';struct.pack_into(fmt,data,512+0x80,0x10c0);struct.pack_into(fmt,data,512+0x80+width,(1<<(width*8-1))|17)
 return bytes(data)


class WindowsLoaderProbeTests(unittest.TestCase):
 def test_static_imports_support_x64_x86_names_and_ordinals_without_running_binary(self):
  for width in (4,8):
   with tempfile.TemporaryDirectory() as d:
    path=pathlib.Path(d)/'fixture.exe';path.write_bytes(image(width));self.assertEqual(loader.pe_imports(path),[('COMCTL32.dll',['TaskDialogIndirect',17])])
 def test_malformed_unbounded_and_path_imports_fail_closed(self):
  for raw in (b'',b'MZ',image()[:100],image().replace(b'COMCTL32.dll',b'../evil!.dll')):
   with tempfile.TemporaryDirectory() as d:
    path=pathlib.Path(d)/'fixture.exe';path.write_bytes(raw)
    with self.assertRaises((ValueError,struct.error)):loader.pe_imports(path)
 def test_nonwindows_does_not_attempt_win32_loading(self):
  if loader.os.name!='nt':self.assertFalse(loader.probe('does-not-exist')['available'])
 def test_probe_is_private_failure_only_bounded_and_never_replaces_gate(self):
  source=(ROOT/'scripts/signed_release.py').read_text();self.assertIn("if not stage['success']:",source);self.assertIn("platform=='windows' and STAGE_NAMES[index-1] in ('desktop-tests','license-tests')",source);self.assertIn("45,'windows-loader-probe'",source)
  self.assertIn("failed=index",source);self.assertNotIn('windows_loader_probe',source[source.index('def diagnostics('):])

if __name__=='__main__':unittest.main()
