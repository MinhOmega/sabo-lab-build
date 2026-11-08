import base64, contextlib, io, json, pathlib, sys, tempfile, types, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import macos_signing as m
import signed_release as signed
import build_runtime
ID='A6F5EDA9D867A1F52450800B769815D9D4B1B553'
P12=b'fixture-p12-bytes';PASSWORD='fixture-p12-password'
ENV={'MACOS_SIGNING_P12_BASE64':base64.b64encode(P12).decode(),'MACOS_SIGNING_P12_PASSWORD':PASSWORD}
DR='identifier "dev.minhvo.sabolab" and certificate leaf = H"%s"'%ID.lower()
DETAILS='Executable=/x\nIdentifier=dev.minhvo.sabolab\nCDHash=%s\nAuthority=Sabo Lab Release Signing\nTeamIdentifier=not set\n'%('2B'*20)

class FakeSecurity:
 def __init__(self,fail=None):self.calls=[];self.fail=fail;self.p12=None
 def __call__(self,args,**kwargs):
  self.calls.append(list(args))
  if 'import' in args:self.p12=pathlib.Path(args[2]).read_bytes()
  out=b''
  if args[1:3]==['list-keychains','-d'] and '-s' not in args:out=b'    "/Users/runner/Library/Keychains/login.keychain-db"\n'
  if 'find-identity' in args:out=('  1) %s "Sabo Lab Release Signing" (CSSMERR_TP_NOT_TRUSTED)\n     1 identities found\n'%ID).encode()
  return types.SimpleNamespace(returncode=1 if self.fail and self.fail in args else 0,stdout=out)

class KeychainTests(unittest.TestCase):
 def enter(self,fake,body=None):
  log=io.BytesIO()
  with mock.patch.object(m.subprocess,'run',side_effect=fake):
   with m.keychain(log,ENV) as identity:
    if body:body()
    return identity,log
 def test_temporary_keychain_is_created_used_and_always_removed(self):
  fake=FakeSecurity();identity,log=self.enter(fake)
  self.assertEqual(identity,ID);verbs=[c[1] for c in fake.calls]
  self.assertEqual(verbs,['list-keychains','create-keychain','set-keychain-settings','unlock-keychain','import','set-key-partition-list','list-keychains','find-identity','list-keychains','delete-keychain'])
  self.assertEqual(fake.p12,P12)
  path=fake.calls[1][-1];self.assertTrue(path.endswith('.keychain-db'))
  self.assertIn('-T',fake.calls[4]);self.assertEqual(fake.calls[4][fake.calls[4].index('-T')+1],'/usr/bin/codesign')
  self.assertEqual(fake.calls[5][2:4],['-S','apple-tool:,apple:,codesign:'])
  self.assertEqual(fake.calls[6][5:],[path,'/Users/runner/Library/Keychains/login.keychain-db'])
  self.assertEqual(fake.calls[8][5:],['/Users/runner/Library/Keychains/login.keychain-db'])
  self.assertEqual(fake.calls[9][-1],path);self.assertFalse(pathlib.Path(path).parent.exists())
  self.assertNotEqual(fake.calls[1][3],PASSWORD)
 def test_secrets_never_reach_private_log_or_errors(self):
  fake=FakeSecurity();_,log=self.enter(fake)
  for secret in (PASSWORD,ENV['MACOS_SIGNING_P12_BASE64'],fake.calls[1][3]):self.assertNotIn(secret.encode(),log.getvalue())
  with mock.patch.object(m.subprocess,'run',side_effect=FakeSecurity(fail='import')):
   with self.assertRaises(m.builder.SafeError) as error:
    with m.keychain(io.BytesIO(),ENV):pass
  self.assertNotIn(PASSWORD,str(error.exception))
 def test_failed_setup_or_build_still_restores_search_list_and_deletes_keychain(self):
  fake=FakeSecurity(fail='set-key-partition-list')
  with self.assertRaises(m.builder.SafeError):self.enter(fake)
  self.assertEqual([c[1] for c in fake.calls[-2:]],['list-keychains','delete-keychain'])
  fake=FakeSecurity()
  with self.assertRaises(RuntimeError):self.enter(fake,lambda:(_ for _ in ()).throw(RuntimeError('build failed')))
  self.assertEqual([c[1] for c in fake.calls[-2:]],['list-keychains','delete-keychain'])
 def test_missing_or_corrupt_identity_runs_nothing(self):
  for env in ({},{'MACOS_SIGNING_P12_BASE64':ENV['MACOS_SIGNING_P12_BASE64']},{**ENV,'MACOS_SIGNING_P12_BASE64':'%%%'}):
   with mock.patch.object(m.subprocess,'run') as run:
    with self.assertRaises(m.builder.SafeError):
     with m.keychain(io.BytesIO(),env):pass
    run.assert_not_called()
 def test_identity_must_be_unique_release_certificate(self):
  self.assertEqual(m.find_identity('  1) %s "Sabo Lab Release Signing"\n'%ID),ID)
  for out in ['','  1) %s "Other"\n'%ID,'  1) %s "Sabo Lab Release Signing"\n  2) %s "Sabo Lab Release Signing"\n'%(ID,'B'*40)]:
   with self.assertRaises(m.builder.SafeError):m.find_identity(out)

