"""Small current-task projections. History and full prompts stay in their own files."""
import json
import os
import re
import secrets
import fcntl
from datetime import datetime
from pathlib import Path

from . import broker

FOCUS = {
    'cycle_summary':'Write the detailed cycle summary',
    'goal_check':'Check the user goal against saved evidence',
    'goal_assess':'Independently assess the unfinished work',
    'goal_assign':'Plan the next build from independent assessments',
    'assess':'Assess the requirements, prior evidence, and feasibility',
    'plan_review':'Review the proposed plan independently',
    'exchange':'Reconcile assessments and write the agreed work plan',
    'frame':'Define requirements and acceptance evidence',
    'build':'Implement the agreed work plan',
    'revise':'Resolve the required review findings',
    'critique':'Review the submitted work against the requirements',
    'review':'Review the submitted work against the requirements',
    'verify':'Verify the deliverables and completion evidence',
    'secure':'Review the relevant security findings',
    'continuation_review':'Review saved work and define what remains',
    'deliver':'Prepare the final delivery and evidence handoff',
}


def _write(path, value):
    data=json.dumps(value,indent=2,ensure_ascii=False)+'\n'
    if path.exists() and path.read_text()==data: return
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.parent/(path.name+'.'+secrets.token_hex(6)+'.tmp')
    temp.write_text(data);os.replace(temp,path)


def _items(text):
    return [re.sub(r'^\s*[-*]\s+|^\s*\d+[.)]\s+','',line).strip() for line in (text or '').splitlines() if line.strip()]


def update(run_dir, root, state, graph=None):
    try:
        return _update(Path(run_dir),Path(root),state,graph or broker.view(run_dir))
    except Exception as failure:
        try: broker.error(run_dir,'task-context',str(failure),operation='current_task_projection',source='broker')
        except Exception: pass
        return {'available':False}


