#!/usr/bin/env python3
"""Public orchestration only. Private build output never goes to Actions stdout."""
import argparse, datetime, hashlib, json, os, pathlib, re, shutil, subprocess, sys, tarfile, urllib.error, urllib.parse, urllib.request

import build_runtime

REPOSITORY = 'MinhOmega/sabo-lab-next'
API = 'https://api.github.com'
MAX_ARCHIVE = 512 * 1024**2
ALLOWED_PREFIXES = ('apps/', 'crates/', 'data/', 'scripts/', 'docs/', 'services/license/tests/')
RECOVERED = {
 'reference/csharp-recovered/AI.Auto.Resources.FridaNative.dll',
 'reference/csharp-recovered/AI.Auto.Resources.frida-server-16.1.4-android-x86',
 'reference/csharp-recovered/AI.Auto.Code.Frida.v2.FridaScript.js',
}
class SafeError(Exception): pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self, req, fp, code, msg, headers, newurl): return None

def token():
 value = os.environ.get('SABO_PRIVATE_REPO_TOKEN', '')
 if not value: raise SafeError('Missing SABO_PRIVATE_REPO_TOKEN. Configure the public repository Actions secret.')
 return value

def api(path, method='GET', data=None, content_type='application/json'):
 if path != '/repos/' + REPOSITORY and not path.startswith('/repos/' + REPOSITORY + '/'): raise SafeError('Unexpected API destination.')
 payload = json.dumps(data).encode() if data is not None and content_type == 'application/json' else data
 req = urllib.request.Request(API + path, data=payload, method=method, headers={
  'Authorization': 'Bearer ' + token(), 'Accept':'application/vnd.github+json',
  'X-GitHub-Api-Version':'2026-03-10','User-Agent':'sabo-private-builder', 'Content-Type':content_type})
 try:
  with urllib.request.urlopen(req, timeout=60) as response: return {} if response.status == 204 else json.load(response)
 except urllib.error.HTTPError as error:
  # Never print headers, body, redirect URL or credentials in a public log.
  raise SafeError('GitHub API request failed (HTTP %s); verify token scope/access.' % error.code) from None

def save_json(path, value):
 path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')

def ensure_private():
 repo = api('/repos/' + REPOSITORY)
 if repo.get('private') is not True: raise SafeError('Source/release repository must remain private.')

def select_commit(value):
 if value and not re.fullmatch('[0-9a-fA-F]{40}', value): raise SafeError('Enter a full 40-character commit SHA, never arbitrary refs or shell text.')
 result = api('/repos/' + REPOSITORY + '/commits/' + (value.lower() if value else 'main'))
 sha = result.get('sha', '')
 if not re.fullmatch('[0-9a-f]{40}', sha) or (value and sha != value.lower()): raise SafeError('Source commit resolution failed.')
 return sha

def release_for(tag):
 # Includes draft releases with Contents:write; pagination is bounded.
 for page in range(1, 11):
  rows = api('/repos/' + REPOSITORY + '/releases?per_page=100&page=' + str(page))
  for release in rows:
   if release.get('tag_name') == tag: return release
  if len(rows) < 100: return None
 raise SafeError('Too many releases to inspect safely; use a specific commit and archive old CI drafts.')

def valid_archive_path(name, root):
 parts = pathlib.PurePosixPath(name).parts
 if not parts or '\\' in name or any(p in ('..', '') or ':' in p for p in parts) or name.startswith('/') or parts[0] != root: raise SafeError('Unsafe archive entry.')
 return '/'.join(parts[1:])

def unpack(archive, target):
 if target.exists(): raise SafeError('Source destination already exists.')
 with tarfile.open(archive, 'r:gz') as tar:
  members = tar.getmembers()
  if not members or len(members)>50000: raise SafeError('Unexpected source archive size.')
  root = pathlib.PurePosixPath(members[0].name).parts[0]; total = 0
  target.mkdir(parents=True)
  for item in members:
   rel = valid_archive_path(item.name, root)
   selected = rel.startswith(ALLOWED_PREFIXES) or rel in RECOVERED or rel in ('README.md','.gitignore')
   if not selected or item.isdir(): continue
   if not item.isfile() or item.size<0: raise SafeError('Source archive contains a link or unsupported entry.')
   total += item.size
   if total>2*1024**3: raise SafeError('Source archive exceeds extraction budget.')
   dest=target.joinpath(*pathlib.PurePosixPath(rel).parts); dest.parent.mkdir(parents=True, exist_ok=True)
   with tar.extractfile(item) as source, dest.open('xb') as output: shutil.copyfileobj(source, output)


