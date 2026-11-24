#!/usr/bin/env python3
"""Seeded source-edge and shared-radius diagnostics. No generation or final crop.

Requires NumPy/Pillow. A rough quad identifies the intended object; candidates
are NOT automatic acceptance. All analysis uses the unmasked source photograph.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from prepare_artwork import image, output_size, new_folder, digest, rounded_mask


def sample(rgb, x, y):
    x, y = np.broadcast_arrays(x, y)
    x = np.clip(x, 0, rgb.shape[1]-1.001)
    y = np.clip(y, 0, rgb.shape[0]-1.001)
    ix, iy = x.astype(int), y.astype(int)
    fx, fy = (x-ix)[..., None], (y-iy)[..., None]
    return ((1-fy)*((1-fx)*rgb[iy, ix]+fx*rgb[iy, ix+1])
            + fy*((1-fx)*rgb[iy+1, ix]+fx*rgb[iy+1, ix+1]))


def candidate_edges(im, quad, search):
    rgb = np.asarray(im.filter(ImageFilter.GaussianBlur(.7)), dtype=float)
    lines, reports = [], []
    for a, b in zip(quad, np.roll(quad, -1, axis=0)):
        vector = b-a
        length = np.linalg.norm(vector)
        tangent = vector/length
        normal = np.array([-tangent[1], tangent[0]])
        t = np.linspace(.15, .85, 81)
        base = a+t[:, None]*vector
        offsets = np.arange(-search, search+.5, .5)
        p = base[:, None, :]+offsets[None, :, None]*normal
        contrast = np.linalg.norm(sample(rgb, p[..., 0]+normal[0]*1.5, p[..., 1]+normal[1]*1.5)
                                  - sample(rgb, p[..., 0]-normal[0]*1.5, p[..., 1]-normal[1]*1.5), axis=-1)
        # A straight ridge must persist along an edge; one strong hand/print
        # feature cannot alone pull the line. Nearby ridges are still ambiguous.
        strength = np.maximum(np.max(contrast, axis=1), 8)
        normalized = contrast/strength[:, None]
        best = None
        for slope in np.linspace(-search, search, 41):
            d = offsets[None, :]+(t[:, None]-.5)*slope
            ids = np.rint((d+search)*2).astype(int)
            valid = (ids >= 0) & (ids < len(offsets))
            values = normalized[np.arange(len(t))[:, None], np.clip(ids, 0, len(offsets)-1)]*valid
            score = np.mean(np.sort(values, axis=0)[12:-12], axis=0)
            score -= .04*np.abs(offsets)/search
            at = int(np.argmax(score))
            if best is None or score[at] > best[0]:
                best = (float(score[at]), float(offsets[at]), float(slope))
        _, center, slope = best
        predicted = center+(t-.5)*slope
        near = np.abs(offsets[None, :]-predicted[:, None]) <= 2.5
        ids = np.argmax(contrast*near, axis=1)
        found = offsets[ids]
        confidence = normalized[np.arange(len(t)), ids]
        usable = (confidence > .4) & (contrast[np.arange(len(t)), ids] > 8)
        if usable.sum() >= 12:
            fit = np.linalg.lstsq(np.column_stack([np.ones(usable.sum()), t[usable]-.5]), found[usable], rcond=None)[0]
            center, slope = map(float, fit)
        residual = np.abs(found-center-(t-.5)*slope)
        support = usable & (residual < 2.5)
        pa, pb = a+normal*(center-.5*slope), b+normal*(center+.5*slope)
        line = np.cross([*pa, 1], [*pb, 1])
        lines.append(line/np.linalg.norm(line[:2]))
        reports.append({'support_fraction': float(support.mean()), 'median_residual_source_px': float(np.median(residual)),
                        'center_shift_source_px': center, 'slope_change_source_px': slope,
                        'search_boundary_hit': bool(max(abs(center-.5*slope), abs(center+.5*slope)) > search*.92),
                        'profile_offsets_source_px': offsets.tolist(),
                        'median_rgb_contrast': np.median(contrast, axis=0).tolist()})
    result = []
    for i in range(4):
        point = np.cross(lines[i-1], lines[i])
        if abs(point[2]) < 1e-8:
            raise ValueError('Candidate edges do not intersect reliably; revise the rough quad')
        result.append((point[:2]/point[2]).tolist())
    return np.array(result), reports


def rectify(im, quad, size, pad):
    w, h = size
    matrix, values = [], []
    for (x, y), (u, v) in zip([(pad, pad), (pad+w-1, pad), (pad+w-1, pad+h-1), (pad, pad+h-1)], quad):
        matrix.extend([[x,y,1,0,0,0,-u*x,-u*y], [0,0,0,x,y,1,-v*x,-v*y]])
        values.extend([u, v])
    coefficients = np.linalg.solve(np.asarray(matrix, float), values)
    return im.transform((w+2*pad, h+2*pad), Image.Transform.PERSPECTIVE,
                        tuple(coefficients), Image.Resampling.BICUBIC)


def fit_corners(rectified, size, pad, max_radius):
    w, h = size
    rgb = np.asarray(rectified.filter(ImageFilter.GaussianBlur(.6)), dtype=float)
    radii = np.arange(0, max_radius+1, dtype=float)
    angles = np.linspace(.16, np.pi/2-.16, 51)
    cos, sin = np.cos(angles)[None, :], np.sin(angles)[None, :]
    reports, curves = [], []
    for name, ox, oy, sx, sy in [('TL', pad, pad, 1, 1), ('TR', pad+w-1, pad, -1, 1),
                                ('BR', pad+w-1, pad+h-1, -1, -1), ('BL', pad, pad+h-1, 1, -1)]:
        x = ox+sx*radii[:, None]*(1-cos)
        y = oy+sy*radii[:, None]*(1-sin)
        nx, ny = -sx*cos, -sy*sin
        def gradient(shift):
            return np.linalg.norm(sample(rgb, x+nx*(shift+1.5), y+ny*(shift+1.5))
                                  - sample(rgb, x+nx*(shift-1.5), y+ny*(shift-1.5)), axis=-1)
        center = gradient(0)
        # Prefer a narrow actual boundary over a smooth lighting gradient.
        ridge = np.maximum(center-.35*(gradient(-4)+gradient(4)), 0)
        scores = np.mean(np.sort(ridge, axis=1)[:, 10:-10], axis=1)
        at = int(np.argmax(scores))
        strength = float(scores[at])
        elsewhere = scores[np.abs(radii-at) > max(8, max_radius*.1)]
        prominence = strength-float(elsewhere.max()) if len(elsewhere) else 0
        support = float(np.mean(center[at] > max(8, np.median(center[at])*.4)))
        reliable = strength > 10 and prominence > max(2, strength*.12) and support > .65 and at < max_radius-2
        reports.append({'corner': name, 'radius_output_px': at, 'strength': strength,
                        'prominence': prominence, 'support_fraction': support,
                        'reliable': bool(reliable), 'search_boundary_hit': bool(at >= max_radius-2)})
        curves.append(scores)
    # Find a group of independently supported, matching corners. Outliers do
    # not pull the consensus via an arithmetic average.
    valid = [i for i, r in enumerate(reports) if r['reliable']]
    tolerance = max(5, float(np.median([reports[i]['radius_output_px'] for i in valid]))*.16) if valid else 5
    groups = [[j for j in valid if 0 <= reports[j]['radius_output_px']-reports[i]['radius_output_px'] <= tolerance] for i in valid]
    group = max(groups, key=lambda g: (len(g), sum(reports[j]['strength'] for j in g)), default=[])
    radius = None
    if len(group) >= 2:
        normalized = [curves[j]/max(curves[j].max(), 1) for j in group]
        radius = int(np.argmax(np.mean(normalized, axis=0)))
        if radius <= 2:
            radius = 0
    return {'corners': reports, 'shared_radius_output_px': radius,
            'supporting_corners': [reports[j]['corner'] for j in group],
            'disagreeing_reliable_corners': [reports[j]['corner'] for j in valid if j not in group],
            'status': ('candidate_with_disagreement_requires_review' if any(j not in group for j in valid)
                       else 'candidate_requires_visual_review') if radius is not None else 'insufficient_or_conflicting_evidence',
            'search_max_output_px': max_radius}, curves


def diagnostic_sheets(im, seed, quad, rectified, size, pad, radius, out):
    w, h = size
    scale = min(1, 1100/max(im.size))
    overview = im.resize((round(im.width*scale), round(im.height*scale)))
    draw = ImageDraw.Draw(overview)
    for points, color in [(seed, '#ffb133'), (quad, '#00e5ff')]:
        pts = [tuple(p*scale) for p in points]
        draw.line(pts+[pts[0]], fill=color, width=2)
    for name, point in zip(['TL','TR','BR','BL'], quad):
        label = f'{name} ({point[0]:.1f}, {point[1]:.1f})'
        x, y = point*scale
        draw.text((min(x+8, overview.width-150), min(y+6, overview.height-18)), label, fill='#00e5ff', stroke_width=1, stroke_fill='#171c23')
    draw.rectangle((0, 0, overview.width, 24), fill='#151a20')
    draw.text((10, 6), 'AMBER: supplied rough quad   CYAN: candidate edges (review required)', fill='white')
    overview.save(out/'source-edges.jpg', quality=94)
    overlay = rectified.copy()
    if radius is not None:
        mask = rounded_mask(size, radius)
        padded = Image.new('L', rectified.size)
        padded.paste(mask, (pad, pad))
        band = np.array(padded.filter(ImageFilter.MaxFilter(3))) - np.array(padded.filter(ImageFilter.MinFilter(3)))
        line = Image.fromarray(np.uint8(band > 50)*255)
        overlay.paste((0, 235, 255), mask=line)
    extent = min(max(75, int((radius or 30)*1.9)), round(min(size)*.22))
    boxes = [(pad-12,pad-12,pad+extent,pad+extent),
             (pad+w-extent,pad-12,pad+w+12,pad+extent),
             (pad+w-extent,pad+h-extent,pad+w+12,pad+h+12),
             (pad-12,pad+h-extent,pad+extent,pad+h+12)]
    tile, header = 290, 36
    sheet = Image.new('RGB', (tile*4, tile*2+header*2), '#171c23')
    draw = ImageDraw.Draw(sheet)
    for i, (name, box) in enumerate(zip(['TL','TR','BR','BL'], boxes)):
        draw.text((i*tile+10, 12), f'{name}  SOURCE / NO MASK', fill='white')
        draw.text((i*tile+10, tile+header+12), f'OUTLINE R={radius}px' if radius is not None else 'NO RELIABLE SHARED RADIUS', fill='#00e5ff')
        for row, picture in enumerate([rectified, overlay]):
            sheet.paste(picture.crop(box).resize((tile, tile), Image.Resampling.NEAREST), (i*tile, header+row*(tile+header)))
    sheet.save(out/'corner-comparison.jpg', quality=96)
    # Four side-midpoint strips retain both the photographed exterior and print.
    boxes = [(pad+w//2-100,pad-14,pad+w//2+100,pad+26),
             (pad+w-26,pad+h//2-100,pad+w+14,pad+h//2+100),
             (pad+w//2-100,pad+h-26,pad+w//2+100,pad+h+14),
             (pad-14,pad+h//2-100,pad+26,pad+h//2+100)]
    strips = Image.new('RGB', (800, 960), '#171c23')
    d = ImageDraw.Draw(strips)
    for i, (name, box) in enumerate(zip(['TOP','RIGHT','BOTTOM','LEFT'], boxes)):
        d.text((10, i*240+9), f'{name}: SOURCE above / OUTLINE below; no automatic inset', fill='white')
        for row, picture in enumerate([rectified, overlay]):
            strip = picture.crop(box)
            if i % 2:
                strip = strip.transpose(Image.Transpose.ROTATE_90)
            strips.paste(strip.resize((800, 100), Image.Resampling.NEAREST), (0, i*240+30+row*103))
    strips.save(out/'edge-comparison.jpg', quality=96)


def inspect(args):
    config = Path(args.geometry)
    spec = json.loads(config.read_text(encoding='utf-8'))
    size, metadata = output_size(spec)
    im = image(args.input).convert('RGB')
    if list(im.size) != spec.get('source_size'):
        raise ValueError('source_size must match EXIF-oriented source')
    seed = np.asarray(spec['quad'], dtype=float)
    if seed.shape != (4, 2) or not np.isfinite(seed).all():
        raise ValueError('Provide a rough TL, TR, BR, BL quad for this object')
    vectors = np.roll(seed, -1, axis=0)-seed
    cross = vectors[:, 0]*np.roll(vectors[:, 1], -1)-vectors[:, 1]*np.roll(vectors[:, 0], -1)
    if not (cross > 0).all():
        raise ValueError('Rough quad must be convex and ordered TL, TR, BR, BL')
    search = args.search_px or max(6, round(min(np.linalg.norm(vectors, axis=1))*.012))
    if search <= 0 or search > min(im.size)*.1:
        raise ValueError('search-px must be positive and at most 10% of the source short edge')
    quad, edges = candidate_edges(im, seed, search)
    if args.keep_quad:
        quad = seed.copy()
    if ((quad < 0).any() or (quad[:, 0] >= im.width).any() or (quad[:, 1] >= im.height).any()):
        raise ValueError('Candidate quad leaves the photograph; revise the rough quad')
    vectors = np.roll(quad, -1, axis=0)-quad
    cross = vectors[:, 0]*np.roll(vectors[:, 1], -1)-vectors[:, 1]*np.roll(vectors[:, 0], -1)
    if not (cross > 0).all():
        raise ValueError('Candidate quad is not convex; revise the rough quad')
    pad = 24
    rectified = rectify(im, quad, size, pad)
    max_radius = args.max_radius_px or round(min(size)*.12)
    if not 3 <= max_radius <= min(size)/2:
        raise ValueError('max-radius-px must be between 3 and half the output short edge')
    radius, _ = fit_corners(rectified, size, pad, max_radius)
    if ((quad < 3).any() or (quad[:, 0] > im.width-4).any() or (quad[:, 1] > im.height-4).any()):
        radius.update(shared_radius_output_px=None, status='source_boundary_clipped: insufficient exterior evidence')
    # picture_only windows remain rectangular; no rounded frame inference.
    if metadata['target'] == 'picture_only':
        radius.update(shared_radius_output_px=0, status='picture_window_rectangle')
    candidate = {**spec, 'quad': quad.tolist(), 'output_size': list(size)}
    candidate.pop('pixels_per_mm', None)
    candidate.pop('long_edge_px', None)
    candidate.pop('mask_path', None)
    candidate['corner_radius_px'] = radius['shared_radius_output_px']
    candidate['geometry_review'] = 'pending: inspect source-edges, corner-comparison and edge-comparison before prepare'
    out = new_folder(args.out_dir)
    rectified.save(out/'rectified-unmasked-with-context.png')
    diagnostic_sheets(im, seed, quad, rectified, size, pad, radius['shared_radius_output_px'], out)
    report = {'source_sha256': digest(args.input), 'format': metadata,
              'method': 'rough-quad-guided straight-edge search, unmasked source arc evidence; not full-image object detection',
              'quad_seed': seed.tolist(), 'quad_candidate': quad.tolist(), 'kept_seed_quad': args.keep_quad,
              'edge_search_source_px': search, 'edges_T_R_B_L': edges, 'radius': radius,
              'edge_review_flags': [name for name, edge in zip(['TOP', 'RIGHT', 'BOTTOM', 'LEFT'], edges)
                                    if edge['support_fraction'] < .6 or edge['median_residual_source_px'] > 2.5 or edge['search_boundary_hit']],
              'inset': {'applied_source_px': 0, 'policy': 'No blanket inset. Review edge strips; correct only demonstrated contamination.'},
              'accepted': False, 'note': 'Gradients cannot identify paper vs printed borders/skin/shadow semantically. Visual review required. No finished artwork is produced.'}
    (out/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    (out/'candidate-geometry.json').write_text(json.dumps(candidate, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(out.resolve()), 'radius': radius, 'accepted': False}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--geometry', required=True, help='Confirmed dimensions and an AI-inspected rough quad')
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--search-px', type=int, help='Source-pixel edge search around the seed, not an inset')
    parser.add_argument('--max-radius-px', type=int, help='Output-pixel search limit, not a default radius')
    parser.add_argument('--keep-quad', action='store_true', help='Diagnose radius on an already reviewed quad; still report edge alternatives')
    args = parser.parse_args()
    try:
        inspect(args)
    except (ValueError, OSError, np.linalg.LinAlgError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
