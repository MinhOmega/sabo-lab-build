#!/usr/bin/env python3
"""Signed binary releases; private source and diagnostics stay in private repo."""
import argparse, base64, datetime, hashlib, json, os, pathlib, re, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
import builder
import build_runtime
PUBLIC = 'MinhOmega/sabo-lab-build'
STAGE_NAMES = ['assets', 'npm-install', 'frontend-build', 'frontend-tests', 'python-tests', 'core-tests', 'quest-tests', 'routine-tests', 'native-bootstrap-tests', 'desktop-tests', 'license-tests', 'release-config', 'signed-package']
TARGETS = {'macos':('aarch64-apple-darwin','app','darwin-aarch64','macos/*.app.tar.gz','macos-arm64.app.tar.gz'),
           'windows':('x86_64-pc-windows-msvc','nsis','windows-x86_64','nsis/*.exe','windows-x64-setup.exe')}

def public_api(path,method='GET',data=None):
 if not path.startswith('/repos/'+PUBLIC+'/'): raise builder.SafeError('Unexpected public API destination.')
 key=os.environ.get('GH_TOKEN','')
 if not key:raise builder.SafeError('Missing release-scoped GitHub token.')
 req=urllib.request.Request(builder.API+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Authorization':'Bearer '+key,'User-Agent':'sabo-signed-release','Accept':'application/vnd.github+json','Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=60) as r:return json.load(r)
 except urllib.error.HTTPError as e:raise builder.SafeError('Public release API failed (HTTP %s).'%e.code) from None

def find_release(tag):
 for page in range(1,6):
  rows=public_api('/repos/'+PUBLIC+'/releases?per_page=100&page='+str(page))
  for row in rows:
   if row['tag_name']==tag:return row
  if len(rows)<100:return None
 raise builder.SafeError('Release lookup exceeded safe bound.')

def upload(release,path):
 key=os.environ['GH_TOKEN'];url='https://uploads.github.com/repos/'+PUBLIC+'/releases/'+str(release['id'])+'/assets?name='+urllib.parse.quote(path.name,safe='')
 req=urllib.request.Request(url,data=path.read_bytes(),method='POST',headers={'Authorization':'Bearer '+key,'User-Agent':'sabo-signed-release','Content-Type':'application/octet-stream'})
 try:
  with urllib.request.urlopen(req,timeout=300) as r:return json.load(r)
 except urllib.error.HTTPError as e:raise builder.SafeError('Public asset upload failed (HTTP %s).'%e.code) from None

