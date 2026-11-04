import importlib.util, pathlib, sys, tempfile, unittest, json, types
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import signed_release as signed
class SignedReleaseTests(unittest.TestCase):
 def test_private_diagnostics_retries_transient_failure_without_rebuilding_or_leaking_error(self):
  error=signed.builder.SafeError('GitHub API request failed (HTTP 500); fixture-sensitive-detail')
  with mock.patch.object(signed,'_diagnostics_once',side_effect=[error,None]) as operation,mock.patch.object(signed.time,'sleep') as sleep,mock.patch('builtins.print') as output:
   signed.diagnostics(pathlib.Path('fixture'))
   self.assertEqual(operation.call_count,2);sleep.assert_called_once_with(2)
   self.assertNotIn('fixture-sensitive-detail',str(output.call_args_list))
 def test_private_diagnostics_never_retries_auth_or_policy_failures(self):
  for message in ['GitHub API request failed (HTTP 401).','GitHub API request failed (HTTP 403).','Source repository must remain private.']:
   with mock.patch.object(signed,'_diagnostics_once',side_effect=signed.builder.SafeError(message)) as operation,mock.patch.object(signed.time,'sleep') as sleep:
    with self.assertRaises(signed.builder.SafeError):signed.diagnostics(pathlib.Path('fixture'))
    self.assertEqual(operation.call_count,1);sleep.assert_not_called()
 def test_private_diagnostics_transient_retries_are_bounded_and_fail_closed(self):
  with mock.patch.object(signed,'_diagnostics_once',side_effect=signed.builder.SafeError('HTTP 503 fixture-sensitive-detail')) as operation,mock.patch.object(signed.time,'sleep') as sleep:
   with self.assertRaisesRegex(signed.builder.SafeError,'bounded retries') as error:signed.diagnostics(pathlib.Path('fixture'))
   self.assertEqual(operation.call_count,4);self.assertEqual([c.args[0] for c in sleep.call_args_list],[2,5,15]);self.assertNotIn('fixture-sensitive-detail',str(error.exception))
 def test_private_diagnostics_network_timeout_is_retried(self):
  with mock.patch.object(signed,'_diagnostics_once',side_effect=[TimeoutError('fixture-sensitive-detail'),None]) as operation,mock.patch.object(signed.time,'sleep'):
   signed.diagnostics(pathlib.Path('fixture'));self.assertEqual(operation.call_count,2)
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
   with mock.patch.object(signed.build_runtime,'execute',side_effect=run),mock.patch.dict(signed.os.environ,{'TAURI_SIGNING_PRIVATE_KEY':'fixture-only-secret'}):
    with self.assertRaises(signed.builder.SafeError):signed.run_build(root,platform)
   self.assertTrue(any('crates/sabo-bootstrap/Cargo.toml' in c for c in calls))
   self.assertFalse(any('tauri' in c for c in calls))
   self.assertEqual(json.loads((root/'private-output/build-info.json').read_text())['status'],'failed')
 def test_even_a_successful_verification_bundle_cannot_be_uploaded_publicly(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);out=root/'private-output';out.mkdir();(out/'build-info.json').write_text(json.dumps({'status':'success','version':'0.3.19','verificationOnly':True}))
   with mock.patch.object(signed,'find_release') as lookup,mock.patch.object(signed,'upload') as upload:
    with self.assertRaises(signed.builder.SafeError):signed.upload_bundle(root,'app-v0.3.19')
    lookup.assert_not_called();upload.assert_not_called()
 def test_verification_workflow_cannot_publish_binary_or_feed(self):
  workflow=(ROOT/'.github/workflows/verify-delivery.yml').read_text()
  self.assertIn('signed_release.py verify',workflow)
  self.assertIn('permissions:\n      contents: read',workflow)
  self.assertNotIn('GH_TOKEN:',workflow)
  self.assertNotIn('signed_release.py upload',workflow)
  self.assertNotIn('signed_release.py finalize',workflow)
  self.assertNotIn('pull_request:',workflow)
 def test_actions_are_pinned_node24_and_public_cache_is_disabled(self):
  for name in ['self-test.yml','signed-desktop.yml','windows-private-source.yml','verify-delivery.yml']:
   source=(ROOT/'.github/workflows'/name).read_text()
   self.assertNotIn('ubuntu-latest',source)
   self.assertIn('actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',source)
   self.assertNotIn('uses: actions/cache@',source)
   if 'actions/setup-node@' in source:self.assertIn('package-manager-cache: false',source)
if __name__=='__main__':unittest.main()

class ReleaseHistoryTests(unittest.TestCase):
 def release(self,version,draft=False,complete=True):
  names=['latest.json']
  if complete:
   for _,_,_,_,suffix in signed.TARGETS.values():names+=['SaboLab-'+version+'-'+suffix,'SaboLab-'+version+'-'+suffix+'.sig']
  return {'tag_name':'app-v'+version,'draft':draft,'prerelease':False,'published_at':'2026-11-04T00:00:00Z','assets':[{'name':name} for name in names]}
 def test_history_is_bounded_numeric_and_contains_current_draft_plus_ten_signed_releases(self):
  current=self.release('0.4.5',draft=True);rows=[self.release('0.3.'+str(i)) for i in range(9,25)];history=signed.release_history(current,rows)
  self.assertEqual(history['schema'],1);self.assertEqual(len(history['releases']),11);self.assertEqual(history['releases'][0]['version'],'0.4.5');self.assertEqual(history['releases'][1]['version'],'0.3.24');self.assertTrue(all(row['manifestUrl'].startswith('https://github.com/MinhOmega/sabo-lab-build/releases/download/app-v') for row in history['releases']))
 def test_history_excludes_other_drafts_unsigned_incomplete_and_unstable_tags(self):
  current=self.release('0.4.5',draft=True);bad=self.release('0.4.4');bad['prerelease']=True
  rows=[self.release('0.4.3',draft=True),self.release('0.4.2',complete=False),{**self.release('0.4.1'),'tag_name':'app-v0.4.1-beta'},bad,self.release('0.4.0')]
  self.assertEqual([r['version'] for r in signed.release_history(current,rows)['releases']],['0.4.5','0.4.0'])
