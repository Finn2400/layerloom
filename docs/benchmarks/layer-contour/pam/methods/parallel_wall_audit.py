"""Bounded parallel coordinator for the unchanged Pam wall-audit algorithms.

One streaming parser produces aligned model-wall layers. At most --inflight
layer pairs are submitted, and ordered results preserve the serial reduction
order, including every individual histogram/sample-chunk contribution.
"""
from __future__ import annotations
import argparse
from collections import deque
from concurrent.futures import ProcessPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import signal
import time

import numpy as np
from compare_model_walls import aligned_layers, file_identity, sampled_distances, visuals
from layerloom.contour_gcode import compare_layers

THRESHOLDS=[.005,.01,.02,.04,.06,.08,.1,.2]
BINS=np.r_[np.arange(0,1.0001,.0001),np.inf]


def new_stat():
    return dict(length_mm=0.,samples=0,weighted_distance_sum=0.,above={str(t):0. for t in THRESHOLDS},
                histogram=np.zeros(len(BINS)-1),max_sample_distance_mm=0.)


def process_layer(a,b,tolerance=.02,spacing=.01):
    """Use existing certificates/sampling exactly; return bounded layer data."""
    if a is None:
        strict=dict(z_mm=b.z,passed=False,reason='extra candidate wall layer')
    elif b is None:
        strict=dict(z_mm=a.z,passed=False,reason='missing candidate wall layer')
    else:
        strict=compare_layers(a,b,tolerance_mm=tolerance,width_tolerance_mm=.02)
    if a is None or b is None:
        raise ValueError('Missing wall layer prevents a complete length distribution')
    row=dict(z_mm=a.z,total_length_mm=0.,length_above_002_mm=0.,max_sample_distance_mm=0.)
    contributions=[]
    for direction,source,target in [('reference_to_candidate',a,b),('candidate_to_reference',b,a)]:
        for tool in sorted({s.tool for s in source.segments}):
            ss=[s for s in source.segments if s.tool==tool]
            tt=[s for s in target.segments if s.tool==tool]
            if not tt:raise ValueError(f'Missing filament {tool} at Z={a.z}')
            for xy,distances,weights in sampled_distances(ss,tt,spacing,chunk=200_000):
                length=float(weights.sum())
                worst_index=int(np.argmax(distances));maximum=float(distances[worst_index])
                # Return each chunk separately so the parent performs the same
                # float64 additions in the same order as the serial helper.
                contributions.append(dict(direction=direction,length_mm=length,samples=len(distances),
                    weighted_distance_sum=float((weights*distances).sum()),
                    histogram=np.histogram(distances,bins=BINS,weights=weights)[0],
                    max_sample_distance_mm=maximum,
                    above={str(t):float(weights[distances>t].sum()) for t in THRESHOLDS}))
                row['total_length_mm']+=length
                row['length_above_002_mm']+=float(weights[distances>.02].sum())
                if maximum>row['max_sample_distance_mm']:
                    row.update(max_sample_distance_mm=maximum,worst_xy_mm=xy[worst_index].tolist(),tool=tool,direction=direction)
    return strict,row,contributions


def add_strict(summary,row,tolerance):
    summary['layers']+=1;summary['failed_layers']+=not row['passed']
    groups=row.get('combined_feature_deposition',[])
    summary['combined_feature_geometry_failed_layers']+=('reason' in row or any(not g['passed'] for g in groups))
    summary['certified_over_tolerance_layers']+=any(g.get('max_displacement_lower_mm',0.)>tolerance for g in groups)
    summary['max_combined_feature_displacement_upper_mm']=max(summary['max_combined_feature_displacement_upper_mm'],max((g.get('max_displacement_upper_mm',0.) for g in groups),default=0.))
    summary['max_displacement_upper_mm']=max(summary['max_displacement_upper_mm'],max((g.get('max_displacement_upper_mm',0.) for g in row.get('groups',[])),default=0.))


def add_distribution(summary,aggregate,row,contributions):
    for contribution in contributions:
        stat=aggregate[contribution['direction']]
        stat['length_mm']+=contribution['length_mm']
        stat['samples']+=contribution['samples']
        stat['weighted_distance_sum']+=contribution['weighted_distance_sum']
        stat['histogram']+=contribution['histogram']
        stat['max_sample_distance_mm']=max(stat['max_sample_distance_mm'],contribution['max_sample_distance_mm'])
        for t in THRESHOLDS:stat['above'][str(t)]+=contribution['above'][str(t)]
    summary['layer_count']+=1
    summary['worst_samples'].append(row)
    summary['worst_samples'].sort(key=lambda r:r['max_sample_distance_mm'],reverse=True)
    del summary['worst_samples'][8:]