class SignatureTests(unittest.TestCase):
 def test_exact_certificate_pin_passes(self):
  info=m.parse_signature(DETAILS,'Executable=/x\ndesignated => '+DR+'\n',ID)
  self.assertEqual(info['authority'],'Sabo Lab Release Signing');self.assertNotIn('cdhash',info['designatedRequirement'])
 def test_adhoc_wrong_signer_or_build_specific_requirement_fails(self):
  cases=[(DETAILS+'Signature=adhoc\n',DR),(DETAILS.replace('Sabo Lab Release Signing','Other'),DR),(DETAILS.replace('dev.minhvo.sabolab','other.app'),DR),
         (DETAILS,'cdhash H"%s"'%('2B'*20)),(DETAILS,DR+' or cdhash H"%s"'%('2B'*20)),(DETAILS,DR+' or true'),(DETAILS,DR.replace(ID.lower(),'99'*20)),(DETAILS,'identifier "dev.minhvo.sabolab"'),(DETAILS,'')]
  for details,dr in cases:
   with self.assertRaises(m.builder.SafeError):m.parse_signature(details,'designated => '+dr+'\n' if dr else '',ID)
 def test_verify_uses_read_only_codesign_commands(self):
  calls=[]
  def run(args,log,check=True,timeout=120):
   calls.append(args);return DETAILS if '--verbose=4' in args else 'designated => '+DR if '-r-' in args else ''
  with mock.patch.object(m,'run',side_effect=run):m.verify(pathlib.Path('/x/Sabo Lab.app'),ID,io.BytesIO())
  self.assertTrue(all(c[0]=='/usr/bin/codesign' and '--force' not in c and '-s' not in c for c in calls))
  self.assertIn(['/usr/bin/codesign','--verify','--deep','--strict','/x/Sabo Lab.app'],calls)

