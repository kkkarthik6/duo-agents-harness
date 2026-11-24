import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const page=readFileSync(process.env.DUO_UI_SOURCE || new URL('../web/index.html',import.meta.url),'utf8');
const editing=page.includes('function isTextEditingTarget')?page.slice(page.indexOf('function isTextEditingTarget'),page.indexOf('const rowCache')):'';
const questions=page.slice(page.indexOf('const rowCache'),page.indexOf('\nfunction renderTimeline'));
const inspector=page.slice(page.indexOf('function renderInspector'),page.indexOf('\nfunction agentsHtml'));
const decision=page.slice(page.indexOf('function renderDecision'),page.indexOf('\nfunction renderTurn'));
const keys=page.slice(page.lastIndexOf('document.addEventListener("keydown", ev =>'),page.indexOf('\nbindComposer(); loadSessions();'));
const decode=s=>s.replaceAll('&quot;','"').replaceAll('&#39;',"'").replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// The fake DOM models the browser rule central to this regression: removing or
// moving a focused subtree loses focus. Keeping that subtree preserves its caret.
function harness(storage=new Map()){
  const doc={activeElement:null,querySelector:()=>null,listeners:[],addEventListener(type,fn){this.listeners.push([type,fn]);}};
  class Node {
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.dataset={};this.children=[];this.parentElement=null;this.value='';this.selectionStart=0;this.selectionEnd=0;this.selectionDirection='none';this.textContent='';this.disabled=false;this.classList={toggle(){}};}
    contains(node){return node===this||this.children.some(child=>child.contains(node));}
    closest(selector){return selector.includes(this.tagName.toLowerCase())||this.editable&&selector.includes('contenteditable')||this.textbox&&selector.includes('role="textbox"')?this:null;}
    focus(){doc.activeElement=this;}
    append(child){child.remove();this.children.push(child);child.parentElement=this;}
    remove(){if(!this.parentElement)return;const parent=this.parentElement;if(this.contains(doc.activeElement))doc.activeElement=doc.body;parent.children.splice(parent.children.indexOf(this),1);this.parentElement=null;}
    replaceWith(next){const parent=this.parentElement;const index=parent.children.indexOf(this);this.remove();parent.children.splice(index,0,next);next.parentElement=parent;}
    insertBefore(next,want){next.remove();const index=want?this.children.indexOf(want):this.children.length;this.children.splice(index,0,next);next.parentElement=this;}
    replaceChildren(...nodes){for(const node of [...this.children])node.remove();for(const node of nodes)this.append(node);}
    get firstChild(){return this.children[0]||null;}
    get nextSibling(){const parent=this.parentElement;return parent?.children[parent.children.indexOf(this)+1]||null;}
    querySelectorAll(selector){return selector==='form[data-question]'&&this.form?[this.form]:[];}
    querySelector(){return null;}
    addEventListener(type,fn){this['on'+type]=fn;}
  }
  doc.body=new Node('body');doc.activeElement=doc.body;
  const timeline=new Node();doc.body.append(timeline);
  doc.createElement=tag=>{
    if(tag!=='template')return new Node(tag);
    return {content:{},set innerHTML(html){
      const node=new Node('section');node.dataset.questionKey=decode(html.match(/data-question-key="([^"]*)"/)?.[1]||'');node.dataset.open=html.match(/data-open="([^"]*)"/)?.[1];
      const key=html.match(/<form data-question="([^"]*)"/)?.[1];
      if(key){
        const form=new Node('form');form.dataset.question=decode(key);node.form=form;node.append(form);
        const input=new Node('textarea');input.value=decode(html.match(/<textarea[^>]*>([\s\S]*?)<\/textarea>/)?.[1]||'');form.append(input);form.elements={answer:input};
        const error=new Node('p');form.querySelector=selector=>selector==='.question-error'?error:null;
        const buttons=[new Node('button')];
        const options=[...html.matchAll(/data-question-option="([^"]*)"/g)].map(m=>{const b=new Node('button');b.dataset.questionOption=decode(m[1]);return b;});
        form.querySelectorAll=selector=>selector==='button'?buttons:selector==='[data-question-option]'?options:[];
      }
      this.content.firstElementChild=node;
    }};
  };
  const ins=new Node(),fields=new Map();doc.body.append(ins);ins.fields=fields;
  const addFields=html=>{for(const match of html.matchAll(/<(textarea|button)[^>]*\bid="([^"]+)"[^>]*>/g)){const node=new Node(match[1]);fields.set(match[2],node);ins.append(node);}};
  Object.defineProperty(ins,'innerHTML',{set(html){ins.replaceChildren();fields.clear();addFields(html);}});
  ins.insertAdjacentHTML=(_,html)=>addFields(html);ins.querySelector=selector=>fields.get(selector.slice(1))||null;
  const calls=[],selections=[];const app=new Node();const sheet={open:false};
  const context=vm.createContext({sessionStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},document:doc,console,past:null,sel:'d:D1',insTab:'reply',lastInsKey:null,
    esc:escape,md:escape,select:key=>selections.push(key),toast(){},refresh(){},react(){},labelOf:x=>x,
    api:async(url,body)=>{calls.push({url,body});return {};},S:()=>({status:'running'}),ACTIVE:['running','paused_decision'],COLORS:{},
    $:selector=>selector==='#inspector'?ins:selector==='#app'?app:selector==='#sheet'?sheet:fields.get(selector.slice(1)),
    insightsHtml:()=>'',renderChat(){},renderTurn(){}});
  vm.runInContext(editing+questions,context);
  const run={id:'R1',status:'running',decisions:[],broker:{questions:[{question_id:'Q1',status:'open',text:'Choose the research scope.',options:['Proposal only','Another experiment']}]}};
  context.run=run;context.timeline=timeline;
  const draw=()=>vm.runInContext("patchList(timeline,runQuestions(run).map(q=>({key:'question:'+run.id+':'+q.id,html:questionCard(q,run)})))",context);
  const type=text=>{const input=doc.activeElement;input.value=input.value.slice(0,input.selectionStart)+text+input.value.slice(input.selectionEnd);input.selectionStart=input.selectionEnd=input.selectionStart+text.length;input.oninput?.();};
  return {context,doc,Node,timeline,ins,calls,selections,run,draw,type};
}

