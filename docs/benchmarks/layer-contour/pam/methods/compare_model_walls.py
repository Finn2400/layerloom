"""Stream model-only Pam wall validation without counting supports or the tower.

Run with the feature worktree's src directory in PYTHONPATH. All motion and tool
state is retained; only feature annotations outside named print objects are
masked. Explicit object labels are mandatory. No G-code is modified on disk.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import shapely
from layerloom.contour_gcode import iter_wall_layers, compare_layers


def file_identity(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''):
            digest.update(block)
    return dict(path=str(Path(path).resolve()), size_bytes=Path(path).stat().st_size, sha256=digest.hexdigest())


class ModelStream:
    def __init__(self, path, *, byte_limit=None):
        self.path = Path(path)
        if byte_limit is not None and (not isinstance(byte_limit, int) or byte_limit <= 0):
            raise ValueError('Byte limit must be a positive integer at a line boundary')
        self.byte_limit = byte_limit
        self.stats = dict(path=str(self.path), object_starts=0, object_stops=0,
                          object_names={}, feature_annotations_inside={}, feature_annotations_outside={}, byte_limit=byte_limit)

    def __iter__(self):
        inside = False
        feature = 'Custom'
        starts = 0
        consumed = 0
        digest = hashlib.sha256()
        with self.path.open('rb') as stream:
            for raw_bytes in stream:
                if self.byte_limit is not None and consumed >= self.byte_limit:
                    break
                if self.byte_limit is not None and consumed + len(raw_bytes) > self.byte_limit:
                    raise ValueError('Byte limit cuts a G-code line')
                digest.update(raw_bytes)
                consumed += len(raw_bytes)
                raw = raw_bytes.decode('utf-8')
                comment = raw.partition(';')[2].strip()
                lower = comment.lower()
                if lower.startswith('printing object '):
                    if inside:
                        raise ValueError('Nested print-object marker in ' + str(self.path))
                    inside = True
                    starts += 1
                    self.stats['object_starts'] += 1
                    name = comment[len('printing object '):]
                    self.stats['object_names'][name] = self.stats['object_names'].get(name, 0) + 1
                    yield ';TYPE:' + feature + '\n'
                elif lower.startswith('stop printing object '):
                    if not inside:
                        raise ValueError('Unmatched stop-object marker in ' + str(self.path))
                    inside = False
                    self.stats['object_stops'] += 1
                    yield ';TYPE:Excluded outside named model object\n'
                if lower.startswith(('type:', 'feature:')):
                    feature = comment.split(':', 1)[1].strip()
                    key = 'feature_annotations_inside' if inside else 'feature_annotations_outside'
                    self.stats[key][feature] = self.stats[key].get(feature, 0) + 1
                    if not inside:
                        # Keep any command before the semicolon, so machine
                        # coordinates, extrusion state and tools remain exact.
                        raw = raw.partition(';')[0] + ';TYPE:Excluded outside named model object\n'
                yield raw
        if self.byte_limit is not None and consumed != self.byte_limit:
            raise ValueError('G-code ended before requested byte limit')
        self.stats['input_bytes_used'] = consumed
        self.stats['input_prefix_sha256'] = digest.hexdigest()
        if not starts:
            raise ValueError('No explicit print-object labels: refusing an unscoped wall audit')
        if inside:
            raise ValueError('Unclosed print-object label in ' + str(self.path))


def model_layers(path, stats=None, *, byte_limit=None):
    scoped = ModelStream(path, byte_limit=byte_limit)
    yield from iter_wall_layers(scoped)
    if stats is not None:
        stats.update(scoped.stats)


def aligned_layers(reference, candidate, stats, *, reference_byte_limit=None, candidate_byte_limit=None):
    a = iter(model_layers(reference, stats.setdefault('reference', {}), byte_limit=reference_byte_limit))
    b = iter(model_layers(candidate, stats.setdefault('candidate', {}), byte_limit=candidate_byte_limit))
    aa, bb = next(a, None), next(b, None)
    while aa is not None or bb is not None:
        if aa is None or (bb is not None and bb.z < aa.z - 1e-6):
            yield None, bb
            bb = next(b, None)
        elif bb is None or aa.z < bb.z - 1e-6:
            yield aa, None
            aa = next(a, None)
        else:
            yield aa, bb
            aa, bb = next(a, None), next(b, None)


def compare(reference, candidate, prefix, tolerance=.02):
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    path = Path(str(prefix) + '.layers.jsonl')
    summary = dict(reference=str(reference), candidate=str(candidate), tolerance_mm=tolerance,
                   reference_artifact=file_identity(reference), candidate_artifact=file_identity(candidate),
                   width_tolerance_mm=.02, layers=0, failed_layers=0,
                   combined_feature_geometry_failed_layers=0, certified_over_tolerance_layers=0,
                   max_displacement_upper_mm=0., max_combined_feature_displacement_upper_mm=0.,
                   wall_paths_validated=False, object_scope={},
                   exclusions='Only named print-object Outer/Inner/Overhang walls; supports, prime tower, infill, travels and custom moves excluded by feature annotations.',
                   method='Continuous-polyline Hausdorff enclosure; 0.001mm residual. Width compatibility is spatial, not just a min/max check. No entire G-code file is loaded.')
    start = time.monotonic()
    with path.open('w') as output:
        for i, (a, b) in enumerate(aligned_layers(reference, candidate, summary['object_scope'])):
            if a is None:
                row = dict(z_mm=b.z, passed=False, reason='extra candidate wall layer')
            elif b is None:
                row = dict(z_mm=a.z, passed=False, reason='missing candidate wall layer')
            else:
                row = compare_layers(a, b, tolerance_mm=tolerance, width_tolerance_mm=.02)
            summary['layers'] += 1
            summary['failed_layers'] += not row['passed']
            groups = row.get('combined_feature_deposition', [])
            summary['combined_feature_geometry_failed_layers'] += ('reason' in row or any(not g['passed'] for g in groups))
            summary['certified_over_tolerance_layers'] += any(g.get('max_displacement_lower_mm', 0.) > tolerance for g in groups)
            summary['max_combined_feature_displacement_upper_mm'] = max(summary['max_combined_feature_displacement_upper_mm'], max((g.get('max_displacement_upper_mm', 0.) for g in groups), default=0.))
            summary['max_displacement_upper_mm'] = max(summary['max_displacement_upper_mm'], max((g.get('max_displacement_upper_mm', 0.) for g in row.get('groups', [])), default=0.))
            output.write(json.dumps(row) + '\n')
            if i % 50 == 0:
                output.flush()
                print(f'wall audit layer {i + 1}, Z={row["z_mm"]:.3f}, elapsed={time.monotonic()-start:.1f}s', flush=True)
    summary['wall_paths_validated'] = summary['layers'] > 0 and summary['failed_layers'] == 0
    summary['elapsed_seconds'] = time.monotonic() - start
    summary['layer_report'] = str(path)
    Path(str(prefix) + '.summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def sampled_distances(segments, target, spacing=.01, chunk=200_000):
    """Length-weighted midpoint samples in bounded chunks; a diagnostic, not a certificate."""
    tree = shapely.STRtree(shapely.linestrings(np.asarray([(s.start, s.end) for s in target])))
    # Segment batches limit temporary arrays even for unusually detailed layers.
    batch_xy, batch_weight, count = [], [], 0
    def evaluate():
        xy = np.concatenate(batch_xy)
        weight = np.concatenate(batch_weight)
        indices, distances = tree.query_nearest(shapely.points(xy), all_matches=False, return_distance=True)
        ordered = np.empty(len(xy))
        ordered[indices[0]] = distances
        return xy, ordered, weight
    for segment in segments:
        a, b = np.asarray(segment.start), np.asarray(segment.end)
        length = float(np.linalg.norm(b - a))
        n = max(1, math.ceil(length / spacing))
        for offset in range(0, n, chunk):
            take = min(chunk, n-offset)
            u = (np.arange(offset, offset+take)+.5)/n
            batch_xy.append(a + u[:, None]*(b-a))
            batch_weight.append(np.full(take, length/n))
            count += take
            if count >= chunk:
                yield evaluate()
                batch_xy, batch_weight, count = [], [], 0
    if count:
        yield evaluate()


def distribution(reference, candidate, prefix, spacing=.01):
    thresholds = [.005, .01, .02, .04, .06, .08, .1, .2]
    bins = np.r_[np.arange(0, 1.0001, .0001), np.inf]
    aggregate = {direction:dict(length_mm=0., samples=0, weighted_distance_sum=0.,
                                above={str(t):0. for t in thresholds}, histogram=np.zeros(len(bins)-1),
                                max_sample_distance_mm=0.)
                 for direction in ['reference_to_candidate', 'candidate_to_reference']}
    summary = dict(reference=str(reference), candidate=str(candidate), spacing_mm=spacing,
                   reference_artifact=file_identity(reference), candidate_artifact=file_identity(candidate),
                   method='Bidirectional per-filament nearest-wall distances with all wall classes combined; length-weighted midpoint samples. Sampling does not certify exact extrema or entire paths.',
                   object_scope={}, layer_count=0, worst_samples=[])
    start = time.monotonic()
    with Path(str(prefix) + '.distribution.layers.jsonl').open('w') as output:
        for i, (a, b) in enumerate(aligned_layers(reference, candidate, summary['object_scope'])):
            if a is None or b is None:
                raise ValueError('Missing wall layer prevents a complete length distribution')
            row = dict(z_mm=a.z, total_length_mm=0., length_above_002_mm=0., max_sample_distance_mm=0.)
            for direction, source, target in [('reference_to_candidate', a, b), ('candidate_to_reference', b, a)]:
                stat = aggregate[direction]
                for tool in sorted({s.tool for s in source.segments}):
                    ss = [s for s in source.segments if s.tool == tool]
                    tt = [s for s in target.segments if s.tool == tool]
                    if not tt:
                        raise ValueError(f'Missing filament {tool} at Z={a.z}')
                    for xy, distances, weights in sampled_distances(ss, tt, spacing):
                        length = float(weights.sum())
                        stat['length_mm'] += length
                        stat['samples'] += len(distances)
                        stat['weighted_distance_sum'] += float((weights*distances).sum())
                        stat['histogram'] += np.histogram(distances, bins=bins, weights=weights)[0]
                        worst_index = int(np.argmax(distances))
                        maximum = float(distances[worst_index])
                        stat['max_sample_distance_mm'] = max(stat['max_sample_distance_mm'], maximum)
                        for t in thresholds:
                            stat['above'][str(t)] += float(weights[distances > t].sum())
                        row['total_length_mm'] += length
                        row['length_above_002_mm'] += float(weights[distances > .02].sum())
                        if maximum > row['max_sample_distance_mm']:
                            row.update(max_sample_distance_mm=maximum, worst_xy_mm=xy[worst_index].tolist(), tool=tool, direction=direction)
            summary['layer_count'] += 1
            summary['worst_samples'].append(row)
            summary['worst_samples'].sort(key=lambda r:r['max_sample_distance_mm'], reverse=True)
            del summary['worst_samples'][8:]
            output.write(json.dumps(row) + '\n')
            if i % 50 == 0:
                output.flush()
                print(f'wall distribution layer {i+1}, Z={a.z:.3f}, elapsed={time.monotonic()-start:.1f}s', flush=True)
    both = dict(length_mm=0., samples=0, weighted_distance_sum=0., above={str(t):0. for t in thresholds}, histogram=np.zeros(len(bins)-1), max_sample_distance_mm=0.)
    for stat in aggregate.values():
        for key in ['length_mm', 'samples', 'weighted_distance_sum', 'histogram']:
            both[key] += stat[key]
        for t in thresholds:
            both['above'][str(t)] += stat['above'][str(t)]
        both['max_sample_distance_mm'] = max(both['max_sample_distance_mm'], stat['max_sample_distance_mm'])
    aggregate['both'] = both
    for stat in aggregate.values():
        stat['mean_distance_mm'] = stat.pop('weighted_distance_sum')/stat['length_mm']
        stat['fraction_length_above'] = {k:v/stat['length_mm'] for k,v in stat['above'].items()}
        cumulative = np.cumsum(stat.pop('histogram'))
        stat['distance_quantile_upper_bin_mm'] = {str(q):float(bins[min(len(bins)-1,np.searchsorted(cumulative,q*stat['length_mm'])+1)]) for q in [.5,.9,.95,.99,.999]}
    summary['weighted'] = aggregate
    summary['elapsed_seconds'] = time.monotonic() - start
    Path(str(prefix) + '.distribution.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def visuals(reference, candidate, prefix, panels=6):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    report = json.loads(Path(str(prefix) + '.distribution.json').read_text())
    rows = report['worst_samples'][:panels]
    needed = {r['z_mm']:r for r in rows}
    selected = {}
    for a,b in aligned_layers(reference,candidate,{}):
        if a is not None and b is not None and a.z in needed:
            selected[a.z] = (a,b)
    fig,axes = plt.subplots(len(rows),2,figsize=(13,4.5*len(rows)),squeeze=False)
    for row,(left,right) in zip(rows,axes):
        a,b = selected[row['z_mm']]
        for ax in [left,right]:
            aa = a.segments if ax is left else [s for s in a.segments if s.tool == row['tool']]
            bb = b.segments if ax is left else [s for s in b.segments if s.tool == row['tool']]
            ax.add_collection(LineCollection([(s.start,s.end) for s in aa], colors='#777777', linewidths=1.1,label='Zero simplification'))
            ax.add_collection(LineCollection([(s.start,s.end) for s in bb], colors='#007ac2', linewidths=.6,linestyles='dashed',label='0.02 mm contours'))
            ax.autoscale();ax.set_aspect('equal');ax.grid(alpha=.15)
            ax.set_xlabel('X (mm)');ax.set_ylabel('Y (mm)')
        xy = row['worst_xy_mm'];radius = max(1.5,3*row['max_sample_distance_mm'])
        left.plot(*xy,'o',color='#db3b24',markersize=4)
        left.set_title(f'Model walls at Z={a.z:.3f} mm; red mark = local worst sample')
        right.set_xlim(xy[0]-radius,xy[0]+radius);right.set_ylim(xy[1]-radius,xy[1]+radius)
        right.plot(*xy,'o',color='#db3b24',markersize=4)
        right.set_title(f'Local zoom: sampled error {row["max_sample_distance_mm"]:.5f} mm, tool {row["tool"]}')
        right.legend(loc='best',fontsize=8)
    fig.suptitle('Pam model-only wall comparison: full layers and local error regions\nSupports and purge tower excluded; sampled locations are diagnostics, not exact maxima',fontsize=12)
    fig.tight_layout(rect=[0,0,1,.98])
    output = Path(str(prefix) + '.local-errors.png')
    fig.savefig(output,dpi=150);plt.close(fig)
    return {'image':str(output),'sample_rows':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['compare','distribution','visuals','inventory'])
    parser.add_argument('reference',type=Path)
    parser.add_argument('candidate',type=Path)
    parser.add_argument('--output-prefix',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='compare':result=compare(args.reference,args.candidate,args.output_prefix)
    elif args.action=='distribution':result=distribution(args.reference,args.candidate,args.output_prefix)
    elif args.action=='visuals':result=visuals(args.reference,args.candidate,args.output_prefix)
    else:
        result={}
        for name,path in [('reference',args.reference),('candidate',args.candidate)]:
            stats={};n=segments=0;tools=Counter();feature=Counter();z_first=z_last=None
            for layer in model_layers(path,stats):
                n+=1;segments+=len(layer.segments);z_first=layer.z if z_first is None else z_first;z_last=layer.z
                tools.update(s.tool for s in layer.segments);feature.update(s.feature for s in layer.segments)
            result[name]=dict(stats,layers=n,segments=segments,tools=dict(tools),features=dict(feature),first_z=z_first,last_z=z_last)
        Path(str(args.output_prefix)+'.inventory.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':main()
