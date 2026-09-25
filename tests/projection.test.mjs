import test from 'node:test';
import assert from 'node:assert/strict';
import {sourcePixel,reproject,samePose} from '../fs_capture/preview_assets/projection.mjs';
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-10,`${a} != ${b}`);
test('principal point, anisotropic focal and fisheye theta polynomial',()=>{
 const camera={modelId:5,params:[700,800,900,950,.01,-.002,.0003,.00001]};
 assert.deepEqual(sourcePixel(camera,0,0),[900,950]);
 const theta=Math.PI/4,t=theta*theta,rad=theta*(1+.01*t-.002*t*t+.0003*t**3+.00001*t**4);
 near(sourcePixel(camera,1,0)[0],900+700*rad);
 near(sourcePixel(camera,0,-1)[1],950-800*rad);
});
test('pinhole projection and calibrated ray round trip',()=>{
 for(const [x,y]of [[0,0],[.3,-.2],[-1,1]]){
  const p=sourcePixel({modelId:1,params:[300,400,500,600]},x,y);
  near((p[0]-500)/300,x);near((p[1]-600)/400,y);
 }
 assert.deepEqual(sourcePixel({modelId:0,params:[100,50,60]},1,-1),[150,-40]);
 assert.throws(()=>sourcePixel({modelId:3,params:[]},0,0));
});
test('pixel-centre mapping, orientation and mask nearest-neighbour',()=>{
 const data=new Uint8ClampedArray(4*4*4);
 for(let y=0;y<4;y++)for(let x=0;x<4;x++)data.set([x*50,y*50,0,255],4*(y*4+x));
 const camera={modelId:1,params:[2,2,2,2],width:4,height:4}, projection={width:4,height:4,fovDegrees:90};
 const out=reproject({width:4,height:4,data},camera,projection,true);
 assert.deepEqual(Array.from(out.slice(20,24)),[50,50,0,255]);
 assert.deepEqual(Array.from(out.slice(24,28)),[100,50,0,255]);
 assert.throws(()=>reproject({width:3,height:4,data},camera,projection));
});

test('roll and translation prevent claims of matching source projection',()=>{
 const a={position:[0,0,0],forward:[0,0,1],up:[0,-1,0]};
 assert.ok(samePose(a,a));
 assert.ok(!samePose(a,{...a,up:[0,1,0]}));
 assert.ok(!samePose(a,{...a,position:[.1,0,0]}));
});