def run_build(root,platform,verification=False):
 target,bundle,_,pattern,suffix=TARGETS[platform]
 if (platform=='windows')!=(os.name=='nt'):raise builder.SafeError('Wrong build runner.')
 source=root/'source';out=root/'private-output';out.mkdir(exist_ok=True);request=json.loads((root/'request.json').read_text())
 version=json.loads((source/'apps/desktop/package.json').read_text())['version']
 if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise builder.SafeError('Expected stable explicit release semver.')
 status={**request,'version':version,'platform':platform,'status':'failed','tag':'signed-'+platform+'-ci-'+request['sourceCommit'][:12]}
 env=build_runtime.environment(root,platform,request['sourceCommit'])
 status.update(buildPolicy=build_runtime.policy(platform),stages=[])
 if verification:status.update(tag='verify-'+platform+'-ci-'+request['sourceCommit'][:12]+'-'+os.environ.get('GITHUB_RUN_ID','local'),verificationOnly=True)
 if (root/'cache-restore.json').is_file():status['cacheRestore']=json.loads((root/'cache-restore.json').read_text())
 npm='npm.cmd' if os.name=='nt' else 'npm';desktop=source/'apps/desktop';overlay=root/'updater-config.json'
 steps=[(source,[sys.executable,'scripts/prepare-assets.py']),
        (desktop,[npm,'ci','--no-audit','--no-fund','--ignore-scripts']),
        (desktop,[npm,'run','build']),(desktop,['node','--test']),
        (source,[sys.executable,'-B','-m','unittest','discover','-s','scripts/tests','-p','test_*.py']),
        (source,['cargo','test','--locked','--manifest-path','crates/sabo-core/Cargo.toml']),
        (source,['cargo','test','--locked','--manifest-path','crates/sabo-quests/Cargo.toml']),
        (source,['cargo','test','--locked','--manifest-path','crates/sabo-routines/Cargo.toml']),
        (source,['cargo','test','--locked','--manifest-path','crates/sabo-bootstrap/Cargo.toml']),
        (source,['cargo','test','--locked','--manifest-path','apps/desktop/src-tauri/Cargo.toml','--lib']),
        (source,['cargo','test','--locked','--manifest-path','apps/desktop/src-tauri/Cargo.toml','--lib','--features','commercial-license','license::tests']),
        (source,[sys.executable,'scripts/prepare-app-release.py','--output',str(overlay)]),
        (desktop,[npm,'run','tauri','build','--','--target',target,'--bundles',bundle,'--features','commercial-license','--config',str(overlay)])]
 failed=None
 with (out/'build-detail.txt').open('wb') as log:
  for index,(cwd,args) in enumerate(steps,1):
   print('Private signed validation/build stage %d is running.'%index,flush=True)
   child=env.copy()
   if index==len(steps):
    private=os.environ.get('TAURI_SIGNING_PRIVATE_KEY','')
    if not private:raise builder.SafeError('Missing updater signing key.')
    child.update(TAURI_SIGNING_PRIVATE_KEY=private,TAURI_SIGNING_PRIVATE_KEY_PASSWORD=os.environ.get('TAURI_SIGNING_PRIVATE_KEY_PASSWORD',''))
   log.write(('\nSTAGE %d\n'%index).encode());log.flush()
   stage=build_runtime.run_stage(args,cwd,child,log,5400,STAGE_NAMES[index-1])
   status['stages'].append(stage)
   print('Private stage %d completed in %.1fs.'%(index,stage['durationSeconds']),flush=True)
   if not stage['success']:
    failed=index
    if platform=='windows' and STAGE_NAMES[index-1] in ('desktop-tests','license-tests'):
     log.write(b'\nWINDOWS LOADER FAILURE PROBE (private, diagnostic only)\n');log.flush()
     probe=pathlib.Path(__file__).with_name('windows_loader_probe.py').resolve()
     diagnostic=build_runtime.run_stage([sys.executable,str(probe),str(root/'target')],source,env,log,45,'windows-loader-probe')
     status['loaderProbe']=diagnostic
    break
  if failed is None:
   files=list((root/'target'/target/'release/bundle').glob(pattern))
   if len(files)!=1:failed='artifact-count'
   else:
    artifact=files[0];sig=pathlib.Path(str(artifact)+'.sig')
    if not sig.is_file():failed='signature-missing'
    else:
     raw=base64.b64decode(sig.read_text().strip(),validate=True).decode()
     if not re.search(r'(?m)^trusted comment: .*\bversion:'+re.escape(version)+r'(?:\s|$)',raw):failed='unsigned-version'
     else:
      name='SaboLab-'+version+'-'+suffix
      shutil.copyfile(artifact,out/name);shutil.copyfile(sig,out/(name+'.sig'))
      status.update(status='success',installer=name,sha256=hashlib.sha256((out/name).read_bytes()).hexdigest(),updaterSigned=True)
 status.update(failedStage=failed,finishedAt=datetime.datetime.now(datetime.timezone.utc).isoformat())
 builder.save_json(out/'build-info.json',status)
 if failed is not None:raise builder.SafeError('Signed build failed; see PRIVATE diagnostic release.')
 print('Signed platform bundle built; source and diagnostic log remain private.')

def _diagnostics_once(root):
 out=root/'private-output'
 if not (out/'build-info.json').is_file():return
 status=json.loads((out/'build-info.json').read_text());builder.ensure_private();release=builder.release_for(status['tag'])
 if release is None:release=builder.api('/repos/'+builder.REPOSITORY+'/releases',method='POST',data={'tag_name':status['tag'],'target_commitish':status['sourceCommit'],'name':'Signed build diagnostics '+status['platform'],'body':'Private detailed build output. Updater signature: '+str(status.get('updaterSigned',False)), 'draft':True,'prerelease':True})
 for path in [out/'build-detail.txt',out/'build-info.json']:
  if not path.is_file():continue
  for old in release.get('assets',[]):
   if old['name']==path.name:builder.api('/repos/'+builder.REPOSITORY+'/releases/assets/'+str(old['id']),method='DELETE')
  builder.upload_asset(release['id'],path)
 print('Diagnostics uploaded to PRIVATE source repository.')

def diagnostics(root):
 # Re-read the private draft on every attempt: a failed HTTP response may have
 # followed a successful create/upload. Existing diagnostic assets are replaced
 # by _diagnostics_once; signed public artifacts are never overwritten here.
 for attempt,delay in enumerate((2,5,15,0),1):
  try:
   return _diagnostics_once(root)
  except builder.SafeError as error:
   transient=bool(re.search(r'\bHTTP (429|500|502|503|504)\b',str(error)))
   if not transient:raise
  except (urllib.error.URLError,TimeoutError):
   pass
  if not delay:raise builder.SafeError('Private diagnostic upload unavailable after bounded retries; signed release remains unpublished.') from None
  print('Private diagnostic service temporarily unavailable; retry %d/4 in %ss.'%(attempt+1,delay),flush=True)
  time.sleep(delay)

