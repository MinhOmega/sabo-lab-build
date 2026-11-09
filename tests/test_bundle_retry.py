import base64, io, json, pathlib, sys, tempfile, types, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import signed_release as signed
NSIS_504=b'Downloading https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.13/nsis-3.13.zip\nfailed to bundle project: http status: 504\n'

class BundleRetryTests(unittest.TestCase):
 def run_stage(self,outputs,codes=None,prefix=b'',failure=None):
  calls=[];codes=codes or [1]*(len(outputs)-1)+[0]
  def execute(args,cwd,env,stdout,stderr,timeout):
   n=len(calls);calls.append(args);stdout.write(outputs[n])
   if failure:raise failure
   return types.SimpleNamespace(returncode=codes[n])
  with tempfile.TemporaryDirectory() as d:
   path=pathlib.Path(d)/'build-detail.txt'
   with path.open('wb') as log,mock.patch.object(signed.build_runtime,'execute',side_effect=execute),mock.patch.object(signed.time,'sleep') as sleep,mock.patch('builtins.print') as out:
    log.write(prefix)
    stage=signed.run_bundle_stage(['npm','run','tauri','build'],pathlib.Path(d),{},log,path,14)
   return stage,calls,[c.args[0] for c in sleep.call_args_list],str(out.call_args_list),path.read_bytes() if path.exists() else b''
 def test_transient_5xx_reruns_only_bundle_stage_then_succeeds(self):
  stage,calls,sleeps,out,log=self.run_stage([NSIS_504,b'bundled\n'])
  self.assertTrue(stage['success']);self.assertEqual(len(calls),2);self.assertEqual(sleeps,[30])
  self.assertEqual(stage['transientRetries'][0]['attempt'],1);self.assertIn(b'RETRY 1',log)
  self.assertNotIn('github.com',out);self.assertNotIn('nsis',out.lower())
 def test_persistent_5xx_is_bounded_to_two_retries_and_fails(self):
  stage,calls,sleeps,_,_=self.run_stage([NSIS_504]*3,codes=[1,1,1])
  self.assertFalse(stage['success']);self.assertEqual(len(calls),3);self.assertEqual(sleeps,[30,90]);self.assertEqual(len(stage['transientRetries']),2)
 def test_non_transient_failures_never_retry(self):
  for output in [b'error[E0425]: cannot find value\n',b'failed to bundle project: http status: 404\n',b'signing failed\n']:
   stage,calls,sleeps,_,_=self.run_stage([output],codes=[1])
   self.assertFalse(stage['success']);self.assertEqual(len(calls),1);self.assertEqual(sleeps,[]);self.assertNotIn('transientRetries',stage)
 def test_timeout_is_not_treated_as_transient_download(self):
  stage,calls,sleeps,_,_=self.run_stage([NSIS_504],failure=signed.build_runtime.subprocess.TimeoutExpired('tauri',1))
  self.assertEqual(stage['failure'],'timeout');self.assertEqual(len(calls),1);self.assertEqual(sleeps,[])
 def test_only_the_current_attempt_output_is_inspected(self):
  stage,calls,sleeps,_,_=self.run_stage([b'compile error\n'],codes=[1],prefix=NSIS_504)
  self.assertEqual(len(calls),1);self.assertEqual(sleeps,[])
 def test_success_has_no_retry_metadata(self):
  stage,calls,sleeps,_,_=self.run_stage([b'ok\n'],codes=[0])
  self.assertTrue(stage['success']);self.assertEqual(len(calls),1);self.assertNotIn('transientRetries',stage)

@unittest.skipUnless(signed.os.name=='nt','Windows NSIS bundle path runs on the Windows runner')
class WindowsBuildRetryTests(unittest.TestCase):
 def test_earlier_stages_run_once_and_nsis_bundle_retries(self):
  calls=[]
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);desktop=root/'source/apps/desktop';desktop.mkdir(parents=True)
   (root/'request.json').write_text(json.dumps({'sourceCommit':'a'*40,'repository':'MinhOmega/sabo-lab-next'}));(desktop/'package.json').write_text('{"version":"0.7.12"}')
   nsis=root/'target/x86_64-pc-windows-msvc/release/bundle/nsis'
   def execute(args,cwd,env,stdout,stderr,timeout):
    calls.append(args);bundle='tauri' in args
    if bundle and sum('tauri' in c for c in calls)==1:stdout.write(NSIS_504);return types.SimpleNamespace(returncode=1)
    if bundle:
     nsis.mkdir(parents=True,exist_ok=True);(nsis/'Sabo Lab_0.7.12_x64-setup.exe').write_bytes(b'exe')
     (nsis/'Sabo Lab_0.7.12_x64-setup.exe.sig').write_text(base64.b64encode(b'untrusted comment: x\nsig\ntrusted comment: timestamp:1 file:x version:0.7.12\nsig\n').decode())
    return types.SimpleNamespace(returncode=0)
   with mock.patch.object(signed.build_runtime,'execute',side_effect=execute),mock.patch.object(signed.time,'sleep'),mock.patch.dict(signed.os.environ,{'TAURI_SIGNING_PRIVATE_KEY':'fixture-updater-key'}):
    signed.run_build(root,'windows')
   status=json.loads((root/'private-output/build-info.json').read_text())
  self.assertEqual(status['status'],'success');self.assertEqual(sum('tauri' in c for c in calls),2)
  self.assertEqual(len(calls),len(signed.STAGE_NAMES)+1);self.assertEqual(len(status['stages'][-1]['transientRetries']),1)
if __name__=='__main__':unittest.main()
