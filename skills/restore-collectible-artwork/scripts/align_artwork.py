"""Non-generative, trial-grade registration of an edited flat artwork to its source.

NumPy/Pillow only. The reference supplies geometry; output RGB comes solely
from the edited artwork. No generative calls or tone changes. New outputs only.
Trial-grade alignment; internal composition still requires visual review.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

W, H = 1000, 1550

def box_mean(a, radius):
    p = np.pad(a, radius, mode='edge')
    p = np.pad(p.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    n = 2 * radius + 1
    return (p[n:, n:] - p[:-n, n:] - p[n:, :-n] + p[:-n, :-n]) / n**2

def match(ref, moving, point, center, radius, search):
    x, y = map(lambda v: int(round(v)), point)
    cx, cy = map(lambda v: int(round(v)), center)
    p = ref[y-radius:y+radius+1, x-radius:x+radius+1]
    area = moving[cy-radius-search:cy+radius+search+1,
                  cx-radius-search:cx+radius+search+1]
    if p.shape != (2*radius+1,)*2 or area.shape != (2*(radius+search)+1,)*2:
        return None
    p = p - p.mean()
    norm = np.sqrt(np.sum(p*p))
    if norm < 1e-6:
        return None
    windows = np.lib.stride_tricks.sliding_window_view(area, p.shape)
    sums = np.einsum('ijkl->ij', windows, optimize=True)
    squared = np.einsum('ijkl,ijkl->ij', windows, windows, optimize=True)
    norms = np.sqrt(np.maximum(1e-9, squared-sums*sums/p.size))
    corr = np.einsum('ijkl,kl->ij', windows, p, optimize=True) / (norm*norms)
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)
    return np.array([cx+ix-search, cy+iy-search], float), float(corr[iy, ix])

def summary(errors):
    a = np.asarray(errors)
    return {'count': len(a), 'median_px': float(np.median(a)),
            'p90_px': float(np.percentile(a, 90)), 'max_px': float(a.max())}

def fit_affine(points, displacements):
    design = np.column_stack((points / np.array([W, H]), np.ones(len(points))))
    weights = np.ones(len(points))
    for _ in range(8):
        coef = np.linalg.lstsq(design*weights[:, None]**.5,
                               displacements*weights[:, None]**.5, rcond=None)[0]
        residual = np.linalg.norm(design@coef-displacements, axis=1)
        weights = np.minimum(1, 4/np.maximum(residual, .001))
    return coef

def kernel(a, b):
    squared = np.sum((a[:, None, :]-b[None, :, :])**2, axis=2)
    return squared*np.log(squared+1e-12)

def fit_smooth_field(points, displacement, confidence, affine):
    # Weak affine priors stabilize unconstrained image edges. Fixing every
    # edge to zero displaced nearby artwork, so the final warp does not do it.
    pins = []
    for x in np.linspace(0, W, 9):
        pins.extend([[x, 0], [x, H]])
    for y in np.linspace(0, H, 14)[1:-1]:
        pins.extend([[0, y], [W, y]])
    pins = np.asarray(pins)
    controls = np.vstack([points, pins])/1000
    prior = np.column_stack([pins/[W, H], np.ones(len(pins))])@affine
    values = np.vstack([displacement, prior])
    weights = np.r_[np.clip((confidence-.89)/.1, .1, 1), np.full(len(pins), .35)]
    polynomial = np.column_stack([np.ones(len(controls)), controls])
    matrix = np.block([[kernel(controls, controls)+np.diag(.003/weights), polynomial],
                       [polynomial.T, np.zeros((3, 3))]])
    coefficients = np.linalg.solve(matrix, np.vstack([values, np.zeros((3, 2))]))

    def field(p):
        p = np.asarray(p)
        normalized = p/1000
        basis = np.column_stack([kernel(normalized, controls), np.ones(len(p)), normalized])
        return basis@coefficients

    return field, controls, coefficients

def measure_patches(ref, target, points, search=10):
    errors, scores = [], []
    for p in points:
        result = match(ref, target, p, p, 32, search)
        if result is not None:
            errors.append(np.linalg.norm(result[0]-p))
            scores.append(result[1])
    return {**summary(errors), 'median_correlation': float(np.median(scores)),
            'matches_below_0_90': int(np.sum(np.array(scores)<.90))}


def pairs(ref, moving):
    r = np.asarray(Image.fromarray(ref.astype('uint8')).resize((W//2,H//2), Image.Resampling.LANCZOS),float)
    m = np.asarray(Image.fromarray(moving.astype('uint8')).resize((W//2,H//2), Image.Resampling.LANCZOS),float)
    gy,gx = np.gradient(r)
    xx,yy,xy = box_mean(gx*gx,3),box_mean(gy*gy,3),box_mean(gx*gy,3)
    corner = (xx*yy-xy*xy)/(xx+yy+1e-6)
    records=[]
    for row,y in enumerate(range(55,H//2-45,35)):
        for col,x in enumerate(range(50,W//2-40,35)):
            local=corner[y-10:y+11,x-10:x+11]
            iy,ix=np.unravel_index(np.argmax(local),local.shape)
            if local[iy,ix]<.4:continue
            p=np.array([x+ix-10,y+iy-10],float)
            coarse=match(r,m,p,p,16,24)
            if coarse is None or coarse[1]<.89:continue
            p=p*2
            fine=match(ref,moving,p,coarse[0]*2,32,6)
            if fine is None or fine[1]<.90:continue
            q,score=fine
            backward=match(moving,ref,q,p,32,6)
            if backward is None or np.linalg.norm(backward[0]-p)>2.5:continue
            records.append({'reference':p.tolist(),'artwork':q.tolist(),'score':score,
                            'held_out':(row+2*col)%4==0})
    return records


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_report(folder, report):
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')


def run(args):
    global H
    ref_path, art_path=Path(args.reference),Path(args.artwork)
    reference=ImageOps.exif_transpose(Image.open(ref_path)).convert('RGBA')
    artwork=ImageOps.exif_transpose(Image.open(art_path)).convert('RGBA')
    if reference.size!=artwork.size:
        raise ValueError('Input sizes differ; normalize the inspected artwork body first. Do not silently stretch it.')
    H=round(W*reference.height/reference.width)
    if not 256<=H<=3000:
        raise ValueError('Aspect outside tested analysis limits; use a format-specific/manual method')
    folder=Path(args.out_dir)
    folder.mkdir(parents=True,exist_ok=False)
    initial={'reference':sha(ref_path),'artwork':sha(art_path)}
    def analysis(im):
        return np.asarray(im.convert('L').resize((W,H),Image.Resampling.LANCZOS)
                          .filter(ImageFilter.GaussianBlur(1.5)),float)
    ref,moving=analysis(reference),analysis(artwork)
    records=pairs(ref,moving)
    report={'status':'needs_review','analysis_size':[W,H],
            'metric_units':'analysis pixels at 1000px width; not anatomical measurements',
            'input_sha256':initial,'generation_calls':0,'correspondences':records,
            'rgb_source':'edited artwork only; reference used for coordinates and alpha'}
    def stop(reason):
        report['status']='needs_manual_alignment'
        report['reason']=reason
        write_report(folder,report)
        raise ValueError(reason)
    if len(records)<40:stop('Too few reliable matches; do not force a warp or claim alignment')
    p=np.array([v['reference'] for v in records]);q=np.array([v['artwork'] for v in records])
    held=np.array([v['held_out'] for v in records]);scores=np.array([v['score'] for v in records])
    if held.sum()<10 or (~held).sum()<25:stop('Insufficient held-out/training coverage')
    if (np.ptp(p,axis=0)/[W,H]<.45).any():stop('Matches cover too little of the image')
    before=summary(np.linalg.norm(q[held]-p[held],axis=1))
    affine=fit_affine(p[~held],(q-p)[~held])
    design=np.column_stack([p/[W,H],np.ones(len(p))])
    residual=np.linalg.norm(design@affine-(q-p),axis=1)
    eligible=(~held)&(residual<12)
    if eligible.sum()<25:stop('Too few geometrically consistent training matches')
    affine=fit_affine(p[eligible],(q-p)[eligible])
    affine_error=summary(np.linalg.norm((design@affine-(q-p))[held],axis=1))
    report.update(before=before,affine_held_out=affine_error,training_controls=int(eligible.sum()),
                  held_out_count=int(held.sum()),excluded_training_indices=np.flatnonzero((~held)&(~eligible)).tolist())
    if args.mode=='check':
        report['status']='geometry_check_only'
        write_report(folder,report)
        print(json.dumps({k:v for k,v in report.items() if k!='correspondences'},indent=2))
        return
    mode=args.mode
    if mode=='auto':
        if before['median_px']<=.75 and before['p90_px']<=1.5:mode='identity'
        elif affine_error['median_px']<=1 and affine_error['p90_px']<=2:mode='affine'
        else:mode='local'
    if mode=='local':
        field,controls,coefficients=fit_smooth_field(p[eligible],(q-p)[eligible],scores[eligible],affine)
    elif mode=='affine':
        def field(points):return np.column_stack([points/[W,H],np.ones(len(points))])@affine
    else:
        def field(points):return np.zeros_like(points)
    xs=np.r_[np.arange(0,W,24),W].astype(float)
    ys=np.r_[np.arange(0,H,24),H].astype(float)
    xx,yy=np.meshgrid(xs,ys)
    delta=field(np.c_[xx.ravel(),yy.ravel()]).reshape(len(ys),len(xs),2)
    mapped=np.dstack([xx,yy])+delta
    ux_y,ux_x=np.gradient(mapped[:,:,0],ys,xs)
    uy_y,uy_x=np.gradient(mapped[:,:,1],ys,xs)
    det=ux_x*uy_y-ux_y*uy_x
    report.update(mode=mode,jacobian_determinant={'min':float(det.min()),'max':float(det.max())})
    if det.min()<.65 or det.max()>1.5:stop('Excessive local deformation/folding; preserve inputs and inspect manually')
    outside={'left':max(0.,-float(mapped[:,:,0].min())),
             'right':max(0.,float(mapped[:,:,0].max())-W),
             'top':max(0.,-float(mapped[:,:,1].min())),
             'bottom':max(0.,float(mapped[:,:,1].max())-H)}
    report['edge_extension_analysis_px']=outside
    if max(outside.values())>W*args.max_edge_fraction:stop('Edge sampling exceeds configured limit; no broad filling is permitted')
    sx,sy=reference.width/W,reference.height/H
    if mode=='identity':
        aligned=artwork.copy()
    else:
        padding=int(np.ceil(max(outside['left']*sx,outside['right']*sx,
                                outside['top']*sy,outside['bottom']*sy)))+4
        rgb=Image.fromarray(np.pad(np.asarray(artwork.convert('RGB')),
                                   ((padding,padding),(padding,padding),(0,0)),mode='edge'))
        native_x=np.rint(xs*sx).astype(int);native_y=np.rint(ys*sy).astype(int)
        mesh=[]
        for j in range(len(ys)-1):
            for i in range(len(xs)-1):
                box=(native_x[i],native_y[j],native_x[i+1],native_y[j+1])
                if box[0]==box[2] or box[1]==box[3]:continue
                quad=np.array([mapped[j,i],mapped[j+1,i],mapped[j+1,i+1],mapped[j,i+1]])*[sx,sy]+padding
                mesh.append((box,tuple(quad.ravel())))
        aligned=rgb.transform(reference.size,Image.Transform.MESH,mesh,Image.Resampling.BICUBIC).convert('RGBA')
    aligned.putalpha(reference.getchannel('A'))
    after=measure_patches(ref,analysis(aligned),p[held])
    report['after_held_out']=after
    if after['matches_below_0_90']>max(2,int(held.sum()*.1)):
        stop('Too many unreliable validation patches after correction')
    if mode!='identity' and after['p90_px']>max(3.5,before['p90_px']*.65):
        stop('Held-out alignment did not improve enough; do not silently accept')
    if initial!={'reference':sha(ref_path),'artwork':sha(art_path)}:stop('Inputs changed during processing')
    aligned.save(folder/'aligned.png')
    # Diagnostic files do not affect matching, validation or the output pixels.
    # Existing callers retain them; the local pipeline can skip their PNG I/O.
    if getattr(args, 'diagnostics', True):
        Image.blend(reference,artwork,.5).save(folder/'before-overlay.png')
        Image.blend(reference,aligned,.5).save(folder/'after-overlay.png')
        np.savez(folder/'field.npz',analysis_x=xs,analysis_y=ys,inverse_displacement=delta)
    report.update(status='candidate_requires_visual_review',size=list(aligned.size),
                  same_alpha=bool(np.array_equal(np.asarray(aligned.getchannel('A')),np.asarray(reference.getchannel('A')))),
                  input_hashes_unchanged=True,
                  edge_note='Narrow outside samples repeat existing artwork edge colors, never recovered hidden detail.')
    write_report(folder,report)
    print(json.dumps({k:v for k,v in report.items() if k!='correspondences'},indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',required=True)
    parser.add_argument('--artwork',required=True)
    parser.add_argument('--out-dir',required=True)
    parser.add_argument('--mode',choices=['check','auto','affine','local'],default='auto')
    parser.add_argument('--max-edge-fraction',type=float,default=.015)
    parser.add_argument('--no-diagnostics',dest='diagnostics',action='store_false',
                        help='Skip overlay PNGs and field.npz; keep all geometry checks and aligned pixels')
    args=parser.parse_args()
    if not 0<=args.max_edge_fraction<=.05:parser.error('max-edge-fraction must be 0..0.05; larger borders require a different method')
    try:run(args)
    except (ValueError,FileExistsError,np.linalg.LinAlgError) as exc:parser.exit(2,f'{exc}\n')


if __name__=='__main__':main()