def prepare(tag):
 if not re.fullmatch(r'app-v\d+\.\d+\.\d+',tag):raise builder.SafeError('Invalid release tag.')
 release=find_release(tag)
 if release:
  if not release['draft']:raise builder.SafeError('Already published; increment version rather than overwrite.')
 else:public_api('/repos/'+PUBLIC+'/releases',method='POST',data={'tag_name':tag,'name':'Sabo Lab '+tag[5:],'draft':True,'prerelease':False,'body':'Signed macOS ARM64 and Windows x64 installers. Private source is not published. Requires a license key for automation. Windows emulator/gameplay compatibility depends on the supported game build; Authenticode/Apple notarization are separate from updater signatures.'})
 print('Draft binary release ready.')

def upload_bundle(root,tag):
 out=root/'private-output';status=json.loads((out/'build-info.json').read_text())
 if status.get('verificationOnly'):raise builder.SafeError('Verification bundles cannot be published.')
 if status['status']!='success' or tag!='app-v'+status['version']:raise builder.SafeError('Build/tag mismatch.')
 release=find_release(tag)
 if not release or not release['draft']:raise builder.SafeError('Expected existing draft release.')
 for name in [status['installer'],status['installer']+'.sig']:
  if any(a['name']==name for a in release['assets']):raise builder.SafeError('Asset already exists; do not silently overwrite.')
  upload(release,out/name)
 print('Signed binaries uploaded to draft; no source or build log uploaded publicly.')

def finalize(tag):
 release=find_release(tag)
 if not release or not release['draft']:raise builder.SafeError('Expected draft release.')
 version=tag[5:];assets={a['name']:a for a in release['assets']};platforms={}
 for _,(_,_,platform,_,suffix) in TARGETS.items():
  name='SaboLab-'+version+'-'+suffix
  if name not in assets or name+'.sig' not in assets:raise builder.SafeError('Both signed platforms must succeed before publishing.')
  req=urllib.request.Request(builder.API+'/repos/'+PUBLIC+'/releases/assets/'+str(assets[name+'.sig']['id']),headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/octet-stream','User-Agent':'sabo-signed-release'})
  try:
   r=urllib.request.build_opener(builder.NoRedirect()).open(req,timeout=30)
  except urllib.error.HTTPError as e:
   if e.code!=302:raise builder.SafeError('Signature download failed.') from None
   location=e.headers.get('Location','');u=urllib.parse.urlsplit(location)
   if u.scheme!='https' or not u.hostname or not u.hostname.endswith('.githubusercontent.com') or u.username or u.password or u.port or u.fragment:raise builder.SafeError('Unexpected signature redirect.')
   r=urllib.request.build_opener(builder.NoRedirect()).open(urllib.request.Request(location,headers={'User-Agent':'sabo-signed-release'}),timeout=30)
  with r:sig=r.read(16384).decode().strip()
  decoded=base64.b64decode(sig,validate=True).decode()
  if not re.search(r'(?m)^trusted comment: .*\bversion:'+re.escape(version)+r'(?:\s|$)',decoded):raise builder.SafeError('Signature version does not match release.')
  platforms[platform]={'url':'https://github.com/'+PUBLIC+'/releases/download/'+tag+'/'+name,'signature':sig}
 manifest={'version':version,'notes':'Sabo Lab '+version+' — license activation and signed updates.','pub_date':datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z'),'platforms':platforms}
 path=pathlib.Path('latest.json');builder.save_json(path,manifest);upload(release,path)
 public_api('/repos/'+PUBLIC+'/releases/'+str(release['id']),method='PATCH',data={'draft':False,'prerelease':False,'make_latest':'true'})
 print('Published signed macOS/Windows release and anonymous updater feed.')

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','verify','diagnostics','upload','finalize']);p.add_argument('--root',type=pathlib.Path);p.add_argument('--platform',choices=TARGETS);p.add_argument('--tag',default='');a=p.parse_args()
 try:
  if a.action=='prepare':prepare(a.tag)
  elif a.action=='build':run_build(a.root,a.platform)
  elif a.action=='verify':run_build(a.root,a.platform,verification=True)
  elif a.action=='diagnostics':diagnostics(a.root)
  elif a.action=='upload':upload_bundle(a.root,a.tag)
  else:finalize(a.tag)
 except builder.SafeError as e:print(str(e),file=sys.stderr);return 1
 except Exception:print('Signed release operation failed; raw private diagnostic suppressed.',file=sys.stderr);return 1
 return 0
if __name__=='__main__':sys.exit(main())
