import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const page=readFileSync(new URL('../web/index.html',import.meta.url),'utf8');
const helper=page.slice(page.indexOf('function shortRunTitle('),page.indexOf('\nfunction renderHeader('));
const shortTitle=vm.runInNewContext('('+helper+')');

test('the header uses a short project label without changing the request',()=>{
  const run={project:'ORB Setup review',task:'Can you assess the current directory and build a trading system? '+ 'Keep the full request. '.repeat(30)};
  const original=JSON.stringify(run);
  assert.equal(shortTitle(run),'ORB Setup review');
  assert.equal(JSON.stringify(run),original);
  assert.match(page,/\$\("#title"\)\.title = s \? s\.task/);
});

test('explicit titles and fallback requests have a bounded single-line label',()=>{
  assert.equal(shortTitle(null),'Duo');
  assert.equal(shortTitle({project:'cognitive core',short_title:'Compare model scale'}),'Compare model scale');
  assert.equal(shortTitle({task:'Fix the composer. Preserve every saved run.'}),'Fix the composer.');
  const label=shortTitle({task:'Review '.repeat(80)});
  assert.ok(label.split(/\s+/).length<=8);
  assert.ok(Array.from(label).length<=65);
  assert.ok(!shortTitle({task:'漢'.repeat(200)}).includes('\n'));
});

function luminance(hex){
  const rgb=hex.match(/[a-f\d]{2}/gi).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);
  return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];
}
function contrast(a,b){const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}

test('both palettes keep button text and placeholders above the 4.5 contrast target',()=>{
  for(const theme of ['light','dark']){
    const rule=page.match(new RegExp('html\\[data-theme='+theme+'\\] \\{([^}]+)\\}'))[1];
    const tokens=Object.fromEntries([...rule.matchAll(/--([\w-]+):\s*(#[a-f\d]{6})/gi)].map(x=>[x[1],x[2]]));
    for(const [foreground,background] of [['ink','raised'],['panel','ink'],['quiet','raised'],['quiet','panel']]){
      assert.ok(contrast(tokens[foreground],tokens[background])>=4.5,theme+': '+foreground+' / '+background);
    }
    assert.match(rule,new RegExp('color-scheme:\\s*'+theme));
  }
});
