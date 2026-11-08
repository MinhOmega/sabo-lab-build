#!/usr/bin/env python3
"""Dispatch signed-desktop.yml when private main carries a new stable version."""
import base64, json, os, re, sys, urllib.error, urllib.request
import builder, signed_release as signed

WORKFLOW='signed-desktop.yml'
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

def dispatch(sha,tag):
 path='/repos/'+signed.PUBLIC+'/actions/workflows/'+WORKFLOW+'/dispatches'
 key=os.environ.get('GH_TOKEN','')
 if not key:raise builder.SafeError('Missing release-scoped GitHub token.')
 req=urllib.request.Request(builder.API+path,data=json.dumps({'ref':'main','inputs':{'source_sha':sha,'tag':tag}}).encode(),method='POST',headers={'Authorization':'Bearer '+key,'User-Agent':'sabo-auto-release','Accept':'application/vnd.github+json','Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=60) as r:
   if r.status not in (200,204):raise builder.SafeError('Dispatch failed (HTTP %s).'%r.status)
 except urllib.error.HTTPError as e:raise builder.SafeError('Dispatch failed (HTTP %s).'%e.code) from None

def decide():
 """Return (action, detail). Never re-dispatches an existing tag (draft or
 published): a failed draft needs a human, not a retry loop."""
 sha,tag=main_version()
 if signed.find_release(tag) is not None:return 'skip','%s already has a release or draft.'%tag
 top=highest_published()
 if top is not None and version_key(tag)<=top:return 'skip','%s is not newer than the latest published release.'%tag
 if active_release_run():return 'skip','A signed release run is already active.'
 dispatch(sha,tag)
 return 'dispatched','Signed release %s dispatched for %s.'%(tag,sha)

def main():
 try:
  action,detail=decide();print(detail);return 0
 except builder.SafeError as e:print(str(e),file=sys.stderr);return 1
 except Exception:print('Auto release check failed; raw diagnostic suppressed.',file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())
