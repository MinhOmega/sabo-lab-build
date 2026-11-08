import pathlib, sys, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import auto_release as auto
SHA='a'*40;OTHER='b'*40;TREE='c'*40;DATE='2026-11-08T10:00:00Z'
class AutoReleaseTests(unittest.TestCase):
 VERIFIED={'macos':'success','windows':'success'}
 def run_decide(self,tag='app-v0.7.4',release=None,top=(0,7,3),active=False,state=VERIFIED,verifying=False,auto_sent=False):
  with mock.patch.object(auto,'main_version',return_value=(SHA,tag)),mock.patch.object(auto.signed,'find_release',return_value=release),mock.patch.object(auto,'highest_published',return_value=top),mock.patch.object(auto,'active_release_run',return_value=active),mock.patch.object(auto,'commit_tree',return_value=(TREE,DATE)),mock.patch.object(auto,'verification_state',return_value=dict(state)),mock.patch.object(auto,'active_verify_run',return_value=verifying),mock.patch.object(auto,'auto_verify_since',return_value=auto_sent),mock.patch.object(auto,'dispatch') as dispatch,mock.patch.object(auto,'dispatch_verify') as verify:
   self.last_verify=verify
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

class VerificationGateTests(unittest.TestCase):
 VERIFIED=AutoReleaseTests.VERIFIED;run_decide=AutoReleaseTests.run_decide
 def test_missing_verification_dispatches_full_verify_not_signed(self):
  for state in ({},{'macos':'success'},{'windows':'success'}):
   (action,_),dispatch=self.run_decide(state=state)
   self.assertEqual(action,'verifying');dispatch.assert_not_called();self.last_verify.assert_called_once_with(SHA)
 def test_running_verification_is_not_duplicated(self):
  (action,_),dispatch=self.run_decide(state={},verifying=True)
  self.assertEqual(action,'skip');dispatch.assert_not_called();self.last_verify.assert_not_called()
 def test_auto_verify_without_evidence_is_not_retried_in_a_loop(self):
  (action,_),dispatch=self.run_decide(state={'macos':'success'},auto_sent=True)
  self.assertEqual(action,'skip');dispatch.assert_not_called();self.last_verify.assert_not_called()
 def test_failed_verification_blocks_release_with_generic_message(self):
  for state in ({'macos':'failed','windows':'success'},{'windows':'failed'}):
   (action,detail),dispatch=self.run_decide(state=state)
   self.assertEqual(action,'skip');dispatch.assert_not_called();self.last_verify.assert_not_called()
   self.assertNotIn(SHA,detail);self.assertNotIn(TREE,detail);self.assertNotIn(SHA[:12],detail)
 def test_verify_dispatch_requests_full_mode(self):
  with mock.patch.object(auto,'dispatch_workflow') as send:
   auto.dispatch_verify(SHA)
  send.assert_called_once_with('verify-delivery.yml',{'source_sha':SHA,'mode':'full'})

def release(platform,source,run=1,assets=True):
 return {'tag_name':'verify-%s-ci-%s-%d'%(platform,source[:12],run),'target_commitish':source,'draft':True,'assets':[{'name':'build-info.json','id':run,'size':10}] if assets else []}
def info(platform,source,status='success',mode='full'):
 row={'platform':platform,'sourceCommit':source,'status':status,'verificationOnly':True}
 if mode is not None:row['mode']=mode
 return row
