import importlib.util, pathlib, sys, tempfile, unittest, json, types
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
 def test_failed_bootstrap_validation_blocks_bundle_and_keeps_pinned_revision(self):
  platform='windows' if signed.os.name=='nt' else 'macos';calls=[];revision='a'*40
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);desktop=root/'source/apps/desktop';desktop.mkdir(parents=True)
   (root/'request.json').write_text(json.dumps({'sourceCommit':revision,'repository':'MinhOmega/sabo-lab-next'}))
   (desktop/'package.json').write_text('{"version":"0.3.14"}')
   def run(args,**kwargs):
    calls.append(args);self.assertEqual(kwargs['env']['SABO_BUILD_REVISION'],revision)
    self.assertNotIn('TAURI_SIGNING_PRIVATE_KEY',kwargs['env'])
    return types.SimpleNamespace(returncode=1 if 'crates/sabo-bootstrap/Cargo.toml' in args else 0)
   with mock.patch.object(signed.subprocess,'run',side_effect=run),mock.patch.dict(signed.os.environ,{'TAURI_SIGNING_PRIVATE_KEY':'fixture-only-secret'}):
    with self.assertRaises(signed.builder.SafeError):signed.run_build(root,platform)
   self.assertTrue(any('crates/sabo-bootstrap/Cargo.toml' in c for c in calls))
   self.assertFalse(any('tauri' in c for c in calls))
   self.assertEqual(json.loads((root/'private-output/build-info.json').read_text())['status'],'failed')
if __name__=='__main__':unittest.main()