def download_source(sha, dest):
 # Request the private archive, then follow its signed codeload URL WITHOUT
 # forwarding the Authorization header across origins. URLs never reach stdout.
 request=urllib.request.Request(API+'/repos/'+REPOSITORY+'/tarball/'+sha,headers={
  'Authorization':'Bearer '+token(),'User-Agent':'sabo-private-builder','X-GitHub-Api-Version':'2026-03-10'})
 try:
  urllib.request.build_opener(NoRedirect()).open(request,timeout=60)
  raise SafeError('Expected an authenticated archive redirect.')
 except urllib.error.HTTPError as error:
  if error.code!=302: raise SafeError('Private source download failed (HTTP %s).' % error.code) from None
  location=error.headers.get('Location','')
 url=urllib.parse.urlsplit(location)
 if url.scheme!='https' or url.hostname!='codeload.github.com' or url.username or url.password or url.port or url.fragment: raise SafeError('Unexpected archive redirect host.')
 archive=dest.parent/'source.tar.gz'; size=0
 try:
  request=urllib.request.Request(location,headers={'User-Agent':'sabo-private-builder'})
  with urllib.request.build_opener(NoRedirect()).open(request,timeout=180) as response,archive.open('xb') as output:
   while chunk:=response.read(1024*1024):
    size+=len(chunk)
    if size>MAX_ARCHIVE: raise SafeError('Source archive exceeds download budget.')
    output.write(chunk)
  unpack(archive,dest)
 except urllib.error.HTTPError as error: raise SafeError('Source archive transfer failed (HTTP %s).' % error.code) from None
 finally: archive.unlink(missing_ok=True)

def output_flag(name,value):
 path=os.environ.get('GITHUB_OUTPUT')
 if path:
  with open(path,'a',encoding='utf-8') as file: file.write(name+'='+value+'\n')

def fetch(root, sha, scheduled):
 ensure_private(); commit=select_commit(sha); tag='windows-ci-'+commit[:12]
 release=release_for(tag)
 # Only our own completion marker prevents a repeated scheduled build.
 if scheduled and release and any(a.get('name')=='BUILD-SUCCEEDED.json' for a in release.get('assets',[])):
  output_flag('skip','true'); print('The latest source already has a successful private build.'); return
 download_source(commit,root/'source')
 save_json(root/'request.json',{'sourceCommit':commit,'repository':REPOSITORY,'tag':tag})
 output_flag('skip','false'); print('Private source prepared. No source, cache or artifact is published here.')

def commands(source,native_only=False):
 desktop=source/'apps/desktop'; npm='npm.cmd' if os.name=='nt' else 'npm'
 yield source,[sys.executable,'scripts/prepare-assets.py']
 if native_only:
  yield source,['cargo','test','--locked','--manifest-path','crates/sabo-bootstrap/Cargo.toml','--','--nocapture']
  return
 yield desktop,[npm,'ci','--no-audit','--no-fund','--ignore-scripts']
 yield desktop,[npm,'run','build']
 yield desktop,['node','--test']
 for crate in ['sabo-core','sabo-bootstrap','sabo-task-api','sabo-transport','sabo-navigation','sabo-routines','sabo-quests']:
  yield source,['cargo','test','--locked','--manifest-path',f'crates/{crate}/Cargo.toml']
 yield source,['cargo','test','--locked','--manifest-path','apps/desktop/src-tauri/Cargo.toml','--lib']
 yield desktop,[npm,'run','tauri','build','--','--target','x86_64-pc-windows-msvc','--bundles','nsis']

