// Execute the real UI module with DOM/rendering doubles. No browser/GPU/quality claim.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

async function ui({masked=true,saved=[]}={}) {
  const elements=new Map();
  const element=id=>{if(!elements.has(id))elements.set(id,{value:({result:'NOT_TESTED',step:'0.1',saved:''})[id]??'',checked:false,disabled:false,hidden:true,href:'',textContent:'',width:128,height:128,
    append(){},removeAttribute(name){this[name]='';},getContext(){return {clearRect(){},drawImage(){},putImageData(){},getImageData(){return {width:128,height:128,data:new Uint8ClampedArray(128*128*4)};}}},toDataURL(){return 'data:image/png;base64,fixture';}});return elements.get(id);};
  const views=[1,2].map(id=>({id,position:[id,0,0],forward:[0,0,1],up:[0,-1,0],intrinsics:{},source:{cameraId:'cam0',frameIndex:id},imageUrl:`images/${id}.jpg`,maskUrl:masked?`masks/${id}.png`:null}));
  const projection={width:128,height:128,fovDegrees:90,model:'perspective'};
  const scene={renderer:{shDegree:0},modelSha256:'fixture',masking:masked?'APPLIED':'NOT_APPLIED'};
  const urls={'scene.json':scene,'cameras.json':{views,projection},'bundle.json':{id:'fixture-bundle'},'qa.json':{items:saved}};
  class Camera {lookAt(p,t,u){this.pos=[...p];const f=t.map((v,i)=>v-p[i]),n=Math.hypot(...f);this.f=f.map(v=>v/n);this.u=[...u];}forward(){return [...this.f];}up(){return [...this.u];}right(){return [1,0,0];}}
  class Image {set src(url){if(!url)throw Error('Missing image URL');}async decode(){}naturalWidth=128;naturalHeight=128;}
  const context=vm.createContext({console,document:{getElementById:element,createElement:()=>element(`created-${elements.size}`)},fetch:async url=>({ok:true,json:async()=>urls[url]}),navigator:{userAgent:'DOM TEST ONLY'},
    Image,ImageData:class{},Blob,URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},Uint8ClampedArray,
    Renderer:class {setSplat(){}updateSplatOrder(){}render(){}},Camera,initWasm:async()=>{},loadModelFromUrl:async()=>({kind:'splat',data:{shDegree:0,count:1}}),sortSplats(){},freeSplatSh(){},reproject:()=>new Uint8ClampedArray(128*128*4),samePose:()=>true});
  const code=readFileSync(new URL('../fs_capture/preview_assets/app.js',import.meta.url),'utf8').replace(/^import .*;\n/gm,'');
  vm.runInContext(code,context);
  for(let i=0;i<20&&!element('save').onclick;i++)await new Promise(resolve=>setImmediate(resolve));
  assert.ok(element('save').onclick,element('status').textContent);
  const click=async id=>{await element(id).onclick();};
  async function confirm(){element('confirmed').checked=true;await element('confirmed').onchange?.();}
  async function judge(status='PASS'){for(const [id,value] of Object.entries({label:'Aのみ',purpose:'test',reviewer:'reviewer',reason:'Aだけを確認',result:status}))element(id).value=value;await element('result').onchange?.();await confirm();}
  return {element,click,confirm,judge,views,projection};
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