@unittest.skipIf(signed.os.name=='nt','macOS build path is exercised on POSIX runners')
class SignedBuildTests(unittest.TestCase):
 def build(self,keychain_error=False,verify_error=False):
  envs=[]
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);desktop=root/'source/apps/desktop';desktop.mkdir(parents=True)
   (root/'request.json').write_text(json.dumps({'sourceCommit':'a'*40,'repository':'MinhOmega/sabo-lab-next'}));(desktop/'package.json').write_text('{"version":"0.7.8"}')
   bundle=root/'target/aarch64-apple-darwin/release/bundle/macos';(bundle/'Sabo Lab.app').mkdir(parents=True)
   (bundle/'Sabo Lab.app.tar.gz').write_bytes(b'app')
   (bundle/'Sabo Lab.app.tar.gz.sig').write_text(base64.b64encode(b'untrusted comment: x\nsig\ntrusted comment: timestamp:1 file:x version:0.7.8\nsig\n').decode())
   def run(args,**kwargs):envs.append((args,kwargs['env']));return types.SimpleNamespace(returncode=0)
   @contextlib.contextmanager
   def keychain(log):
    if keychain_error:raise m.builder.SafeError('fixture')
    yield ID
   def verify(app,identity,log):
    self.assertEqual((app.name,identity),('Sabo Lab.app',ID))
    if verify_error:raise m.builder.SafeError('fixture')
    return {'designatedRequirement':DR}
   out=io.StringIO()
   with mock.patch.object(signed.build_runtime,'execute',side_effect=run),mock.patch.object(signed.macos_signing,'keychain',side_effect=keychain),mock.patch.object(signed.macos_signing,'verify',side_effect=verify),mock.patch.dict(signed.os.environ,{'TAURI_SIGNING_PRIVATE_KEY':'fixture-updater-key',**ENV}),mock.patch('sys.stdout',out):
    try:signed.run_build(root,'macos');error=None
    except signed.builder.SafeError as e:error=e
   return envs,json.loads((root/'private-output/build-info.json').read_text()),error,out.getvalue()
 def test_only_bundle_stage_gets_stable_identity_and_signature_is_verified(self):
  envs,status,error,out=self.build()
  self.assertIsNone(error);self.assertEqual(status['status'],'success');self.assertEqual(status['macosSignature'],{'designatedRequirement':DR})
  self.assertEqual(envs[-1][1]['APPLE_SIGNING_IDENTITY'],ID);self.assertIn('tauri',envs[-1][0])
  self.assertTrue(all('APPLE_SIGNING_IDENTITY' not in e for _,e in envs[:-1]))
  for _,env in envs:
   self.assertFalse(any(k.startswith('MACOS_SIGNING') or 'APPLE_CERTIFICATE' in k for k in env))
  self.assertIn('macOS stable signature check: pass.',out);self.assertNotIn(ID,out);self.assertNotIn(PASSWORD,out)
 def test_keychain_failure_never_builds_unpinned_bundle(self):
  envs,status,error,out=self.build(keychain_error=True)
  self.assertIsNotNone(error);self.assertEqual(status['failedStage'],'macos-signing-setup');self.assertFalse(any('tauri' in a for a,_ in envs))
 def test_unpinned_signature_blocks_upload(self):
  envs,status,error,out=self.build(verify_error=True)
  self.assertIsNotNone(error);self.assertEqual(status['failedStage'],'macos-signature');self.assertEqual(status['status'],'failed');self.assertNotIn('installer',status)
  self.assertIn('macOS stable signature check: fail.',out)

class WorkflowTests(unittest.TestCase):
 def test_signing_identity_only_reaches_macos_signing_steps(self):
  for name,command in [('signed-desktop.yml','signed_release.py build'),('verify-delivery.yml','--mode full')]:
   steps=(ROOT/'.github/workflows'/name).read_text().split('\n      - ')
   holders=[s for s in steps if 'MACOS_SIGNING' in s]
   self.assertEqual(len(holders),1);self.assertIn(command,holders[0])
   for key in ('MACOS_SIGNING_P12_BASE64','MACOS_SIGNING_P12_PASSWORD'):
    self.assertIn("%s: ${{ matrix.platform == 'macos' && secrets.%s || '' }}"%(key,key),holders[0])
  quick=[s for s in (ROOT/'.github/workflows/verify-delivery.yml').read_text().split('\n      - ') if '--mode quick' in s][0]
  self.assertNotIn('MACOS_SIGNING',quick)
 def test_child_stage_environment_strips_signing_identity(self):
  with mock.patch.dict(build_runtime.os.environ,{**ENV,'APPLE_CERTIFICATE':'x','APPLE_CERTIFICATE_PASSWORD':'y'}):
   env=build_runtime.environment(pathlib.Path('/tmp/r'),'macos')
  self.assertFalse(any(k.startswith('MACOS_SIGNING') or k.startswith('APPLE_CERTIFICATE') for k in env))
if __name__=='__main__':unittest.main()
