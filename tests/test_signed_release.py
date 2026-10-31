import importlib.util, pathlib, sys, tempfile, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import signed_release as signed
class SignedReleaseTests(unittest.TestCase):
 def test_public_token_cannot_be_sent_to_private_or_external_destination(self):
  for path in ['/repos/MinhOmega/sabo-lab-next/releases','https://attacker.invalid/','/repos/other/repo/releases']:
   with self.assertRaises(signed.builder.SafeError):signed.public_api(path)
 def test_published_release_cannot_be_overwritten(self):
  with mock.patch.object(signed,'find_release',return_value={'draft':False}),mock.patch.object(signed,'public_api') as api:
   with self.assertRaises(signed.builder.SafeError):signed.prepare('app-v0.3.11')
   api.assert_not_called()
 def test_publish_requires_both_signed_platforms(self):
  with mock.patch.object(signed,'find_release',return_value={'draft':True,'assets':[]}),mock.patch.object(signed,'public_api') as api:
   with self.assertRaises(signed.builder.SafeError):signed.finalize('app-v0.3.11')
   api.assert_not_called()
 def test_version_tag_is_strict(self):
  for tag in ['app-v0.3.11;echo secret','latest','app-v../file','app-v0.3.11-beta']:
   with self.assertRaises(signed.builder.SafeError):signed.prepare(tag)
 def test_build_must_use_platform_matching_runner(self):
  other='macos' if signed.os.name=='nt' else 'windows'
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(signed.builder.SafeError):signed.run_build(pathlib.Path(d),other)
if __name__=='__main__':unittest.main()
