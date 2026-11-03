import io
import os
import pathlib
import sys
import tempfile
import types
import time
import unittest
from unittest import mock
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'scripts'))
import build_runtime as runtime

class RuntimeTests(unittest.TestCase):
 def test_hosted_parallelism_is_bounded_and_all_credentials_are_removed(self):
  with mock.patch.object(runtime.os,'cpu_count',return_value=32),mock.patch.dict(os.environ,{'SABO_PRIVATE_REPO_TOKEN':'fixture','GH_TOKEN':'fixture','TAURI_SIGNING_PRIVATE_KEY':'fixture','SABO_FAKE_AGENT':'1','SABO_AGENT_SCRIPT':'bad'}):
   w=runtime.environment(pathlib.Path('/tmp/owned'),'windows','a'*40);m=runtime.environment(pathlib.Path('/tmp/owned'),'macos')
   self.assertEqual(w['CARGO_BUILD_JOBS'],'3');self.assertEqual(m['CARGO_BUILD_JOBS'],'2')
   for key in ('GH_TOKEN','SABO_PRIVATE_REPO_TOKEN','TAURI_SIGNING_PRIVATE_KEY','SABO_FAKE_AGENT','SABO_AGENT_SCRIPT'):self.assertNotIn(key,w)
   self.assertEqual(w['CARGO_PROFILE_RELEASE_LTO'],'thin');self.assertEqual(w['CARGO_PROFILE_RELEASE_CODEGEN_UNITS'],'16')
   self.assertEqual(w['SABO_BUILD_REVISION'],'a'*40)
 def test_stage_never_passes_aggregate_log_handle_to_compiler(self):
  log=io.BytesIO()
  def run(args,**kw):
   self.assertIsNot(kw['stdout'],log);kw['stdout'].write(b'compiler fixture output\n');kw['stdout'].flush()
   return types.SimpleNamespace(returncode=0)
  with mock.patch.object(runtime,'execute',side_effect=run):
   stage=runtime.run_stage(['fixture'],pathlib.Path('/tmp'),{},log,5,'compile')
  self.assertEqual(log.getvalue(),b'compiler fixture output\n');self.assertTrue(stage['success']);self.assertEqual(stage['name'],'compile');self.assertGreaterEqual(stage['durationSeconds'],0)
 def test_timeout_is_a_failed_timed_stage_without_exposing_exception_text(self):
  log=io.BytesIO()
  with mock.patch.object(runtime,'execute',side_effect=runtime.subprocess.TimeoutExpired(['private/path'],1)):
   stage=runtime.run_stage(['fixture'],pathlib.Path('/tmp'),{},log,1,'timeout')
  self.assertFalse(stage['success']);self.assertEqual(stage['failure'],'timeout');self.assertNotIn(b'private/path',log.getvalue())

 @unittest.skipUnless(os.name=='nt','Windows Job Object lifecycle')
 def test_owned_windows_job_removes_lingering_descendant_cwd_lock(self):
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);held=root/'compiler-cwd';held.mkdir();pid=root/'child.pid';log=io.BytesIO()
   program="import subprocess,sys,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],cwd=sys.argv[1]); pathlib.Path(sys.argv[2]).write_text(str(p.pid))"
   env=runtime.environment(root,'windows')
   result=runtime.run_stage([sys.executable,'-c',program,str(held),str(pid)],root,env,log,20,'owned-child')
   self.assertTrue(result['success']);self.assertTrue(pid.exists())
   deadline=time.monotonic()+5
   while True:
    try:held.rmdir();break
    except PermissionError:
     if time.monotonic()>=deadline:raise
     time.sleep(.05)
   self.assertFalse(held.exists())