def _update(run_dir, root, state, graph):
    pending=state.get('pending',[])
    if isinstance(pending,dict):pending=list(pending.values())
    assigned=[p.get('broker_task_id',p.get('task_id')) for p in pending]
    active=assigned if state.get('status')=='running' and not state.get('paused') else []
    tasks={}
    for node in graph['nodes']:
        sections=node.get('summary_sections',{})
        local=node.get('context_note',{})
        use_local=bool(local) and (not sections.get('task focus') or node.get('context_note_updated',0)>=node.get('summary_updated',0))
        if use_local: sections=dict(sections,**local)
        assignment=node.get('assignments',[])
        role=node.get('current_role') or node.get('original_role') or (assignment[-1].get('role') if assignment else 'task')
        actor=assignment[-1].get('agent','') if assignment else ''
        actor=next((a for a in ('claude','codex') if actor==a),actor)
        label=FOCUS.get(role,role.replace('_',' ').capitalize())
        focus=(sections.get('task focus') or sections.get('task summary') or label).strip()
        if role in broker.CYCLE_ROLES and sections.get('task focus'):
            focus = label + ' · ' + focus
        if not sections.get('task focus') and actor:
            focus+=' · '+actor
        items=node.get('work_items',[])
        useful=[item for item in items if item['text'].strip() not in
                {str(state.get('task','')).strip(),str(node.get('specification','')).strip(),str(node.get('title','')).strip()}]
        checked=[item['text'] for item in useful if item['checked']]
        remaining=[item['text'] for item in useful if not item['checked']]
        plan=[item['text'] for item in useful] if len(useful)>1 else _items(sections.get('plan')) or remaining or [label]
        finished=list(dict.fromkeys(checked+_items(sections.get('finished'))))
        results=sections.get('results summary') or sections.get('outcomes') or ''
        if not results:
            results='No outcome has been summarized yet.' if not node.get('outputs') else 'Saved work is available. Its outcomes have not been summarized yet.'
        doing=sections.get('doing') or (focus if node['id'] in active or node['status'] in ('working','reviewing_previous') else 'No active assignment is recorded for this task.')
        if node['id'] not in assigned:
            doing='No active assignment is recorded for this task.'
        elif state.get('paused') or state.get('status')!='running':
            doing='This assignment is paused or waiting. '+focus
        tasks[node['id']]={'summary':focus,'goal':sections.get('goal') or label,'plan':plan,
            'finished':finished,'doing':doing,'results_summary':results,
            'next_steps':_items(sections.get('next steps')) or remaining,
            'status':node['status'],'parent_task_id':node.get('parent'),
            'evidence_refs':[{'path':item.get('path'),'sha256':item.get('sha256')} for item in node.get('outputs',[])],
            'updated_at':node['updated'],'summary_source':node.get('context_note_source') if use_local else 'task summary agent',
            'summary_current':bool(node.get('summary_is_current')) or (use_local and node.get('context_note_updated',0)>=max(
                [row['time'] for row in node.get('checkpoints',[])]+[broker._mtime(node.get('task_file'))]))}
        node['title']=focus
        node['task_context']=tasks[node['id']]
    active=[key for key in active if key in tasks]
    current=assigned[0] if assigned else next((node['id'] for node in reversed(graph['nodes']) if node['status'] in ('working','reviewing_previous')),None)
    if state.get('status')=='done':current=None;active=[]
    goal=state.get('goal_summary') or state.get('title') or str(state.get('task','')).split('\n')[0]
    record={'schema_version':1,'run_id':state.get('id'),'goal':goal,'run_status':state.get('status'),
            'current_task_id':current,'active_task_ids':active,'task_summaries':{key:item['summary'] for key,item in tasks.items()},
            'tasks':tasks,'original_request_ref':str(run_dir/'state.json'),'history_ref':str(run_dir/'broker/events.jsonl')}
    context={'schema_version':1,'run_id':state.get('id'),'goal':goal,
             'current_task_id':current,'current_task_file':str(run_dir/'current_task.json'),
             'original_request_ref':str(run_dir/'state.json'),
             'current_tasks':[{key:value for key,value in tasks[task].items() if key!='evidence_refs'}|{'task_id':task} for task in (active or ([current] if current in tasks else []))],
             'useful_previous':[{'task_id':key,'summary':item['summary'],'results_summary':item['results_summary'],
                                 'status':item['status'],'evidence_refs':item['evidence_refs']}
                                for key,item in tasks.items() if key not in active and item['status'] in
                                ('completed','stage_completed','submitted','partial','needs_work','review_pending')],
             'open_questions':[{'question_id':q['question_id'],'question':q['text'],'recommendation':q.get('recommendation','')}
                               for q in graph.get('questions',[]) if q['status']=='open'],
             'user_decisions':[{'question_id':q['question_id'],'question':q['text'],'answer':q['answer'],'answered_at':q.get('answered_at')}
                               for q in graph.get('questions',[]) if q['status']=='answered'],
             'ranked_decisions':[{'decision_id':d.get('id'),'chosen':d.get('chosen'),
                                  'summary':d.get('titles',{}).get(d.get('chosen'),d.get('custom')),'by':d.get('by')}
                                 for d in state.get('decisions',[]) if d.get('status')=='decided'],
             'diagnostics_ref':str(run_dir/'diagnostics'),
             'shared_jobs':[{key:job.get(key) for key in ('job_id','task_id','status','reference')} for job in graph.get('jobs',[])],
             'completion_summary_ref':(graph.get('completion_summary') or {}).get('path')}
    _write(run_dir/'current_task.json',record)
    _write(run_dir/'latest_context.json',context)
    pointer=root/'.duo/latest_context.json'
    pointer.parent.mkdir(parents=True,exist_ok=True)
    with (pointer.parent/'.context-pointer.lock').open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        old=json.loads(pointer.read_text()) if pointer.exists() else {}
        try: created=datetime.fromisoformat(state.get('created','')).timestamp()
        except (TypeError,ValueError):created=0
        active_now=state.get('status')=='running' and not state.get('paused')
        if created>=old.get('run_created_at',0) or (active_now and not old.get('active')):
            _write(pointer,{'run_id':state.get('id'),'run_created_at':created,'active':active_now,
                            'current_task_file':str(run_dir/'current_task.json'),'context_file':str(run_dir/'latest_context.json'),
                            'current_task_id':current})
    return {'available':True,'current_task_file':str(run_dir/'current_task.json'),
            'latest_context_file':str(run_dir/'latest_context.json'),'project_pointer':str(pointer),'current_task_id':current,'tasks':tasks}
