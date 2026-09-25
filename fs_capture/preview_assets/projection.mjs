// COLMAP pixel coordinates: centres at (column + .5, row + .5).
// Only source projections verified against the pinned Spirula models are supported.
export function sourcePixel(camera, x, y) {
  let fx, fy, cx, cy, k = [];
  if (camera.modelId === 0) [fx, cx, cy] = camera.params, fy = fx;
  else if ([1, 5].includes(camera.modelId)) [fx, fy, cx, cy, ...k] = camera.params;
  else throw new Error('Unsupported source camera model');
  if (camera.modelId === 5) {
    const r = Math.hypot(x, y), theta = Math.atan(r), t2 = theta * theta;
    const scale = r < 1e-12 ? 1 : theta * (1 + t2 * (k[0] + t2 * (k[1] + t2 * (k[2] + t2 * k[3])))) / r;
    x *= scale; y *= scale;
  }
  return [fx*x+cx, fy*y+cy];
}

export function reproject(source, camera, projection, mask = false) {
  const {width:w, height:h, fovDegrees} = projection;
  if (source.width !== camera.width || source.height !== camera.height) throw new Error('Source image / calibration dimension mismatch');
  const out = new Uint8ClampedArray(w*h*4), f = h/2/Math.tan(fovDegrees*Math.PI/360);
  for (let j=0;j<h;j++) for (let i=0;i<w;i++) {
    const [u,v] = sourcePixel(camera, (i+.5-w/2)/f, (j+.5-h/2)/f);
    const x=u-.5, y=v-.5, x0=Math.floor(x), y0=Math.floor(y), o=4*(j*w+i);
    out[o+3]=255;
    if (x0<0 || y0<0 || x0+1>=source.width || y0+1>=source.height) continue;
    for(let c=0;c<3;c++) {
      if (mask) out[o+c]=source.data[4*(Math.round(y)*source.width+Math.round(x))+c];
      else { const a=x-x0,b=y-y0;
        out[o+c]=(1-b)*((1-a)*source.data[4*(y0*source.width+x0)+c]+a*source.data[4*(y0*source.width+x0+1)+c])
          +b*((1-a)*source.data[4*((y0+1)*source.width+x0)+c]+a*source.data[4*((y0+1)*source.width+x0+1)+c]);
      }
    }
  }
  return out;
}

export function samePose(a,b) {
  return ['position','forward','up'].every(key=>a[key].every((value,i)=>Math.abs(value-b[key][i])<1e-8));
}
