import sys,pathlib,unittest
from unittest import mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
import signed_release as signed
class ArchiveRootTests(unittest.TestCase):
 def fixture(self,existing=True,tree=None,parents=None,wrong_ref=False):
  sha='a'*40;calls=[];created=[]
  def api(path,method='GET',data=None,allow_missing=False):
   calls.append((path,method,data))
   if path.endswith('ref/heads/release-assets'):return {'object':{'type':'commit','sha':sha}}
   if path.endswith('commits/'+sha):return {'tree':{'sha':signed.EMPTY_RELEASE_TREE if tree is None else tree},'parents':[] if parents is None else parents}
   if path.endswith('ref/tags/app-v0.5.2'):
    if not existing and not created:return None
    return {'object':{'type':'commit','sha':'b'*40 if wrong_ref else sha}}
   if path.endswith('refs') and method=='POST':created.append(data);return {}
   self.fail('Unexpected request '+path)
  return api,calls,created
 def test_new_tag_points_only_to_parentless_empty_commit(self):
  api,calls,created=self.fixture(existing=False)
  with mock.patch.object(signed,'public_api',side_effect=api):self.assertEqual(signed.ensure_empty_release_tag('app-v0.5.2'),'a'*40)
  self.assertEqual(created,[{'ref':'refs/tags/app-v0.5.2','sha':'a'*40}]);self.assertFalse(any(method in ['PATCH','DELETE'] for _,method,_ in calls))
 def test_existing_empty_tag_is_verified_without_rewriting(self):
  api,calls,created=self.fixture()
  with mock.patch.object(signed,'public_api',side_effect=api):signed.ensure_empty_release_tag('app-v0.5.2')
  self.assertEqual(created,[]);self.assertTrue(all(method=='GET' for _,method,_ in calls))
 def test_code_tree_parent_history_or_unreviewed_existing_tag_blocks_release(self):
  for kwargs in [{'tree':'b'*40},{'parents':[{'sha':'b'*40}]},{'wrong_ref':True}]:
   api,calls,created=self.fixture(**kwargs)
   with mock.patch.object(signed,'public_api',side_effect=api):
    with self.assertRaises(signed.builder.SafeError):signed.ensure_empty_release_tag('app-v0.5.2')
   self.assertEqual(created,[]);self.assertTrue(all(method=='GET' for _,method,_ in calls))
 def test_invalid_tag_is_rejected_before_network(self):
  with mock.patch.object(signed,'public_api') as api:
   for tag in ['main','app-v0.5.2/other','app-v0.5.2-beta']:
    with self.assertRaises(signed.builder.SafeError):signed.ensure_empty_release_tag(tag)
   api.assert_not_called()
 def test_prepare_checks_archive_root_before_creating_draft(self):
  with mock.patch.object(signed,'find_release',return_value=None),mock.patch.object(signed,'ensure_empty_release_tag',return_value='a'*40) as check,mock.patch.object(signed,'public_api') as api:
   signed.prepare('app-v0.5.2');check.assert_called_once_with('app-v0.5.2');self.assertEqual(api.call_args.kwargs['data']['target_commitish'],'a'*40)
