"""Durable run conversation, separate from execution state and worker lifetimes."""
import fcntl
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

from . import completion_summary, config, helper

RUNTIME=secrets.token_hex(12)
GUARD=threading.RLock()
RUNNING=set()


def _change(run_dir,operation):
    folder=Path(run_dir)/'conversation';folder.mkdir(parents=True,exist_ok=True)
    path=folder/'messages.json'
    with (folder/'messages.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        rows=json.loads(path.read_text()) if path.exists() else []
        before=json.dumps(rows,ensure_ascii=False)
        for row in rows:
            if row['status'] in ('queued','working') and row.get('owner')!=RUNTIME:
                row.update(status='interrupted',error='Duo restarted before this answer finished. Retry this message.')
        result=operation(rows)
        if json.dumps(rows,ensure_ascii=False)!=before or not path.exists():
            tmp=path.with_name(path.name+'.'+secrets.token_hex(6)+'.tmp')
            tmp.write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n');os.replace(tmp,path)
        return result


def history(run_dir):
    if not (Path(run_dir)/'conversation/messages.json').exists():return []
    return _change(run_dir,lambda rows:[dict(row) for row in rows])


def request(run_dir,root,state,question='',focus=None,provider='auto',request_id=None,retry_id=None):
    if provider not in ('auto','claude','codex'):raise ValueError('Choose Auto, Claude, or Codex.')
    if not retry_id and not str(question).strip():raise ValueError('Write a question first.')
    identity=str(request_id or secrets.token_hex(12))
    def append(rows):
        if retry_id:
            row=next((r for r in rows if r['id']==retry_id),None)
            if not row:raise ValueError('Conversation message not found.')
            if row['status'] in ('failed','interrupted'):
                row.update(status='queued',owner=RUNTIME,error=None)
            return dict(row)
        previous=next((r for r in rows if r['id']==identity),None)
        if previous:
            if previous['q']!=question or previous['requested_provider']!=provider:
                raise ValueError('This message ID already belongs to another question.')
            return dict(previous)
        row=dict(id=identity,q=question,a=None,focus=focus,at=time.time(),status='queued',
                 owner=RUNTIME,requested_provider=provider,run_id=state['id'])
        inputs=Path(run_dir)/'conversation/inputs';inputs.mkdir(parents=True,exist_ok=True)
        snapshot=inputs/(hashlib.sha256(identity.encode()).hexdigest()+'.json')
        context={key:state.get(key) for key in ('id','task','phase','status','options','chosen','decisions','entries','helper','helper_updates','agents','roles')}
        snapshot.write_text(json.dumps(context,indent=2,ensure_ascii=False)+'\n')
        row['context_file']=str(snapshot)
        rows.append(row);return dict(row)
    row=_change(run_dir,append)
    key=str(Path(run_dir).resolve())
    with GUARD:
        if key not in RUNNING:
            RUNNING.add(key)
            threading.Thread(target=_drain,args=(key,Path(root),json.loads(json.dumps(state))),daemon=True).start()
    return row


def _prompt(run_dir,root,state,row,previous):
    facts=dict(state,broker_dir=run_dir)
    lines=[helper.RULES,'This is a conversation, not an execution assignment. Keep task status unchanged. '
           'Do not edit files, place orders, start jobs, or change external services. '
           'If the user requests new work, discuss it and explain that Add work starts an explicit work cycle.',
           '\n--- Saved run facts ---\n'+helper.context(facts,row.get('focus'))]
    report=completion_summary.latest(run_dir) or {}
    if report.get('status')=='ready' and report.get('path'):
        path=Path(report['path']).resolve()
        if path.is_relative_to(root.resolve()) and path.is_file():
            data=path.read_bytes()
            if hashlib.sha256(data).hexdigest()==report.get('sha256'):
                lines+=['\n--- Saved completion summary; historical evidence ---\n'+data.decode('utf-8')]
    earlier=[dict(q=h['q'],a=h.get('a')) for h in (state.get('helper') or [])+(state.get('helper_updates') or [])]+previous
    lines+=['\n--- Conversation history ---']
    for message in earlier:
        if message.get('id')==row['id']:break
        lines+=['User: '+message['q'],'Agent: '+str(message.get('a') or '[No answer recorded]')]
    lines+=['\n--- Current question ---\n'+row['q']]
    return '\n\n'.join(lines)


def _drain(key,root,state):
    try:
        while True:
            with GUARD:
                def take(rows):
                    row=next((r for r in rows if r['status']=='queued'),None)
                    if row:row.update(status='working',owner=RUNTIME)
                    return dict(row) if row else None
                row=_change(key,take)
                if row is None:
                    RUNNING.discard(key);return
            try:
                context=json.loads(Path(row['context_file']).read_text()) if row.get('context_file') else state
                prior=history(key)
                prompt=_prompt(key,root,context,row,prior)
                providers=[p for p in (context.get('agents') or []) if p in ('claude','codex')]
                if row['requested_provider']!='auto':providers=[row['requested_provider']]
                else:
                    builder=(context.get('roles') or {}).get('builder')
                    if builder in providers:providers=[builder]+[p for p in providers if p!=builder]
                answer=None;failures=[];used=None;model=None
                for provider in providers:
                    model=config.CFG.get('chat_models',{}).get(provider)
                    ok,text=helper.run_cli(provider,str(root),prompt,model=model,read_only=True)
                    if ok:answer=text;used=provider;break
                    failures.append(provider+': '+text)
                def finish(rows):
                    target=next(r for r in rows if r['id']==row['id'])
                    target.update(status='answered' if answer is not None else 'failed',a=answer,
                                  provider=used,model=model,error='\n'.join(failures) if answer is None else None,
                                  answered_at=time.time())
                _change(key,finish)
            except Exception as error:
                def fail(rows):
                    next(r for r in rows if r['id']==row['id']).update(status='failed',error=str(error))
                _change(key,fail)
    except Exception:
        with GUARD:RUNNING.discard(key)
        import traceback
        traceback.print_exc()