test('typing and selection survive repeated live refreshes of an answer card',()=>{
  const h=harness();h.draw();const card=h.timeline.firstChild,input=card.form.elements.answer;input.focus();
  h.type('Keep V4.3 <paper only> & wait');input.selectionStart=5;input.selectionEnd=9;input.selectionDirection='backward';
  for(let i=0;i<20;i++)h.draw();
  assert.equal(h.timeline.firstChild,card);assert.equal(h.doc.activeElement,input);
  assert.equal(input.value,'Keep V4.3 <paper only> & wait');assert.equal(input.selectionStart,5);assert.equal(input.selectionEnd,9);assert.equal(input.selectionDirection,'backward');
  h.type('research');assert.equal(h.calls.length,0);
});
test('changed recommendations wait for editing to finish and preserve the draft',()=>{
  const h=harness();h.draw();const card=h.timeline.firstChild,input=card.form.elements.answer;input.focus();h.type('My own decision');
  h.run.broker.questions[0].recommendation='Proposal only';h.draw();assert.equal(h.timeline.firstChild,card);assert.equal(h.doc.activeElement,input);
  h.doc.activeElement=h.doc.body;h.draw();assert.notEqual(h.timeline.firstChild,card);assert.equal(h.timeline.firstChild.form.elements.answer.value,'My own decision');
});
test('an externally answered question updates without submitting the local draft',()=>{
  const h=harness();h.draw();h.timeline.firstChild.form.elements.answer.focus();h.type('Unsent answer');
  h.run.broker.questions[0].status='answered';h.run.broker.questions[0].answer='Recorded elsewhere';h.draw();
  assert.equal(h.timeline.firstChild.dataset.open,'false');assert.equal(h.calls.length,0);
});
test('custom answers still submit once with the correct question and full text',async()=>{
  const h=harness();h.draw();const form=h.timeline.firstChild.form;form.elements.answer.focus();h.type('Keep scope fixed. j k /');h.draw();
  await form.onsubmit({preventDefault(){}});await Promise.resolve();
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'/api/question-answer');assert.equal(h.calls[0].body.id,'R1');assert.equal(h.calls[0].body.question_id,'Q1');assert.equal(h.calls[0].body.text,'Keep scope fixed. j k /');
});
test('shortcut letters, composition, and native modifier keys do not navigate while editing',()=>{
  const h=harness();vm.runInContext(keys,h.context);const handler=h.doc.listeners.at(-1)[1];
  const row=new h.Node('button');row.dataset.sel='e:1';h.doc.querySelectorAll=()=>[row];
  const input=new h.Node('textarea');h.doc.body.append(input);input.focus();
  const key=(value,extra={})=>handler({key:value,target:h.doc.body,preventDefault(){},...extra});
  for(const value of ['j','k','n','f','c','/'])key(value);
  assert.equal(h.selections.length,0);h.doc.activeElement=h.doc.body;
  const editable=new h.Node();editable.editable=true;key('j',{target:editable});key('j',{isComposing:true});key('j',{metaKey:true});key('j',{ctrlKey:true});
  assert.equal(h.selections.length,0);key('j');assert.deepEqual(h.selections,['e:1']);
});
test('inspector updates do not replace an active decision editor',()=>{
  const h=harness();let rendered=0;h.context.renderDecision=()=>{rendered++;h.ins.replaceChildren(new h.Node('textarea'));};
  vm.runInContext(inspector,h.context);
  const run={id:'R1',status:'paused_decision',decisions:[{id:'D1',status:'paused'}],conversation:[]};h.context.state=run;
  vm.runInContext('renderInspector(state)',h.context);const input=h.ins.firstChild;input.focus();input.value='Typed custom option';
  run.conversation.push({id:'background',status:'working'});vm.runInContext('renderInspector(state)',h.context);
  assert.equal(rendered,1);assert.equal(h.doc.activeElement,input);assert.equal(input.value,'Typed custom option');
  h.doc.activeElement=h.doc.body;vm.runInContext('renderInspector(state)',h.context);assert.equal(rendered,2);
});
test('decision and helper drafts survive inspector rebuilds and stay isolated by run',()=>{
  const h=harness();vm.runInContext(decision,h.context);
  const d={id:'D1',status:'paused',ranking:[],cards:[],reasons:[],proposed:'O1'},s={id:'R1',status:'paused_decision',rubric:{criteria:[],gates:[]},options:[],helper:[],conversation:[]};
  h.context.d=d;h.context.state=s;vm.runInContext('renderDecision(d,state)',h.context);
  for(const [id,text] of [['custom','My custom choice'],['askQ','Why did it fail?'],['constraintText','Preserve the original study.']]){const input=h.ins.fields.get(id);input.value=text;input.oninput();}
  vm.runInContext('renderDecision(d,state)',h.context);
  assert.equal(h.ins.fields.get('custom').value,'My custom choice');assert.equal(h.ins.fields.get('askQ').value,'Why did it fail?');assert.equal(h.ins.fields.get('constraintText').value,'Preserve the original study.');
  s.id='R2';vm.runInContext('renderDecision(d,state)',h.context);assert.equal(h.ins.fields.get('custom').value,'');assert.equal(h.ins.fields.get('askQ').value,'');
});