def finish_distribution(aggregate):
    both=new_stat()
    for stat in aggregate.values():
        for key in ['length_mm','samples','weighted_distance_sum','histogram']:both[key]+=stat[key]
        for t in THRESHOLDS:both['above'][str(t)]+=stat['above'][str(t)]
        both['max_sample_distance_mm']=max(both['max_sample_distance_mm'],stat['max_sample_distance_mm'])
    aggregate['both']=both
    for stat in aggregate.values():
        stat['mean_distance_mm']=stat.pop('weighted_distance_sum')/stat['length_mm']
        stat['fraction_length_above']={k:v/stat['length_mm'] for k,v in stat['above'].items()}
        cumulative=np.cumsum(stat.pop('histogram'))
        stat['distance_quantile_upper_bin_mm']={str(q):float(BINS[min(len(BINS)-1,np.searchsorted(cumulative,q*stat['length_mm'])+1)]) for q in [.5,.9,.95,.99,.999]}
    return aggregate


def run(reference,candidate,prefix,*,workers=6,inflight=8,tolerance=.02,spacing=.01,layer_indices=None,max_layers=None,make_visuals=False):
    if workers<1 or workers>6:raise ValueError('Worker count must be between 1 and 6')
    if inflight<workers or inflight>8:raise ValueError('In-flight count must be at least workers and at most 8')
    if layer_indices is not None and max_layers is not None:raise ValueError('Select layer indices or maximum layer count, not both')
    indices=set(layer_indices) if layer_indices is not None else None
    if indices is not None and (not indices or min(indices)<0):raise ValueError('Layer indices must be nonnegative and nonempty')
    if max_layers is not None and max_layers<1:raise ValueError('Maximum layers must be positive')
    prefix=Path(prefix);prefix.parent.mkdir(parents=True,exist_ok=True)
    suffixes=['.layers.jsonl','.distribution.layers.jsonl','.summary.json','.distribution.json','.local-errors.png']
    for suffix in suffixes:
        if Path(str(prefix)+suffix).exists():raise FileExistsError('Refusing to overwrite '+str(prefix)+suffix)
    lock=Path(str(prefix)+'.parallel.lock')
    started=time.monotonic();scope={}
    artifact_a=file_identity(reference);artifact_b=file_identity(candidate)
    with lock.open('x') as f:f.write(str(os.getpid())+'\n')
    strict_summary=dict(reference=str(reference),candidate=str(candidate),tolerance_mm=tolerance,
        reference_artifact=artifact_a,candidate_artifact=artifact_b,width_tolerance_mm=.02,layers=0,failed_layers=0,
        combined_feature_geometry_failed_layers=0,certified_over_tolerance_layers=0,
        max_displacement_upper_mm=0.,max_combined_feature_displacement_upper_mm=0.,wall_paths_validated=False,object_scope=scope,
        exclusions='Only named print-object Outer/Inner/Overhang walls; supports, prime tower, infill, travels and custom moves excluded by feature annotations.',
        method='Continuous-polyline Hausdorff enclosure; 0.001mm residual. Width compatibility is spatial, not just a min/max check. No entire G-code file is loaded.')
    dist_summary=dict(reference=str(reference),candidate=str(candidate),spacing_mm=spacing,
        reference_artifact=artifact_a,candidate_artifact=artifact_b,
        method='Bidirectional per-filament nearest-wall distances with all wall classes combined; length-weighted midpoint samples. Sampling does not certify exact extrema or entire paths.',
        object_scope=scope,layer_count=0,worst_samples=[])
    aggregate={direction:new_stat() for direction in ['reference_to_candidate','candidate_to_reference']}
    pending=deque();submitted=0;peak_pending=0;input_exhausted=False;selection_finished=False
    layer_iterator=aligned_layers(reference,candidate,scope)
    iterator=enumerate(layer_iterator)
    def selected_pair():
        nonlocal input_exhausted,selection_finished
        if selection_finished:return None
        for index,pair in iterator:
            if max_layers is not None and index>=max_layers:
                selection_finished=True;return None
            if indices is not None:
                if index>max(indices):selection_finished=True;return None
                if index not in indices:continue
            if (indices is not None and index==max(indices)) or (max_layers is not None and index==max_layers-1):
                selection_finished=True
            return index,pair
        input_exhausted=True;return None
    try:
        with Path(str(prefix)+'.layers.jsonl').open('w') as strict_out,Path(str(prefix)+'.distribution.layers.jsonl').open('w') as dist_out:
            with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as pool:
                while pending or not (input_exhausted or selection_finished):
                    while len(pending)<inflight and not (input_exhausted or selection_finished):
                        selected=selected_pair()
                        if selected is None:break
                        index,(a,b)=selected
                        pending.append((index,pool.submit(process_layer,a,b,tolerance,spacing)))
                        submitted+=1;peak_pending=max(peak_pending,len(pending))
                        del a,b
                    if not pending:break
                    index,future=pending.popleft()
                    strict,row,contributions=future.result()
                    add_strict(strict_summary,strict,tolerance)
                    add_distribution(dist_summary,aggregate,row,contributions)
                    strict_out.write(json.dumps(strict)+'\n');dist_out.write(json.dumps(row)+'\n')
                    if strict_summary['layers']%25==1:
                        strict_out.flush();dist_out.flush()
                        print(f'parallel wall audit {strict_summary["layers"]} completed, source index={index}, Z={row["z_mm"]:.3f}, elapsed={time.monotonic()-started:.1f}s, in_flight={len(pending)}',flush=True)
                    del future,strict,row,contributions
        if not strict_summary['layers']:raise ValueError('No wall layers selected')
        all_layers=input_exhausted and indices is None and max_layers is None
        strict_summary['wall_paths_validated']=all_layers and strict_summary['failed_layers']==0
        elapsed=time.monotonic()-started
        execution=dict(workers=workers,max_inflight_configured=inflight,peak_inflight_observed=peak_pending,
            layer_pairs_submitted=submitted,parser_passes=1,results_reduced_in_input_order=True,
            sample_chunk_size=200_000,histogram_contributions_reduced_in_serial_chunk_order=True,
            all_layers_compared=all_layers,selected_layer_indices=sorted(indices) if indices is not None else None,max_layers=max_layers,
            memory_scope='At most eight submitted layer pairs; six worker-local GEOS geometries and sample chunks. No entire G-code or mesh is loaded.')
        strict_summary.update(elapsed_seconds=elapsed,layer_report=str(Path(str(prefix)+'.layers.jsonl')),parallel_execution=execution)
        dist_summary.update(weighted=finish_distribution(aggregate),elapsed_seconds=elapsed,parallel_execution=execution)
        Path(str(prefix)+'.summary.json').write_text(json.dumps(strict_summary,indent=2)+'\n')
        Path(str(prefix)+'.distribution.json').write_text(json.dumps(dist_summary,indent=2)+'\n')
        if make_visuals:
            if not all_layers:raise ValueError('Visuals require a full audit rather than a proof subset')
            visual=visuals(reference,candidate,prefix)
            Path(str(prefix)+'.visuals.json').write_text(json.dumps(visual,indent=2)+'\n')
        return dict(strict=strict_summary,distribution=dist_summary)
    finally:
        layer_iterator.close()
        lock.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference',type=Path);parser.add_argument('candidate',type=Path)
    parser.add_argument('--output-prefix',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=6);parser.add_argument('--inflight',type=int,default=8)
    parser.add_argument('--max-layers',type=int);parser.add_argument('--layer-indices',type=int,nargs='+')
    parser.add_argument('--visuals',action='store_true')
    args=parser.parse_args()
    def terminate(_signal,_frame):raise KeyboardInterrupt('Parallel wall audit interrupted')
    signal.signal(signal.SIGTERM,terminate)
    result=run(args.reference,args.candidate,args.output_prefix,workers=args.workers,inflight=args.inflight,
        max_layers=args.max_layers,layer_indices=args.layer_indices,make_visuals=args.visuals)
    print(json.dumps({key:{k:v for k,v in val.items() if k not in ['object_scope','worst_samples','reference_artifact','candidate_artifact']} for key,val in result.items()},indent=2),flush=True)


if __name__=='__main__':main()
