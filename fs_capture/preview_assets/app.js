import {Renderer} from './runtime/viewer/js/renderer.js';
import {Camera} from './runtime/viewer/js/camera.js';
import {initWasm, loadModelFromUrl, sortSplats, freeSplatSh} from './runtime/viewer/js/wasm.js';
import {reproject, samePose} from './projection.mjs';
const $=id=>document.getElementById(id);
const json=async url=>{const r=await fetch(url);if(!r.ok)throw new Error(`${url}: ${r.status}`);return r.json();};
let renderer,camera,sidecar,scene,bundle,records=[],current,referencePixels,maskPixels;
let selection=0,loaded=false,assessmentKey=null,confirmedAt=null,restoredFrom=null;
const opts={primitive:0,gamut:[1,0,0,0,1,0,0,0,1],transfer:0,isLinear:false,shDegree:3,exposure:1,opacityScale:1,upAxis:'z',showGrid:false,background:[0,0,0]};
function render(){const f=camera.forward(),p=camera.pos;renderer.updateSplatOrder(sortSplats(0,...p,...f));renderer.render(camera,opts);}
function viewpoint(){return {cameraId:current.id,position:[...camera.pos],forward:camera.forward(),up:camera.up(),projection:sidecar.projection};}
function contextKey(){return JSON.stringify({bundle:bundle.id,view:viewpoint(),renderer:opts,status:$('result').value});}
function clearExport(){const a=$('download');if(a.href.startsWith('blob:'))URL.revokeObjectURL(a.href);a.removeAttribute('href');a.hidden=true;$('export-json').value='';$('save-message').textContent='';}
function invalidate(message){assessmentKey=null;confirmedAt=null;restoredFrom=null;$('confirmed').checked=false;$('result').value='NOT_TESTED';$('assessment-context').textContent=message+' 判定は未確認です。理由・確認箇所は下書きです。';clearExport();}
function reference(){
  if(!referencePixels)return;
  const p=sidecar.projection,ctx=$('reference').getContext('2d'),pixels=new Uint8ClampedArray(referencePixels);
  if(maskPixels&&$('overlay').checked)for(let i=0;i<pixels.length;i+=4)if(maskPixels[i]===0){pixels[i]=Math.round(pixels[i]*.5+127);pixels[i+1]*=.5;pixels[i+2]*=.5;}
  ctx.putImageData(new ImageData(pixels,p.width,p.height),0,0);
  $('reference-label').textContent=samePose(viewpoint(),current)?'同じ位置・向き・透視投影に変換した元画像（学習入力）':'参考画像：元の撮影位置・向き。現在の視点の正解画像ではありません';
}
async function pixels(url){const im=new Image();im.src=url;await im.decode();const cv=document.createElement('canvas');cv.width=im.naturalWidth;cv.height=im.naturalHeight;const ctx=cv.getContext('2d');ctx.drawImage(im,0,0);return ctx.getImageData(0,0,cv.width,cv.height);}
async function select(id){
  const generation=++selection;loaded=false;invalidate('撮影位置を選択しました。');
  current=sidecar.views.find(v=>v.id===Number(id));if(!current)throw new Error('撮影位置がありません');
  $('capture').value=String(current.id);camera.lookAt(current.position,current.position.map((v,i)=>v+current.forward[i]),current.up);render();
  $('original').href=current.imageUrl;$('mask').hidden=!current.maskUrl;$('overlay').disabled=!current.maskUrl;
  $('mask-status').textContent=current.maskUrl?'復元用マスク適用（公開審査とは別）':'マスク未適用';
  if(current.maskUrl)$('mask').href=current.maskUrl;else{$('mask').removeAttribute('href');$('overlay').checked=false;}
  referencePixels=null;maskPixels=null;$('reference').getContext('2d').clearRect(0,0,$('reference').width,$('reference').height);
  $('reference-label').textContent='対応する元画像を読み込んでいます…';
  const selected=current,[im,mask]=await Promise.all([pixels(selected.imageUrl),selected.maskUrl?pixels(selected.maskUrl):null]);
  if(generation!==selection)return;
  referencePixels=reproject(im,selected.intrinsics,sidecar.projection);maskPixels=mask?reproject(mask,selected.intrinsics,sidecar.projection,true):null;reference();loaded=true;return true;
}
function safely(fn){return async(...args)=>{try{await fn(...args);}catch(e){clearExport();$('status').textContent='エラー: '+e.message;}};}
function moved(){invalidate('視点を変更しました。');render();reference();}
async function boot(){
  [scene,sidecar,bundle]=await Promise.all([json('scene.json'),json('cameras.json'),json('bundle.json')]);
  const {width,height,fovDegrees}=sidecar.projection;for(const id of ['model','reference']){$(id).width=width;$(id).height=height;}
  await initWasm();renderer=new Renderer($('model'));camera=new Camera();camera.fov=fovDegrees*Math.PI/180;camera.model='perspective';
  const model=await loadModelFromUrl('model.ply','model.ply');if(model.kind!=='splat')throw new Error('Gaussian PLY is required');
  if(renderer.setSplat(model.data)==='oom-sh')throw new Error('GPU memory不足。表示条件を自動変更せず停止しました。');
  opts.shDegree=model.data.shDegree;if(opts.shDegree!==scene.renderer.shDegree)throw new Error('SH degree differs from model metadata');freeSplatSh();
  for(const v of sidecar.views){const o=document.createElement('option');o.value=v.id;o.textContent=`${v.source.captureId||'single'} / ${v.source.cameraId} / frame ${v.source.frameIndex}`;$('capture').append(o);}
  await select(sidecar.views[0].id);$('status').textContent=`描画完了 · ${model.data.count.toLocaleString()} Gaussian · 画質は視点別記録を参照（未記録は NOT_TESTED）`;
  $('metadata').textContent=JSON.stringify({modelSha256:scene.modelSha256,cameraSha256:scene.cameraSha256,renderer:scene.renderer,shDegree:opts.shDegree,projection:sidecar.projection,units:sidecar.units,coordinateTransform:sidecar.worldFromReconstruction},null,2);
  for(const id of ['capture','home','left','right','forward','back','look-left','look-right','look-up','look-down','save'])$(id).disabled=false;
  $('capture').onchange=safely(()=>select($('capture').value));$('home').onclick=safely(()=>select(sidecar.views[0].id));$('overlay').onchange=reference;
  for(const [id,axis,sign] of [['left','right',-1],['right','right',1],['forward','forward',1],['back','forward',-1]])$(id).onclick=safely(()=>{
    const step=Number($('step').value);if(!Number.isFinite(step)||step<=0||step>100)throw new Error('移動量を確認してください');
    const d=camera[axis]();camera.pos=camera.pos.map((v,i)=>v+d[i]*step*sign);moved();
  });
  for(const [id,axis,sign] of [['look-left','right',-1],['look-right','right',1],['look-up','up',1],['look-down','up',-1]])$(id).onclick=safely(()=>{
    const a=Math.PI/12,f=camera.forward(),d=camera[axis](),up=camera.up(),next=f.map((v,i)=>v*Math.cos(a)+d[i]*Math.sin(a)*sign);
    const nextUp=axis==='up'?up.map((v,i)=>v*Math.cos(a)-f[i]*Math.sin(a)*sign):up;camera.lookAt(camera.pos,camera.pos.map((v,i)=>v+next[i]),nextUp);moved();
  });
  $('result').onchange=()=>{assessmentKey=null;confirmedAt=null;$('confirmed').checked=false;clearExport();};
  for(const id of ['label','purpose','reviewer','reason'])$(id).oninput=$('result').onchange;
  $('confirmed').onchange=safely(()=>{
    if(!loaded){$('confirmed').checked=false;throw new Error('対応画像の読み込み完了を待ってください');}
    assessmentKey=$('confirmed').checked?contextKey():null;confirmedAt=assessmentKey?new Date().toISOString():null;clearExport();
    if(assessmentKey)$('assessment-context').textContent='現在の視点について、新しい判定の確認操作を受け付けました。まだファイル保存・取り込みはしていません。';
  });
  $('save').onclick=safely(()=>{
    if(!loaded||!$('confirmed').checked||assessmentKey!==contextKey())throw new Error('現在の視点・判定を確認し、確認欄をチェックしてください');
    for(const id of ['label','purpose','reviewer','reason'])if(!$(id).value.trim())throw new Error('確認箇所・用途・確認者・理由を入力してください');
    render();const record={schemaVersion:1,bundleId:bundle.id,category:'visual',status:$('result').value,label:$('label').value,purpose:$('purpose').value,reviewer:$('reviewer').value,reason:$('reason').value,at:new Date().toISOString(),confirmedAt,restoredFrom,device:navigator.userAgent,view:viewpoint(),shDegree:opts.shDegree,screenshot:$('model').toDataURL('image/png')};
    clearExport();const text=JSON.stringify(record),a=$('download');a.href=URL.createObjectURL(new Blob([text],{type:'application/json'}));a.download=`fs-review-${Date.now()}.json`;a.hidden=false;$('export-json').value=text;
    $('save-message').textContent='保存用JSONを作成しました（未取り込み）。リンクからファイルを保存し、python -m fs_capture preview-import PREVIEW 保存したJSON を実行してください。保存完了は保存先で確認してください。';
  });
  $('download').onclick=()=>{$('save-message').textContent='ブラウザへ保存を要求しました。保存先の実ファイルを確認し、CLIで取り込んでください。この画面では保存完了を判定しません。';};
  records=(await json('qa.json')).items;for(const [i,r]of records.entries()){const o=document.createElement('option');o.value=i;o.textContent=`${r.status} · ${r.label} · ${r.reviewer}`;$('saved').append(o);}
  $('restore').disabled=!records.length;$('review-summary').textContent=`取り込み済み確認記録 ${records.length}件。判定は記録した用途・視点に限ります。移動範囲・公開情報・納品は NOT_TESTED。`;
  $('restore').onclick=safely(async()=>{
    if($('saved').value==='')return;const r=records[Number($('saved').value)];if(!await select(r.view.cameraId))return;
    camera.lookAt(r.view.position,r.view.position.map((v,i)=>v+r.view.forward[i]),r.view.up);moved();restoredFrom=r.id;
    for(const id of ['label','purpose','reviewer','reason'])$(id).value=r[id];
    $('assessment-context').textContent=`過去の判定 ${r.status}（${r.reviewer}）の視点を復元しました。入力は下書きです。新しい判定は未確認です。`;
  });
}
safely(boot)();