test('unsent question drafts survive reload without sending a decision',()=>{
  const storage=new Map(),first=harness(storage);first.draw();first.timeline.firstChild.form.elements.answer.focus();first.type('Continue only within this scope.');
  const reloaded=harness(storage);reloaded.draw();assert.equal(reloaded.timeline.firstChild.form.elements.answer.value,'Continue only within this scope.');assert.equal(reloaded.calls.length,0);
});
test('unavailable draft storage does not interrupt typing',()=>{
  const storage={get(){throw Error('unavailable');},set(){throw Error('unavailable');}},h=harness(storage);h.draw();const input=h.timeline.firstChild.form.elements.answer;input.focus();h.type('Still typing');h.draw();assert.equal(h.doc.activeElement,input);assert.equal(input.value,'Still typing');
});
test('a submitted custom decision is not restored as an unsent draft',async()=>{
  const h=harness();vm.runInContext(decision,h.context);h.context.d={id:'D1',status:'paused',ranking:[],cards:[],reasons:[],proposed:'O1'};h.context.state={id:'R1',status:'paused_decision',rubric:{criteria:[],gates:[]},options:[],helper:[],conversation:[]};
  vm.runInContext('renderDecision(d,state)',h.context);const input=h.ins.fields.get('custom');input.value='My custom choice';input.oninput();await h.ins.fields.get('pickCustom').onclick();vm.runInContext('renderDecision(d,state)',h.context);assert.equal(h.ins.fields.get('custom').value,'');assert.equal(h.calls.length,1);
});
