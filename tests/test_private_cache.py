import hashlib
import contextlib
import io
import json
import os
import pathlib
import sys
import tarfile
import tempfile
import types
import unittest
import urllib.error
from unittest import mock
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'scripts'))
import private_cache as cache
SHA='a'*40

def config():return {'schema':1,'key':'b'*64,'platform':'windows','target':'x86_64-pc-windows-msvc','workspaceNames':['desktop','desktop_lib','sabo-core','sabo_core','sabo-doctor','sabo_doctor','fake-agent','fake_agent'],'tag':'ci-deps-cache-v1-windows-'+('b'*32)}
def write(p,data=b'fixture'):
 p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p
class CacheTests(unittest.TestCase):
 def test_local_workspace_and_automatic_target_names_are_detected(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);write(root/'crates/a/Cargo.toml',b'[package]\nname="sabo-core"\nversion="0.1.0"\n[lib]\nname="private_alias"\n');write(root/'crates/a/src/bin/sabo-doctor.rs');write(root/'crates/a/tests/fake-agent.rs')
   names=cache.workspace_names(root)
   for name in ('sabo-core','sabo_core','private_alias','sabo-doctor','sabo_doctor','fake-agent','fake_agent'):self.assertIn(name,names)
 def test_workspace_outputs_source_signing_bundles_and_windows_path_escapes_are_forbidden(self):
  c=config()
  for name in ['source/apps/main.rs','credentials.toml','target/release/app.exe','target/release/bundle/nsis/app.exe','target/debug/deps/libdesktop_lib-ab.rlib','target/debug/deps/libDESKTOP_LIB-ab.rlib','target/debug/deps/sabo_doctor-ab.exe','target/debug/.fingerprint/sabo-core-ab/lib-a','target/debug/build/desktop-ab/out/key','target/debug/deps/../escape','target/debug/deps/.. /escape','target/debug/deps/CON.txt','target\\debug\\deps\\file','target/debug/deps/a:stream','target//debug/deps/a','target/./debug/deps/a','registry/credentials/token','registry/src/x/.git/config','target/aarch64-apple-darwin/release/deps/libserde-ab.rlib']:
   self.assertFalse(cache.allowed(name,c),name)
  for name in ['target/debug/deps/libserde-ab.rlib','target/x86_64-pc-windows-msvc/release/deps/libtauri-ab.rlib','target/debug/.fingerprint/serde-ab/lib-serde','target/debug/build/libsqlite3-sys-ab/out/sqlite.lib','registry/cache/index/serde.crate','registry/src/index/serde/src/lib.rs']:
   self.assertTrue(cache.allowed(name,c),name)
 def test_dependency_key_survives_application_version_bump_but_tracks_dependencies_and_toolchain(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);p=root/'source/apps/desktop/Cargo.toml';write(p,b'[package]\nname="desktop"\nversion="0.3.19"\n[dependencies]\nserde="1"\n')
   lock=root/'source/apps/desktop/Cargo.lock';write(lock,b'version=4\n[[package]]\nname="desktop"\nversion="0.3.19"\n[[package]]\nname="serde"\nversion="1.0.0"\nsource="registry+https://github.com/rust-lang/crates.io-index"\nchecksum="abc"\n')
   with mock.patch.object(cache.subprocess,'run',return_value=types.SimpleNamespace(stdout='rustc fixture')):
    old=cache.plan(root,'windows');p.write_text(p.read_text().replace('0.3.19','0.3.20'));lock.write_text(lock.read_text().replace('0.3.19','0.3.20'));new=cache.plan(root,'windows');self.assertEqual(old['key'],new['key'])
    lock.write_text(lock.read_text().replace('checksum="abc"','checksum="changed"'));self.assertNotEqual(old['key'],cache.plan(root,'windows')['key'])
   with mock.patch.object(cache.subprocess,'run',return_value=types.SimpleNamespace(stdout='different rustc')):self.assertNotEqual(new['key'],cache.plan(root,'windows')['key'])
 def test_archive_omits_local_crates_even_when_commit_mtime_predates_old_build(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);public=write(root/'target/debug/deps/libserde-ab.rlib',b'public dependency');private=write(root/'target/debug/deps/libdesktop_lib-ab.rlib',b'old private app')
   os.utime(private,(1,1));os.utime(public,(2,2));write(root/'target/debug/.fingerprint/desktop-ab/lib-desktop',b'old workspace');write(root/'source/main.rs',b'new app');write(root/'target/release/bundle/private.exe',b'signed app')
   archive=root/'cache.tar.gz';count,total=cache.pack(root,config(),archive);self.assertEqual(count,1);self.assertEqual(total,len(b'public dependency'))
   cache.extract(archive,root/'expanded',config());self.assertEqual((root/'expanded/target/debug/deps/libserde-ab.rlib').read_bytes(),b'public dependency');self.assertFalse((root/'expanded/source').exists());self.assertFalse((root/'expanded/target/debug/deps/libdesktop_lib-ab.rlib').exists())
 def test_symlinks_traversal_duplicates_private_crates_and_oversize_entries_are_rejected(self):
  for name,kind in [('target/debug/deps/../outside',tarfile.REGTYPE),('target/debug/deps/link',tarfile.SYMTYPE),('target/debug/deps/link',tarfile.LNKTYPE),('target/debug/deps/libdesktop_lib-ab.rlib',tarfile.REGTYPE)]:
   with self.subTest(name=name,kind=kind),tempfile.TemporaryDirectory() as d:
    root=pathlib.Path(d);archive=root/'cache.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
     item=tarfile.TarInfo(name);item.type=kind;item.linkname='/outside';item.size=1 if kind==tarfile.REGTYPE else 0;t.addfile(item,io.BytesIO(b'x') if item.size else None)
    with self.assertRaises(cache.builder.SafeError):cache.extract(archive,root/'expanded',config())
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);archive=root/'cache.tar.gz'
   with tarfile.open(archive,'w:gz') as t:
    for _ in range(2):
     item=tarfile.TarInfo('target/debug/deps/libserde-ab.rlib');item.size=1;t.addfile(item,io.BytesIO(b'x'))
   with self.assertRaises(cache.builder.SafeError):cache.extract(archive,root/'expanded',config())
 def test_archive_byte_limits_stop_pack_before_an_unbounded_upload(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);write(root/'target/debug/deps/libserde-ab.rlib',b'x'*100)
   with mock.patch.object(cache,'MAX_EXPANDED',10):
    with self.assertRaises(cache.builder.SafeError):cache.pack(root,config(),root/'deps.tar.gz')
 def test_download_never_forwards_pat_to_signed_asset_url(self):
  payload=b'public fixture bytes';asset={'id':7,'size':len(payload),'digest':'sha256:'+hashlib.sha256(payload).hexdigest()};calls=[]
  def open(req,**kw):
   calls.append(req)
   if len(calls)==1:raise urllib.error.HTTPError(req.full_url,302,'redirect',{'Location':'https://release-assets.githubusercontent.com/signed?opaque=fixture'},None)
   return io.BytesIO(payload)
  with tempfile.TemporaryDirectory() as d,mock.patch.object(cache.builder,'token',return_value='fixture-only-pat'),mock.patch.object(cache.urllib.request,'build_opener',return_value=types.SimpleNamespace(open=open)):
   digest=cache.download_asset(asset,pathlib.Path(d)/'asset',128)
  self.assertEqual(digest,hashlib.sha256(payload).hexdigest());self.assertIn('Authorization',calls[0].headers);self.assertNotIn('Authorization',calls[1].headers)
 def test_redirect_credentials_external_host_and_truncated_payload_are_rejected(self):
  for url in ['http://release-assets.githubusercontent.com/a','https://evil.invalid/a','https://user:pass@release-assets.githubusercontent.com/a','https://release-assets.githubusercontent.com:443/a']:
   with self.subTest(url=url),tempfile.TemporaryDirectory() as d,mock.patch.object(cache.builder,'token',return_value='fixture'),mock.patch.object(cache.urllib.request,'build_opener',return_value=types.SimpleNamespace(open=mock.Mock(side_effect=urllib.error.HTTPError('api',302,'redirect',{'Location':url},None)))):
    with self.assertRaises(cache.builder.SafeError):cache.download_asset({'id':7,'size':1},pathlib.Path(d)/'asset',128)
  with tempfile.TemporaryDirectory() as d,mock.patch.object(cache.builder,'token',return_value='fixture'),mock.patch.object(cache.urllib.request,'build_opener',return_value=types.SimpleNamespace(open=lambda *a,**k:io.BytesIO(b'x'))):
   with self.assertRaises(cache.builder.SafeError):cache.download_asset({'id':7,'size':3},pathlib.Path(d)/'asset',128)
 def test_failed_build_never_creates_or_uploads_cache(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);write(root/'private-output/build-info.json',json.dumps({'status':'failed','sourceCommit':SHA}).encode());write(root/'request.json',json.dumps({'sourceCommit':SHA}).encode())
   with mock.patch.object(cache.builder,'ensure_private') as check,mock.patch.object(cache.builder,'upload_asset') as upload:
    with self.assertRaises(cache.builder.SafeError):cache.save(root,config())
    check.assert_not_called();upload.assert_not_called()
 def test_cache_is_private_immutable_and_completion_marker_is_uploaded_last(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);write(root/'private-output/build-info.json',json.dumps({'status':'success','sourceCommit':SHA}).encode());write(root/'request.json',json.dumps({'sourceCommit':SHA}).encode());write(root/'target/debug/deps/libserde-ab.rlib')
   with mock.patch.object(cache.builder,'ensure_private'),mock.patch.object(cache.builder,'release_for',return_value=None),mock.patch.object(cache.builder,'api',return_value={'id':1}) as api,mock.patch.object(cache.builder,'upload_asset') as upload,mock.patch.object(cache,'prune'):
    self.assertEqual(cache.save(root,config()),'saved');self.assertEqual(upload.call_args_list[-1].args[1].name,'cache-manifest.json');self.assertTrue(api.call_args.kwargs['data']['draft'])
   with mock.patch.object(cache.builder,'ensure_private'),mock.patch.object(cache.builder,'release_for',return_value={'draft':True,'name':cache.MARKER,'assets':[{'name':'cache-manifest.json'}]}),mock.patch.object(cache,'pack') as pack:
    self.assertEqual(cache.save(root,config()),'already-saved');pack.assert_not_called()
 def test_manifest_corruption_never_changes_target_or_source(self):
  c=config();meta={'schema':1,'key':'wrong','workspaceNames':c['workspaceNames'],'sourceCommit':SHA,'parts':[]}
  release={'draft':True,'name':cache.MARKER,'assets':[{'name':'cache-manifest.json','id':1,'size':12}]}
  def download(asset,dest,limit):dest.write_text(json.dumps(meta));return 'unused'
  with tempfile.TemporaryDirectory() as d,mock.patch.object(cache.builder,'ensure_private'),mock.patch.object(cache.builder,'release_for',return_value=release),mock.patch.object(cache,'download_asset',side_effect=download):
   root=pathlib.Path(d);write(root/'source/main.rs',b'current source')
   with self.assertRaises(cache.builder.SafeError):cache.restore(root,c)
   self.assertFalse((root/'target').exists());self.assertEqual((root/'source/main.rs').read_bytes(),b'current source')
 def test_real_pack_private_save_and_restore_round_trip_without_source_or_app_artifacts(self):
  with tempfile.TemporaryDirectory() as d:
   base=pathlib.Path(d);root=base/'build';root.mkdir();c=config();assets={};release={'id':1,'draft':True,'name':cache.MARKER,'assets':[]}
   write(root/'private-output/build-info.json',json.dumps({'status':'success','sourceCommit':SHA}).encode());write(root/'request.json',json.dumps({'sourceCommit':SHA}).encode())
   write(root/'target/debug/deps/libserde-ab.rlib',b'compiled public dependency');write(root/'target/debug/deps/libdesktop_lib-ab.rlib',b'NEVER RESTORE OLD APPLICATION');write(root/'cargo-home/registry/src/index/serde/src/lib.rs',b'public crate source')
   def upload(_id,path):
    data=path.read_bytes();assets[path.name]=data;release['assets'].append({'name':path.name,'id':len(assets),'size':len(data)})
   with mock.patch.object(cache.builder,'ensure_private'),mock.patch.object(cache.builder,'release_for',return_value=None),mock.patch.object(cache.builder,'api',return_value=release),mock.patch.object(cache.builder,'upload_asset',side_effect=upload),mock.patch.object(cache,'prune'):
    self.assertEqual(cache.save(root,c),'saved')
   restored=base/'next-build';restored.mkdir();write(restored/'source/main.rs',b'NEW CURRENT APPLICATION')
   def download(asset,path,limit):
    data=assets[asset['name']];self.assertLessEqual(len(data),limit);path.write_bytes(data);return hashlib.sha256(data).hexdigest()
   with mock.patch.object(cache.builder,'ensure_private'),mock.patch.object(cache.builder,'release_for',return_value=release),mock.patch.object(cache,'download_asset',side_effect=download):self.assertEqual(cache.restore(restored,c),'hit')
   self.assertEqual((restored/'target/debug/deps/libserde-ab.rlib').read_bytes(),b'compiled public dependency');self.assertFalse((restored/'target/debug/deps/libdesktop_lib-ab.rlib').exists());self.assertEqual((restored/'source/main.rs').read_bytes(),b'NEW CURRENT APPLICATION');self.assertEqual((restored/'cargo-home/registry/src/index/serde/src/lib.rs').read_bytes(),b'public crate source')
 def test_cache_failure_diagnostics_never_echo_signed_urls_or_credentials(self):
  with tempfile.TemporaryDirectory() as d,mock.patch.object(cache.sys,'argv',['private_cache.py','restore','--root',d,'--platform','windows']),mock.patch.object(cache,'plan',side_effect=cache.builder.SafeError('signed-url?token=fixture-private-value')):
   output=io.StringIO()
   with contextlib.redirect_stdout(output):self.assertEqual(cache.main(),0)
   text=(pathlib.Path(d)/'cache-restore.json').read_text();self.assertNotIn('fixture-private-value',text+output.getvalue());self.assertEqual(json.loads(text)['error']['code'],'CACHE_SERVICE_UNAVAILABLE')
 def test_pruning_never_deletes_diagnostics_public_releases_or_other_platform(self):
  c=config();rows=[{'id':n,'name':cache.MARKER,'tag_name':'ci-deps-cache-v1-windows-'+str(n),'draft':True,'created_at':str(n)} for n in range(1,5)]
  rows += [{'id':20,'name':cache.MARKER,'tag_name':'signed-windows-ci-example','draft':True},{'id':21,'name':cache.MARKER,'tag_name':'ci-deps-cache-v1-macos-example','draft':True},{'id':22,'name':cache.MARKER,'tag_name':'ci-deps-cache-v1-windows-public','draft':False}]
  with mock.patch.object(cache.builder,'api',side_effect=lambda p,**k:rows if not k else {}) as api:cache.prune(c)
  deletes=[int(call.args[0].rsplit('/',1)[1]) for call in api.call_args_list if call.kwargs.get('method')=='DELETE'];self.assertEqual(sorted(deletes),[1,2])
