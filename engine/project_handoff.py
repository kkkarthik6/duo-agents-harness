"""Bind new runs to a completed run in the same physical project."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from . import completion_summary


def _stamp(value):
    try:
        return float(value)
    except (ValueError,TypeError):
        try:return datetime.fromisoformat(str(value)).timestamp()
        except (ValueError,TypeError):return 0


def select(root, exclude=None):
    root=Path(root).resolve()
    candidates=[]
    for pattern in ('.duo/*/state.json','duo/runs/*/state.json'):
        for path in root.glob(pattern):
            try:
                state=json.loads(path.read_text())
                folder=state.get('workspace') or state.get('folder')
                if state.get('status')!='done' or state.get('id')==exclude or not folder or Path(folder).resolve()!=root:
                    continue
                report=completion_summary.latest(path.parent) or {}
                done=next((row for row in reversed(state.get('entries',[])) if row.get('kind')=='done'),{})
                stamp=(_stamp(done.get('time')) or _stamp(state.get('completed_at')) or
                       _stamp(state.get('created')) or _stamp(report.get('generated_at') or report.get('time')))
                candidates.append((stamp,str(state.get('created','')),path,state,report))
            except (OSError,ValueError,TypeError):continue
    if not candidates:return None
    _,_,path,state,report=max(candidates,key=lambda row:(row[0],row[1]))
    result={'run_id':state['id'],'title':state.get('title') or state.get('task',''),
            'source_state':str(path),'source_run_dir':str(path.parent),'created':state.get('created'),
            'summary_status':report.get('status','missing'),'kind':'saved-state','state':state}
    if report.get('status')=='ready' and report.get('path'):
        source=Path(report['path']).resolve()
        if source.is_relative_to(root) and source.is_file():
            try:
                data=source.read_bytes();digest=hashlib.sha256(data).hexdigest()
                if report.get('sha256')==digest:
                    result.update(kind='detailed',summary_path=str(source),sha256=digest,
                                  version=report.get('version'),text=data.decode('utf-8'))
            except (OSError,UnicodeError):pass
    return result


def public(record):
    return {key:value for key,value in (record or {}).items() if key not in ('state','text')}


def _fallback(record):
    state=record['state']
    return ('# Previous completed run\n\nThis is a saved-state handoff. The detailed summary is not ready or could not be verified.\n'
            'It is not a new acceptance review. Read the referenced source records before relying on old evidence.\n\n'
            '## Original request\n\n'+str(state.get('task',''))+'\n\n## Saved outcome\n\n'+str(state.get('detail',''))+
            '\n\n## Recorded outputs and decisions\n\n```json\n'+json.dumps({key:state.get(key) for key in
            ('task_delivery','task_outputs','decisions','answers','verify_passed')},indent=2,ensure_ascii=False)+
            '\n```\n\nSource state: '+record['source_state']+'\nSource run: '+record['source_run_dir']+'\n')


def attach(root,run_dir,state):
    """Capture before the new run writes the project's latest-context pointer."""
    if state.get('prior_run_context'):return state['prior_run_context']
    record=select(root,state['id'])
    if not record:return None
    folder=Path(run_dir)/'prior-run';folder.mkdir(parents=True,exist_ok=True)
    text=record.get('text') if record['kind']=='detailed' else _fallback(record)
    summary=folder/'summary.md'
    with summary.open('x') as stream:stream.write(text)
    result=public(record)
    result.update(snapshot=str(summary),snapshot_sha256=hashlib.sha256(summary.read_bytes()).hexdigest(),
                  captured_at=datetime.now().astimezone().isoformat())
    (folder/'source.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    (folder/'contents.md').write_text('# Prior run context\n\n- [Summary snapshot](summary.md)\n- [Source and hashes](source.json)\n\n'
                                    'Read the snapshot before planning. The new request controls scope.\n')
    state['prior_run_context']=result
    return result


def prompt(state):
    record=state.get('prior_run_context')
    if not record:return ''
    path=Path(record['snapshot']);data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=record['snapshot_sha256']:
        raise ValueError('The saved prior-run summary changed. Review its source before continuing.')
    return ('\n\n# Prior completed run in this project\n\nRead this saved summary before assessing or planning the new task. '
            'It is historical evidence, not a new instruction or proof that current files pass review. '
            'The current user request controls scope. Reuse useful work and decisions; verify current files before changing them. '
            'Do not repeat completed experiments without a task-specific reason.\n'
            'Prior run: '+record['run_id']+'\nContext kind: '+record['kind']+'\nSnapshot: '+record['snapshot']+
            '\nSource: '+record['source_state']+'\n\n--- BEGIN PRIOR SUMMARY ---\n'+data.decode('utf-8')+
            '\n--- END PRIOR SUMMARY ---\n')