def build(root,native_only=False):
 if os.name!='nt': raise SafeError('The installer job must run on Windows x64.')
 request=json.loads((root/'request.json').read_text());out=root/'private-output';out.mkdir()
 status={'status':'failed',**request,'runner':'windows-2022','target':'x86_64-pc-windows-msvc','startedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 if native_only:status.update(tag='native-runtime-ci-'+request['sourceCommit'][:12],nativeCheckOnly=True)
 env=build_runtime.environment(root,'windows',request['sourceCommit'])
 status.update(buildPolicy=build_runtime.policy('windows'),stages=[])
 log=out/'build-detail.txt'; failed=None
 with log.open('wb') as details:
  for index,(cwd,args) in enumerate(commands(root/'source',native_only),1):
   print('Private validation/build stage %d is running.' % index,flush=True)
   details.write(('\nSTAGE %d: %s\n'%(index,' '.join(args))).encode());details.flush()
   stage=build_runtime.run_stage(args,cwd,env,details,600 if native_only else 5400,'stage-'+str(index))
   status['stages'].append(stage)
   if not stage['success']:failed=index;break
 if failed is None and native_only:status.update(status='success',gameplayVerified=False)
 elif failed is None:
  files=list((root/'target/x86_64-pc-windows-msvc/release/bundle/nsis').glob('*.exe'))
  if len(files)!=1: failed='installer-output'
  else:
   version=json.loads((root/'source/apps/desktop/package.json').read_text())['version']
   if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?',version): failed='version'
   else:
    exe=out/f'SaboLab-{version}-windows-x64-setup.exe';shutil.copyfile(files[0],exe)
    digest=hashlib.sha256(exe.read_bytes()).hexdigest();(out/'SHA256SUMS.txt').write_text(digest+'  '+exe.name+'\n',encoding='utf-8')
    status.update(status='success',version=version,installer=exe.name,sha256=digest,gameplayVerified=False,updaterSigned=False)
 status.update(failedStage=failed,finishedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
 save_json(out/'build-info.json',status)
 if status['status']=='success': save_json(out/'BUILD-SUCCEEDED.json',status)
 output_flag('success','true' if status['status']=='success' else 'false')
 if failed is not None: raise SafeError('Windows validation/build failed. Detailed output will be attached only to the private draft release.')
 print('Windows native check succeeded; no installer built.' if native_only else 'Windows validation and NSIS build succeeded. Gameplay remains unverified.')

def upload_asset(release_id,path):
 data=path.read_bytes()
 if len(data)>512*1024**2: raise SafeError('Release asset too large.')
 request=urllib.request.Request('https://uploads.github.com/repos/'+REPOSITORY+'/releases/'+str(release_id)+'/assets?name='+urllib.parse.quote(path.name,safe=''),data=data,method='POST',headers={
  'Authorization':'Bearer '+token(),'User-Agent':'sabo-private-builder','Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2026-03-10','Content-Type':'application/octet-stream'})
 try:
  with urllib.request.urlopen(request,timeout=300) as response: return json.load(response)
 except urllib.error.HTTPError as error: raise SafeError('Private asset upload failed (HTTP %s).' % error.code) from None

def publish(root, publish_now):
 ensure_private();out=root/'private-output'
 if not (out/'build-info.json').is_file(): raise SafeError('No completed build status is available to release.')
 status=json.loads((out/'build-info.json').read_text());tag=status['tag'];release=release_for(tag)
 if release and not release.get('draft'): raise SafeError('Refusing to overwrite an already published release.')
 body='Windows x64 CI build from commit '+status['sourceCommit']+'.\n\nStatus: '+status['status']+'.\nInstaller is a test build; Windows emulator/gameplay is not verified. Updater signature and Authenticode are not configured by this pipeline. Detailed build output is private.\n'
 if status.get('nativeCheckOnly'):body='Windows native runtime check only; no installer.\n\nStatus: '+status['status']+'. Gameplay remains unverified. Detailed output is private.\n'
 if release is None:
  release=api('/repos/'+REPOSITORY+'/releases',method='POST',data={'tag_name':tag,'target_commitish':status['sourceCommit'],'name':'Windows test build '+status['sourceCommit'][:12],'body':body,'draft':True,'prerelease':True})
 else: api('/repos/'+REPOSITORY+'/releases/'+str(release['id']),method='PATCH',data={'body':body})
 # Remove completion marker FIRST so partial/retried uploads cannot look successful.
 old={a['name']:a for a in release.get('assets',[])}
 for name in sorted(old,key=lambda value:value!='BUILD-SUCCEEDED.json'):
  owned=name in ('BUILD-SUCCEEDED.json','SHA256SUMS.txt','build-info.json','build-detail.txt') or bool(re.fullmatch(r'SaboLab-[0-9A-Za-z.+-]+-windows-x64-setup\.exe',name))
  if owned:
   api('/repos/'+REPOSITORY+'/releases/assets/'+str(old.pop(name)['id']),method='DELETE')
 names=['build-detail.txt','build-info.json','SHA256SUMS.txt',status.get('installer',''),'BUILD-SUCCEEDED.json']
 for name in names:
  if not name: continue
  path=out/name
  if not path.is_file(): continue
  if name in old: api('/repos/'+REPOSITORY+'/releases/assets/'+str(old[name]['id']),method='DELETE')
  upload_asset(release['id'],path)
 if publish_now and status['status']=='success':
  api('/repos/'+REPOSITORY+'/releases/'+str(release['id']),method='PATCH',data={'draft':False,'prerelease':True,'make_latest':'false'})
 print('Build output uploaded to the PRIVATE source repository release. No installer or detailed log was published here.')


def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['fetch','build','publish']);p.add_argument('--root',type=pathlib.Path,required=True);p.add_argument('--sha',default='');p.add_argument('--scheduled',action='store_true');p.add_argument('--publish',action='store_true');p.add_argument('--native-only',action='store_true');a=p.parse_args()
 root=a.root.resolve();root.mkdir(parents=True,exist_ok=True)
 try:
  if a.action=='fetch': fetch(root,a.sha,a.scheduled)
  elif a.action=='build': build(root,a.native_only)
  else: publish(root,a.publish)
 except SafeError as error: print(str(error),file=sys.stderr);return 1
 except Exception:
  # Public stderr must never include private paths/source, HTTP bodies or tokens.
  print('Private build orchestration failed. Inspect configuration or the private build log; no raw exception was printed.',file=sys.stderr);return 1
 return 0
if __name__=='__main__':sys.exit(main())
