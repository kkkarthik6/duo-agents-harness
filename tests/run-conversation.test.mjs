import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const page=readFileSync(new URL('../web/index.html',import.meta.url),'utf8');
const composer=page.slice(page.indexOf('let composeTo ='),page.indexOf('\nfunction refresh()'));
function harness(status='done',saved=true){
  const modes=Object.fromEntries(['agents','helper','work'].map(ct=>[ct,{dataset:{ct},classList:{toggle(){}},setAttribute(){}}]));
  const send={};const msg={value:'Explain the result'};
  const form={style:{},dataset:{},querySelector(selector){return selector==='[type=submit]'?send:modes[selector.match(/ct=(\w+)/)[1]];},querySelectorAll(){return Object.values(modes);}};
  const run={id:'completed',status};const calls=[];
  const c=vm.createContext({console,URLSearchParams,crypto:{randomUUID:()=> 'unique-message'},runPane:'chat',past:saved?run:null,sel:null,live:{session:run},ACTIVE:['running','booting'],
    S:()=>run,$:selector=>selector==='#composer'?form:msg,wireRunPanes(){},
    api:async(url,body)=>{calls.push({url,body});return run;},refresh(){},saveRunDraft(){},setRunPane(){},runQuestions:()=>[],
    addToRun:async(id,text)=>{calls.push({url:'followup',body:{id,text}});return true;},toast(){},select(){}});
  vm.runInContext(composer+'\nbindComposer();',c);
  return {c,run,calls,modes,msg,form,send,submit:()=>form.onsubmit({preventDefault(){}})};
}

test('completed saved run defaults to chat and stays Done',async()=>{
  const h=harness();assert.equal(h.modes.agents.textContent,'Chat with agent');assert.equal(h.modes.helper.hidden,true);assert.equal(h.modes.work.hidden,false);
  assert.match(h.msg.placeholder,/keeps the task marked Done/);
  await h.submit();assert.equal(h.calls[0].url,'/api/ask');assert.equal(h.calls[0].body.run_id,'completed');
  assert.equal(h.calls.filter(c=>c.url==='followup').length,0);assert.equal(h.run.status,'done');
});
test('new work on Done requires the explicit Add work choice',async()=>{
  const h=harness();h.modes.work.onclick();assert.equal(h.send.textContent,'Start work');
  await h.submit();assert.equal(h.calls[0].url,'followup');
  assert.ok(!h.calls.some(c=>c.url==='/api/ask'));
});
test('active-run messaging and helper stay separate',async()=>{
  const h=harness('running',false);assert.equal(h.modes.work.hidden,true);assert.equal(h.modes.helper.hidden,false);
  await h.submit();assert.equal(h.calls[0].url,'/api/message');
  h.msg.value='A helper question';h.modes.helper.onclick();await h.submit();
  assert.equal(h.calls[1].url,'/api/ask');assert.equal(h.calls[1].body.run_id,'completed');
});
test('a saved view retains conversations and the exact project folder',()=>{
  assert.match(page,/api\/chat\?'\+new URLSearchParams\(\{run_id:s.id\}\)/);
  assert.match(page,/s\.conversation \|\| \[\]/);
  assert.match(page,/showStart\(saved.content,s.workspace\)/);
  assert.match(page,/workspace:\$\('#project'\)\.selectedOptions/);
});
