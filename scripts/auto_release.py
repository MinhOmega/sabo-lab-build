#!/usr/bin/env python3
"""Dispatch signed-desktop.yml when private main carries a new stable version
and full verification passed on both platforms for the same source tree."""
import base64, json, os, pathlib, re, sys, tempfile, urllib.error, urllib.parse, urllib.request
import builder, private_cache, signed_release as signed

WORKFLOW='signed-desktop.yml'
VERIFY_WORKFLOW='verify-delivery.yml'
PLATFORMS=('macos','windows')
EVIDENCE_TAG=re.compile(r'verify-(macos|windows)-ci-([0-9a-f]{12})-\d+')
MAX_RELEASE_PAGES=10
MAX_TREE_LOOKUPS=20
MAX_INFO_BYTES=256*1024
BOT='github-actions[bot]'
PACKAGE='apps/desktop/package.json'

def version_key(tag):
 m=re.fullmatch(r'app-v(\d+)\.(\d+)\.(\d+)',tag or '')
 return tuple(int(x) for x in m.groups()) if m else None

def main_version():
 sha=builder.select_commit('')
 row=builder.api('/repos/'+builder.REPOSITORY+'/contents/'+PACKAGE+'?ref='+sha)
 try:version=json.loads(base64.b64decode(row.get('content',''))).get('version','')
 except (ValueError,TypeError):raise builder.SafeError('Private package version unreadable.') from None
 if not re.fullmatch(r'\d+\.\d+\.\d+',str(version)):raise builder.SafeError('Main version is not a stable release.')
 return sha,'app-v'+version

def highest_published():
 best=None
 for page in range(1,6):
  rows=signed.public_api('/repos/'+signed.PUBLIC+'/releases?per_page=100&page='+str(page))
  for row in rows:
   key=version_key(row.get('tag_name'))
   if key and not row.get('draft') and (best is None or key>best):best=key
  if len(rows)<100:return best
 raise builder.SafeError('Release lookup exceeded safe bound.')

def active_release_run():
 for status in ('queued','in_progress','waiting','pending'):
  rows=signed.public_api('/repos/'+signed.PUBLIC+'/actions/workflows/'+WORKFLOW+'/runs?per_page=5&status='+status)
  if rows.get('total_count',0):return True
 return False

def dispatch(sha,tag):dispatch_workflow(WORKFLOW,{'source_sha':sha,'tag':tag})

def dispatch_verify(sha):dispatch_workflow(VERIFY_WORKFLOW,{'source_sha':sha,'mode':'full'})

def dispatch_workflow(workflow,inputs):
 path='/repos/'+signed.PUBLIC+'/actions/workflows/'+workflow+'/dispatches'
 key=os.environ.get('GH_TOKEN','')
 if not key:raise builder.SafeError('Missing release-scoped GitHub token.')
 req=urllib.request.Request(builder.API+path,data=json.dumps({'ref':'main','inputs':inputs}).encode(),method='POST',headers={'Authorization':'Bearer '+key,'User-Agent':'sabo-auto-release','Accept':'application/vnd.github+json','Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=60) as r:
   if r.status not in (200,204):raise builder.SafeError('Dispatch failed (HTTP %s).'%r.status)
 except urllib.error.HTTPError as e:raise builder.SafeError('Dispatch failed (HTTP %s).'%e.code) from None

def commit_tree(sha):
 row=builder.api('/repos/'+builder.REPOSITORY+'/git/commits/'+sha)
 tree=(row.get('tree') or {}).get('sha','');date=(row.get('committer') or {}).get('date','')
 if not re.fullmatch(r'[0-9a-f]{40}',tree) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',date):raise builder.SafeError('Private source tree unreadable.')
 return tree,date

def read_build_info(release):
 asset=next((a for a in release.get('assets',[]) if a.get('name')=='build-info.json'),None)
 if asset is None:return None
 with tempfile.TemporaryDirectory() as folder:
  path=pathlib.Path(folder)/'build-info.json'
  private_cache.download_asset(asset,path,MAX_INFO_BYTES)
  try:return json.loads(path.read_text(encoding='utf-8'))
  except (ValueError,UnicodeError):return None

def verification_state(sha,tree):
 """Newest FULL verification result per platform whose source commit has the
 same git tree as main (a merge commit differs in SHA, not in content)."""
 state={};trees={sha:tree};lookups=0
 for page in range(1,MAX_RELEASE_PAGES+1):
  rows=builder.api('/repos/'+builder.REPOSITORY+'/releases?per_page=100&page='+str(page))
  for release in rows:
   m=EVIDENCE_TAG.fullmatch(release.get('tag_name') or '')
   if not m or m.group(1) in state:continue
   source=release.get('target_commitish') or ''
   if not re.fullmatch(r'[0-9a-f]{40}',source) or not source.startswith(m.group(2)):continue
   if source not in trees:
    if lookups>=MAX_TREE_LOOKUPS:continue
    lookups+=1;trees[source]=commit_tree(source)[0]
   if trees[source]!=tree:continue
   info=read_build_info(release)
   if not isinstance(info,dict) or info.get('sourceCommit')!=source or info.get('platform')!=m.group(1):continue
   if info.get('verificationOnly') is not True or info.get('mode')!='full':continue
   if info.get('status') in ('success','failed'):state[m.group(1)]=info['status']
   if len(state)==len(PLATFORMS):return state
  if len(rows)<100:break
 return state

def is_quick(run):return '(quick)' in (run.get('display_title') or '')

def active_verify_run():
 for status in ('queued','in_progress','waiting','pending'):
  rows=signed.public_api('/repos/'+signed.PUBLIC+'/actions/workflows/'+VERIFY_WORKFLOW+'/runs?per_page=20&status='+status)
  if any(not is_quick(run) for run in rows.get('workflow_runs',[])):return True
 return False

def auto_verify_since(date):
 """True when this job already dispatched a full verification after main moved
 to its current commit; a run that produced no evidence then needs a human."""
 rows=signed.public_api('/repos/'+signed.PUBLIC+'/actions/workflows/'+VERIFY_WORKFLOW+'/runs?per_page=50&event=workflow_dispatch&created='+urllib.parse.quote('>='+date))
 return any((run.get('triggering_actor') or {}).get('login')==BOT and not is_quick(run) for run in rows.get('workflow_runs',[]))

def decide():
 """Return (action, detail). Never re-dispatches an existing tag (draft or
 published): a failed draft needs a human, not a retry loop."""
 sha,tag=main_version()
 if signed.find_release(tag) is not None:return 'skip','%s already has a release or draft.'%tag
 top=highest_published()
 if top is not None and version_key(tag)<=top:return 'skip','%s is not newer than the latest published release.'%tag
 if active_release_run():return 'skip','A signed release run is already active.'
 tree,date=commit_tree(sha);state=verification_state(sha,tree)
 if 'failed' in state.values():return 'skip','Verification failed for the main source tree; fix before release.'
 if any(state.get(p)!='success' for p in PLATFORMS):
  if active_verify_run():return 'skip','Verification is already running.'
  if auto_verify_since(date):return 'skip','Verification finished without a result for main; dispatch verify-delivery manually.'
  dispatch_verify(sha);return 'verifying','Full verification dispatched for main before release.'
 dispatch(sha,tag)
 return 'dispatched','Signed release %s dispatched for %s.'%(tag,sha)

def main():
 try:
  action,detail=decide();print(detail);return 0
 except builder.SafeError as e:print(str(e),file=sys.stderr);return 1
 except Exception:print('Auto release check failed; raw diagnostic suppressed.',file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