class EvidenceTests(unittest.TestCase):
 def state(self,releases,infos,trees):
  def api(path,**_):
   if '/releases?' in path:return releases if path.endswith('page=1') else []
   sha=path.rsplit('/',1)[1];return {'tree':{'sha':trees[sha]},'committer':{'date':DATE}}
  lookup={r['tag_name']:i for r,i in zip(releases,infos)}
  with mock.patch.object(auto.builder,'api',side_effect=api) as call,mock.patch.object(auto,'read_build_info',side_effect=lambda r:lookup[r['tag_name']]):
   self.calls=call
   return auto.verification_state(SHA,TREE)
 def test_both_platforms_full_success_on_main_sha(self):
  rows=[release('macos',SHA),release('windows',SHA)]
  self.assertEqual(self.state(rows,[info('macos',SHA),info('windows',SHA)],{}),{'macos':'success','windows':'success'})
 def test_same_tree_with_different_commit_sha_counts(self):
  rows=[release('macos',OTHER),release('windows',OTHER)]
  self.assertEqual(self.state(rows,[info('macos',OTHER),info('windows',OTHER)],{OTHER:TREE}),{'macos':'success','windows':'success'})
 def test_different_tree_never_counts(self):
  rows=[release('macos',OTHER),release('windows',OTHER)]
  self.assertEqual(self.state(rows,[info('macos',OTHER),info('windows',OTHER)],{OTHER:'d'*40}),{})
 def test_quick_or_unlabelled_mode_is_not_release_evidence(self):
  quick={'tag_name':'verify-quick-macos-ci-%s-3'%SHA[:12],'target_commitish':SHA,'assets':[]}
  rows=[quick,release('macos',SHA,2),release('windows',SHA,1)]
  self.assertEqual(self.state(rows,[info('macos',SHA,mode='quick'),info('macos',SHA,mode='quick'),info('windows',SHA,mode=None)],{}),{})
 def test_newest_result_per_platform_wins(self):
  rows=[release('macos',SHA,3),release('macos',SHA,2),release('windows',SHA,3),release('windows',SHA,2)]
  infos=[info('macos',SHA,'failed'),info('macos',SHA),info('windows',SHA),info('windows',SHA,'failed')]
  self.assertEqual(self.state(rows,infos,{}),{'macos':'failed','windows':'success'})
 def test_mismatched_or_unverified_build_info_is_ignored(self):
  rows=[release('macos',SHA),release('windows',SHA)]
  bad=[{**info('macos',SHA),'sourceCommit':OTHER},{**info('windows',SHA),'verificationOnly':False}]
  self.assertEqual(self.state(rows,bad,{}),{})
 def test_tag_prefix_must_match_target_commit(self):
  row=release('macos',SHA);row['tag_name']='verify-macos-ci-%s-1'%OTHER[:12]
  self.assertEqual(self.state([row],[info('macos',SHA)],{}),{})
 def test_tree_lookups_are_bounded(self):
  sources=['%040x'%(i+1) for i in range(auto.MAX_TREE_LOOKUPS+5)]
  rows=[release('macos',s,i) for i,s in enumerate(sources)]
  self.state(rows,[info('macos',s) for s in sources],{s:'d'*40 for s in sources})
  self.assertEqual(sum('/git/commits/' in c.args[0] for c in self.calls.call_args_list),auto.MAX_TREE_LOOKUPS)
 def test_release_pages_are_bounded(self):
  page=[{'tag_name':'signed-macos-ci-x'}]*100
  with mock.patch.object(auto.builder,'api',return_value=page) as call:
   self.assertEqual(auto.verification_state(SHA,TREE),{})
  self.assertEqual(call.call_count,auto.MAX_RELEASE_PAGES)
 def test_quick_runs_do_not_count_as_active_or_auto_dispatched(self):
  quick={'display_title':'Verify delivery (quick)','triggering_actor':{'login':auto.BOT}}
  full={'display_title':'Verify delivery (full)','triggering_actor':{'login':auto.BOT}}
  manual={'display_title':'Verify delivery (full)','triggering_actor':{'login':'someone'}}
  with mock.patch.object(auto.signed,'public_api',return_value={'workflow_runs':[quick]}):
   self.assertFalse(auto.active_verify_run());self.assertFalse(auto.auto_verify_since(DATE))
  with mock.patch.object(auto.signed,'public_api',return_value={'workflow_runs':[manual]}):
   self.assertTrue(auto.active_verify_run());self.assertFalse(auto.auto_verify_since(DATE))
  with mock.patch.object(auto.signed,'public_api',return_value={'workflow_runs':[full]}) as api:
   self.assertTrue(auto.auto_verify_since(DATE));self.assertIn('created=%3E%3D2026-11-08T10%3A00%3A00Z',api.call_args.args[0])
 def test_commit_tree_rejects_malformed_response(self):
  with mock.patch.object(auto.builder,'api',return_value={'tree':{'sha':'x'}}):
   with self.assertRaises(auto.builder.SafeError):auto.commit_tree(SHA)
if __name__=='__main__':unittest.main()
