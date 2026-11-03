import importlib.util, io, json, pathlib, tarfile, tempfile, unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('builder',pathlib.Path(__file__).parents[1]/'scripts/builder.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
SHA='a'*40
class BuilderTests(unittest.TestCase):
 def test_manual_ref_must_be_full_commit_not_shell_or_branch(self):
  for value in ['main','HEAD','a'*39,'a'*41,';echo hacked','$(bad)']:
   with self.assertRaises(b.SafeError):b.select_commit(value)
  with patch.object(b,'api',return_value={'sha':SHA}):self.assertEqual(b.select_commit(SHA),SHA)
  with patch.object(b,'api',return_value={'sha':'b'*40}):
   with self.assertRaises(b.SafeError):b.select_commit(SHA)
 def test_api_delete_accepts_empty_204_without_json_body(self):
  class Response:
   status=204
   def __enter__(self):return self
   def __exit__(self,*args):return False
  with patch.object(b,'token',return_value='fake-test-only'),patch.object(b.urllib.request,'urlopen',return_value=Response()):
   self.assertEqual(b.api('/repos/'+b.REPOSITORY+'/releases/assets/9',method='DELETE'),{})
 def test_destination_is_always_the_single_private_repo(self):
  with self.assertRaises(b.SafeError):b.api('/repos/attacker/other/releases')
  with patch.object(b,'api',return_value={'private':False}):
   with self.assertRaises(b.SafeError):b.ensure_private()
 def test_archive_selection_rejects_traversal_and_links(self):
  for name in ['/repo/apps/file','repo/apps/../../escape','repo/apps/C:/escape','repo/apps\\evil']:
   with self.assertRaises(b.SafeError):b.valid_archive_path(name,'repo')
  self.assertEqual(b.valid_archive_path('repo/apps/desktop/a.rs','repo'),'apps/desktop/a.rs')
  with tempfile.TemporaryDirectory() as d:
   archive=pathlib.Path(d)/'src.tar.gz'
   with tarfile.open(archive,'w:gz') as t:
    for name in ['repo/apps/main.rs','repo/research/private-detail.txt']:
     data=b'example';item=tarfile.TarInfo(name);item.size=len(data);t.addfile(item,io.BytesIO(data))
   root=pathlib.Path(d)/'source';b.unpack(archive,root)
   self.assertTrue((root/'apps/main.rs').is_file());self.assertFalse((root/'research').exists())
   with tarfile.open(archive,'w:gz') as t:
    item=tarfile.TarInfo('repo/apps/link');item.type=tarfile.SYMTYPE;item.linkname='/outside';t.addfile(item)
   with self.assertRaises(b.SafeError):b.unpack(archive,pathlib.Path(d)/'other')
 def test_command_plan_contains_all_checks_and_nsis_without_release_credentials(self):
  commands=list(b.commands(pathlib.Path('/source')));flat='\n'.join(' '.join(args) for _,args in commands)
  self.assertIn('scripts/prepare-assets.py',flat);self.assertIn('npm',flat);self.assertIn('--locked',flat)
  for name in ['sabo-core','sabo-bootstrap','sabo-task-api','sabo-transport','sabo-navigation','sabo-routines','sabo-quests']:
   self.assertIn('crates/'+name+'/Cargo.toml',flat)
  self.assertIn('x86_64-pc-windows-msvc',flat);self.assertIn('nsis',flat);self.assertNotIn('TOKEN',flat)
 def test_native_only_plan_cannot_build_an_installer(self):
  commands=list(b.commands(pathlib.Path('/source'),True));self.assertEqual(len(commands),2)
  flat='\n'.join(' '.join(args) for _,args in commands)
  self.assertIn('crates/sabo-bootstrap/Cargo.toml',flat);self.assertIn('--nocapture',flat)
  self.assertNotIn('tauri',flat);self.assertNotIn('npm',flat)
 def test_failed_build_cannot_publish_or_retain_a_success_marker(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);out=root/'private-output';out.mkdir();tag='windows-ci-'+SHA[:12]
   b.save_json(out/'build-info.json',{'status':'failed','tag':tag,'sourceCommit':SHA})
   (out/'build-detail.txt').write_text('fake private compiler output')
   release={'id':1,'draft':True,'assets':[{'name':'BUILD-SUCCEEDED.json','id':9},{'name':'SaboLab-0.3.10-windows-x64-setup.exe','id':10},{'name':'SHA256SUMS.txt','id':11}]}
   with patch.object(b,'ensure_private'),patch.object(b,'release_for',return_value=release),patch.object(b,'api') as api,patch.object(b,'upload_asset') as upload:
    b.publish(root,True)
    self.assertEqual(api.call_args_list[1].kwargs.get('method'),'DELETE')
    self.assertTrue(api.call_args_list[1].args[0].endswith('/9'))
    self.assertFalse(any(call.kwargs.get('data',{}).get('draft') is False for call in api.call_args_list))
    self.assertNotIn('BUILD-SUCCEEDED.json',[call.args[1].name for call in upload.call_args_list])
 def test_success_uploads_marker_last_then_optional_private_prerelease(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);out=root/'private-output';out.mkdir();status={'status':'success','sourceCommit':SHA,'tag':'windows-ci-'+SHA[:12],'installer':'SaboLab-0.3.10-windows-x64-setup.exe'}
   for name in ['build-info.json','BUILD-SUCCEEDED.json']:b.save_json(out/name,status)
   for name in ['build-detail.txt','SHA256SUMS.txt',status['installer']]:(out/name).write_text('fixture')
   with patch.object(b,'ensure_private'),patch.object(b,'release_for',return_value=None),patch.object(b,'api',return_value={'id':2}),patch.object(b,'upload_asset') as upload:
    b.publish(root,False);self.assertEqual(upload.call_args_list[-1].args[1].name,'BUILD-SUCCEEDED.json')
   with patch.object(b,'ensure_private'),patch.object(b,'release_for',return_value={'id':2,'draft':False}):
    with self.assertRaises(b.SafeError):b.publish(root,True)
 def test_upload_failure_never_reaches_publish(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);out=root/'private-output';out.mkdir();b.save_json(out/'build-info.json',{'status':'success','sourceCommit':SHA,'tag':'windows-ci-'+SHA[:12]})
   with patch.object(b,'ensure_private'),patch.object(b,'release_for',return_value=None),patch.object(b,'api',return_value={'id':1}) as api,patch.object(b,'upload_asset',side_effect=b.SafeError('upload failed')):
    with self.assertRaises(b.SafeError):b.publish(root,True)
    self.assertEqual(api.call_count,1)
 def test_workflow_does_not_expose_public_artifacts_or_untrusted_events(self):
  workflow=(pathlib.Path(__file__).parents[1]/'.github/workflows/windows-private-source.yml').read_text()
  for value in ['upload-artifact','cache:','pull_request:','pull_request_target:','repository_dispatch:']:self.assertNotIn(value,workflow)
  self.assertIn('persist-credentials: false',workflow);self.assertIn('environment: private-build',workflow)
  self.assertIn('secrets.SABO_PRIVATE_REPO_TOKEN',workflow)
  self.assertNotIn('secrets.SABO_PRIVATE_REPO_TOKEN',workflow.split('name: Test and build')[1].split('- name: Upload')[0])
if __name__=='__main__':unittest.main()
