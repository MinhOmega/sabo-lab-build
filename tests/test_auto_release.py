import pathlib, sys, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import auto_release as auto
SHA='a'*40
class AutoReleaseTests(unittest.TestCase):
 def run_decide(self,tag='app-v0.7.4',release=None,top=(0,7,3),active=False):
  with mock.patch.object(auto,'main_version',return_value=(SHA,tag)),mock.patch.object(auto.signed,'find_release',return_value=release),mock.patch.object(auto,'highest_published',return_value=top),mock.patch.object(auto,'active_release_run',return_value=active),mock.patch.object(auto,'dispatch') as dispatch:
   return auto.decide(),dispatch
 def test_new_stable_version_dispatches_exact_sha_and_tag(self):
  (action,_),dispatch=self.run_decide()
  self.assertEqual(action,'dispatched');dispatch.assert_called_once_with(SHA,'app-v0.7.4')
 def test_existing_draft_or_published_tag_is_never_redispatched(self):
  for release in ({'draft':True},{'draft':False}):
   (action,_),dispatch=self.run_decide(release=release)
   self.assertEqual(action,'skip');dispatch.assert_not_called()
 def test_older_or_equal_version_is_skipped(self):
  for top in ((0,7,4),(0,8,0)):
   (action,_),dispatch=self.run_decide(top=top)
   self.assertEqual(action,'skip');dispatch.assert_not_called()
 def test_active_signed_run_blocks_duplicate_dispatch(self):
  (action,_),dispatch=self.run_decide(active=True)
  self.assertEqual(action,'skip');dispatch.assert_not_called()
 def test_main_version_rejects_prerelease(self):
  import base64,json
  row={'content':base64.b64encode(json.dumps({'version':'1.0.0-rc.1'}).encode()).decode()}
  with mock.patch.object(auto.builder,'select_commit',return_value=SHA),mock.patch.object(auto.builder,'api',return_value=row):
   with self.assertRaises(auto.builder.SafeError):auto.main_version()
 def test_main_version_reads_package_at_resolved_sha(self):
  import base64,json
  row={'content':base64.b64encode(json.dumps({'version':'0.7.4'}).encode()).decode()}
  with mock.patch.object(auto.builder,'select_commit',return_value=SHA),mock.patch.object(auto.builder,'api',return_value=row) as api:
   self.assertEqual(auto.main_version(),(SHA,'app-v0.7.4'))
   self.assertIn('ref='+SHA,api.call_args.args[0])
 def test_failure_output_is_generic(self):
  with mock.patch.object(auto,'decide',side_effect=RuntimeError('fixture-secret')),mock.patch('builtins.print') as out:
   self.assertEqual(auto.main(),1);self.assertNotIn('fixture-secret',str(out.call_args_list))
 def test_version_key(self):
  self.assertEqual(auto.version_key('app-v0.7.10'),(0,7,10));self.assertIsNone(auto.version_key('app-v1.0.0-rc.1'))
if __name__=='__main__':unittest.main()
