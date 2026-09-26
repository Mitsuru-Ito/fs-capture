// Execute the real UI module with DOM/rendering doubles. No browser/GPU/quality claim.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

async function ui({masked=true,saved=[],comparison=false,failCandidate=false,failures={},hold={}}={}) {
  const calls={models:[],images:[],renderers:0,reloads:0};
  const remaining={...failures};
  async function resource(url){if(hold[url])await hold[url];if(remaining[url]>0){remaining[url]--;throw Error(`fixture failure: ${url}`);}}
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{value:({result:'NOT_TESTED',step:'0.1',saved:''})[id]??'',checked:false,disabled:false,hidden:true,href:'',textContent:'',width:128,height:128,
    append(){},removeAttribute(name){this[name]='';},getContext(){return {clearRect(){},drawImage(){},putImageData(){},getImageData(){return {width:128,height:128,data:new Uint8ClampedArray(128*128*4)};}}},toDataURL(){return 'data:image/png;base64,fixture';}});return elements.get(id);};
  const views=[1,2].map(id=>({id,position:[id,0,0],forward:[0,0,1],up:[0,-1,0],intrinsics:{},source:{cameraId:'cam0',frameIndex:id},imageUrl:`images/${id}.jpg`,maskUrl:masked?`masks/${id}.png`:null}));
  const projection={width:128,height:128,fovDegrees:90,model:'perspective'};
  const scene={renderer:{shDegree:0},modelSha256:'fixture',masking:masked?'APPLIED':'NOT_APPLIED'};
  if(comparison)scene.comparison={candidates:[{id:'A',url:'model.ply',modelSha256:'model-a',sourceBundleId:'a',training:{iterations:1}},{id:'B',url:'candidate-b.ply',modelSha256:'model-b',sourceBundleId:'b',training:{iterations:2}}]};
  const urls={'scene.json':scene,'cameras.json':{views,projection},'bundle.json':{id:'fixture-bundle'},'qa.json':{items:saved},'viewpoints.json':{items:[]}};
  class Camera {lookAt(p,t,u){this.pos=[...p];const f=t.map((v,i)=>v-p[i]),n=Math.hypot(...f);this.f=f.map(v=>v/n);this.u=[...u];}forward(){return [...this.f];}up(){return [...this.u];}right(){return [1,0,0];}}
  class Image {set src(url){if(!url)throw Error('Missing image URL');this.url=url;}async decode(){calls.images.push(this.url);await resource(this.url);}naturalWidth=128;naturalHeight=128;}
  const context=vm.createContext({console,document:{getElementById:element,createElement:()=>element(`created-${elements.size}`)},fetch:async url=>{await resource(url);return {ok:true,json:async()=>urls[url]};},navigator:{userAgent:'DOM TEST ONLY'},
    Image,ImageData:class{},Blob,URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},Uint8ClampedArray,location:{reload(){calls.reloads++;}},
    Renderer:class {constructor(){calls.renderers++;}setSplat(){}updateSplatOrder(){}render(){}},Camera,initWasm:()=>resource('runtime'),loadModelFromUrl:async url=>{calls.models.push(url);await resource(url);if(failCandidate&&url==='candidate-b.ply')throw Error('fixture load failure');return {kind:'splat',data:{shDegree:0,count:1}};},sortSplats(){},freeSplatSh(){},freeSplat(){},reproject:()=>new Uint8ClampedArray(128*128*4),samePose:()=>true});
  const code=readFileSync(new URL('../fs_capture/preview_assets/app.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'');
  vm.runInContext(code,context);
  const settle=async()=>{for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));};
  await settle();
  const click=async id=>{await element(id).onclick();};
  async function confirm(){element('confirmed').checked=true;await element('confirmed').onchange?.();}
  async function judge(status='PASS'){for(const [id,value] of Object.entries({label:'Aのみ',purpose:'test',reviewer:'reviewer',reason:'Aだけを確認',result:status}))element(id).value=value;await element('result').onchange?.();await confirm();}
  return {element,click,confirm,judge,views,projection,calls,settle};
}
test('unmasked preview boots and disables mask controls',async()=>{const t=await ui({masked:false});assert.equal(t.element('overlay').disabled,true);assert.equal(t.element('mask').hidden,true);assert.match(t.element('mask-status').textContent,/未適用/);});
for(const action of ['capture','home','right','look-left'])test(`new ${action} context cannot inherit PASS`,async()=>{
  const t=await ui();await t.judge();await t.click('save');assert.equal(JSON.parse(t.element('export-json').value).status,'PASS');
  if(action==='capture'){t.element('capture').value='2';await t.element('capture').onchange();}else await t.click(action);
  assert.equal(t.element('result').value,'NOT_TESTED');assert.equal(t.element('confirmed').checked,false);
  assert.equal(t.element('export-json').value,'');assert.equal(t.element('download').hidden,true);
  assert.equal(t.element('reviewer').value,'reviewer');assert.equal(t.element('purpose').value,'test');
  await t.click('save');assert.equal(t.element('export-json').value,'');
  await t.judge('FAIL');await t.click('save');assert.equal(JSON.parse(t.element('export-json').value).status,'FAIL');
});
test('restoring a historical judgement is not a fresh assessment',async()=>{
  const base={id:'past',status:'PASS',label:'old',purpose:'test',reviewer:'earlier',reason:'past only',view:{cameraId:2,position:[2,0,0],forward:[0,0,1],up:[0,-1,0]}};
  const t=await ui({saved:[base]});t.element('saved').value='0';await t.click('restore');
  assert.equal(t.element('result').value,'NOT_TESTED');assert.match(t.element('assessment-context').textContent,/過去.*PASS/);
  await t.click('save');assert.equal(t.element('export-json').value,'');
  await t.judge('FAIL');await t.click('save');assert.equal(JSON.parse(t.element('export-json').value).restoredFrom,'past');
});
test('a verdict requires explicit confirmation even with complete text',async()=>{const t=await ui();await t.judge();t.element('confirmed').checked=false;await t.element('confirmed').onchange?.();await t.click('save');assert.equal(t.element('export-json').value,'');});
test('A/B keeps the same pose and requires independent assessment',async()=>{
  const t=await ui({comparison:true});await t.click('right');await t.click('look-left');await t.judge('FAIL');await t.click('save');
  const a=JSON.parse(t.element('export-json').value);assert.equal(a.candidateId,'A');
  t.element('candidate').value='B';await t.element('candidate').onchange();
  assert.equal(t.element('result').value,'NOT_TESTED');await t.click('save');assert.equal(t.element('export-json').value,'');
  await t.judge('FAIL');await t.click('save');const b=JSON.parse(t.element('export-json').value);
  assert.deepEqual(b.view,a.view);assert.equal(b.schemaVersion,2);assert.equal(b.candidateId,'B');assert.equal(b.modelSha256,'model-b');assert.equal(b.sourceBundleId,'b');
});
test('candidate loading failure cannot save the previous image as the next model',async()=>{
  const t=await ui({comparison:true,failCandidate:true});await t.judge();await t.click('save');
  t.element('candidate').value='B';await t.element('candidate').onchange();
  assert.equal(t.element('model').hidden,true);await t.judge();await t.click('save');assert.equal(t.element('export-json').value,'');
});
test('normal startup binds controls once and needs no retry',async()=>{
  const t=await ui({comparison:true});
  for(const id of ['candidate','capture'])assert.equal(typeof t.element(id).onchange,'function');
  assert.equal(t.element('save').disabled,false);assert.equal(t.element('retry').hidden,true);
  assert.equal(t.calls.renderers,1);
});
test('initial model failure can retry without reloading the page or duplicating handlers',async()=>{
  const t=await ui({comparison:true,failures:{'model.ply':1}});
  assert.equal(typeof t.element('retry').onclick,'function');assert.equal(t.element('retry').hidden,false);
  assert.match(t.element('status').textContent,/モデル/);assert.equal(t.element('save').disabled,true);
  await t.judge();await t.click('save');assert.equal(t.element('export-json').value,'');
  const handler=t.element('candidate').onchange;await t.click('retry');
  assert.equal(t.element('save').disabled,false);assert.equal(t.element('retry').hidden,true);
  assert.equal(t.element('candidate').onchange,handler);assert.equal(t.calls.renderers,1);
  assert.deepEqual(t.calls.models,['model.ply','model.ply']);
  t.element('candidate').value='B';await t.element('candidate').onchange();await t.judge();await t.click('save');
  assert.equal(JSON.parse(t.element('export-json').value).candidateId,'B');
});
test('initial reference failure retries the images without another model allocation',async()=>{
  const t=await ui({failures:{'images/1.jpg':1}});
  assert.equal(t.element('retry').hidden,false);assert.match(t.element('status').textContent,/画像/);
  assert.equal(t.element('save').disabled,true);await t.judge();await t.click('save');assert.equal(t.element('export-json').value,'');
  await t.click('retry');assert.equal(t.element('save').disabled,false);
  assert.deepEqual(t.calls.models,['model.ply']);assert.equal(t.calls.renderers,1);
  assert.equal(t.calls.images.filter(x=>x==='images/1.jpg').length,2);
  t.element('capture').value='2';await t.element('capture').onchange();await t.judge();await t.click('save');
  assert.equal(JSON.parse(t.element('export-json').value).view.cameraId,2);
});
test('a failed B switch retries B and never exports A under its label',async()=>{
  const t=await ui({comparison:true,failures:{'candidate-b.ply':1}});await t.judge();await t.click('save');
  t.element('candidate').value='B';await t.element('candidate').onchange();
  assert.equal(t.element('model').hidden,true);assert.equal(t.element('save').disabled,true);
  await t.judge();await t.click('save');assert.equal(t.element('export-json').value,'');
  await t.click('retry');assert.equal(t.element('result').value,'NOT_TESTED');await t.judge('FAIL');await t.click('save');
  const r=JSON.parse(t.element('export-json').value);assert.equal(r.candidateId,'B');assert.equal(r.modelSha256,'model-b');
  assert.deepEqual(t.calls.models,['model.ply','candidate-b.ply','candidate-b.ply']);assert.equal(t.calls.renderers,1);
});
test('pending initialization cannot navigate, save, or start overlapping model loads',async()=>{
  let release;const pending=new Promise(resolve=>{release=resolve;});
  const t=await ui({comparison:true,hold:{'model.ply':pending}});
  assert.equal(t.element('candidate').disabled,true);assert.equal(t.element('right').disabled,true);assert.equal(t.element('save').disabled,true);
  await t.click('right');t.element('candidate').value='B';await t.element('candidate').onchange();await t.click('save');
  assert.deepEqual(t.calls.models,['model.ply']);assert.equal(t.element('export-json').value,'');
  release();await t.settle();assert.equal(t.element('save').disabled,false);assert.equal(t.element('candidate').value,'A');
});
test('repeated retry clicks cannot overlap a pending retry',async()=>{
  const hold={},t=await ui({comparison:true,failures:{'candidate-b.ply':1},hold});
  t.element('candidate').value='B';await t.element('candidate').onchange();
  let release;hold['candidate-b.ply']=new Promise(resolve=>{release=resolve;});
  const loading=t.click('retry');await t.settle();assert.equal(t.element('retry').disabled,true);
  await t.click('retry');await t.judge();await t.click('save');assert.equal(t.element('export-json').value,'');
  assert.deepEqual(t.calls.models,['model.ply','candidate-b.ply','candidate-b.ply']);
  release();await loading;await t.judge('FAIL');await t.click('save');
  assert.equal(JSON.parse(t.element('export-json').value).candidateId,'B');assert.equal(t.calls.renderers,1);
});
test('a pending reference blocks candidate switches and applies only its selected view',async()=>{
  const hold={},t=await ui({comparison:true,hold});
  let release;hold['images/2.jpg']=new Promise(resolve=>{release=resolve;});
  t.element('capture').value='2';const loading=t.element('capture').onchange();await t.settle();
  assert.equal(t.element('candidate').disabled,true);assert.equal(t.element('save').disabled,true);
  await t.element('candidate').onchange();assert.deepEqual(t.calls.models,['model.ply']);
  release();await loading;await t.judge();await t.click('save');
  const r=JSON.parse(t.element('export-json').value);assert.equal(r.view.cameraId,2);assert.equal(r.candidateId,'A');
});
for(const failed of ['qa.json','runtime'])test(`initial ${failed} failure offers explicit reload with uninitialized controls disabled`,async()=>{
  const t=await ui({failures:{[failed]:1}});
  for(const id of ['candidate','capture','right','save'])assert.equal(t.element(id).disabled,true);
  assert.equal(t.element('retry').hidden,false);assert.match(t.element('retry').textContent,/再読み込み/);
  await t.click('right');await t.judge();await t.click('save');assert.equal(t.element('export-json').value,'');
  await t.click('retry');assert.equal(t.calls.reloads,1);assert.equal(t.calls.renderers,0);assert.deepEqual(t.calls.models,[]);
});
