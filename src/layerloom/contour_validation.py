"""Independent all-layer audit of the serialized layer-contour deliverable.

This reopens both 3MFs through separate disk-backed stores. It never trusts the
builder's cached polygons or its validation counters. The geometric slicing and
numerical regularization policy are shared deliberately; this is an independent
artifact/readback audit, not a second slicing-kernel implementation. It does not
certify downstream wall paths or seams.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import hashlib
import json
import math
import tempfile
import time

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .contour_store import TriangleStore
from .contour_weave import (GRID, EXPORT_GRID, _signature, _within,
                            _numerical_cleanup, layer_regions, polygons)


def _polygonal(g):
    return unary_union(list(polygons(g)))


def _geometry_comparison(reference,candidate,tolerance):
    a,b=_signature(reference),_signature(candidate)
    within=bool(_within(reference,candidate,tolerance))
    distance=0. if reference.is_empty and candidate.is_empty else (
        None if reference.is_empty or candidate.is_empty else
        float(reference.boundary.hausdorff_distance(candidate.boundary)))
    return dict(passed=a==b and within,topology_matches=a==b,
        reference_components=a[0],candidate_components=b[0],reference_holes=a[1],candidate_holes=b[1],
        boundary_within_certified_limit=within,certified_limit_mm=tolerance,
        observed_boundary_hausdorff_mm=distance,
        symmetric_difference_area_mm2=float(reference.symmetric_difference(candidate).area),
        reference_area_mm2=float(reference.area),candidate_area_mm2=float(candidate.area))


def _part_digest(store,part,legacy_rounding=False):
    h=hashlib.sha256()
    for vertices in store.iter_vertices(part.id):
        v=np.round(vertices,9) if legacy_rounding else vertices.copy()
        v[v==0]=0. # Canonicalize signed zero without moving any geometry.
        h.update(np.asarray(v,dtype='<f8').tobytes())
    for faces in store.iter_faces(part.id):h.update(np.asarray(faces,dtype='<u8').tobytes())
    return h.hexdigest()


def _audit_passthrough(source,candidate,legacy_rounding=False):
    left=defaultdict(list);right=defaultdict(list)
    for store,groups in [(source,left),(candidate,right)]:
        for p in store.parts:
            if p.noweave:groups[p.label].append(p)
    details=[]
    for label in sorted(set(left)|set(right)):
        a,b=left[label],right[label]
        geometry_matches=len(a)==len(b)
        material_matches=len(a)==len(b)
        for old,new in zip(a,b):
            if (old.vertex_count,old.triangle_count)!=(new.vertex_count,new.triangle_count):
                geometry_matches=False
            elif _part_digest(source,old,legacy_rounding)!=_part_digest(candidate,new,legacy_rounding):geometry_matches=False
            if (old.source_color,old.source_extruder)!=(new.source_color,new.source_extruder):
                material_matches=False
        details.append(dict(label=label,reference_instances=len(a),candidate_instances=len(b),
            passed=geometry_matches and material_matches,geometry_matches=geometry_matches,
            material_provenance_matches=material_matches,
            reference_colors=[p.source_color for p in a],candidate_colors=[p.source_color for p in b],
            reference_extruders=[p.source_extruder for p in a],candidate_extruders=[p.source_extruder for p in b]))
    return dict(passed=all(x['passed'] for x in details),parts=details,
                legacy_nine_decimal_rounding=legacy_rounding,
                method='Indexed float64 geometry plus source color/extruder provenance; transforms and reflected winding included. Legacy nine-decimal rounding is used only when the candidate predates explicit coordinate encoding.')


def audit_contours(source,candidate,report_path=None,tolerance=None):
    """Re-slice and compare every nominal fixed-height printing plane.

    ``tolerance`` defaults to the candidate's requested XY tolerance. As in the
    exporter, zero disables discretionary simplification but retains a separately
    declared numerical allowance of twice the binary export-grid spacing.
    ``report_path`` is the aggregate JSON; detailed layer records stream to the
    adjacent ``*.layers.jsonl`` file. NOWEAVE parts are checked separately by
    indexed geometry, not silently excluded from the success result.
    """
    started=time.monotonic();source=Path(source).resolve();candidate=Path(candidate).resolve()
    settings_path=candidate.with_suffix('.report.json')
    settings=json.loads(settings_path.read_text())
    if settings.get('mode')!='layer-contour':raise ValueError('Expected layer-contour candidate settings')
    encoding=settings.get('coordinate_encoding')
    if encoding not in (None,'float64-roundtrip-17g'):
        raise ValueError('Unsupported candidate coordinate encoding: '+str(encoding))
    step=float(settings['layer_height']);first=float(settings['first_layer_height'])
    requested=float(settings['xy_tolerance_mm'] if tolerance is None else tolerance)
    if not all(math.isfinite(v) for v in [step,first,requested]) or step<=0 or first<=0 or requested<0:
        raise ValueError('Invalid layer schedule or audit tolerance')
    # Allow only the declared numerical policy; don't silently validate a file
    # produced with a different grid under current behavior.
    for key,actual in [('numerical_export_grid_mm',EXPORT_GRID),('numerical_intersection_grid_mm',GRID)]:
        if key in settings and not math.isclose(float(settings[key]),actual,rel_tol=0,abs_tol=1e-15):
            raise ValueError('Candidate numerical policy differs from auditor: '+key)
    effective=max(requested,2*EXPORT_GRID)
    token=settings.get('first_layer_token',settings.get('first_layer_color'))
    report_path=Path(report_path).resolve() if report_path else candidate.with_suffix('.audit.json')
    report_path.parent.mkdir(parents=True,exist_ok=True)
    layers_path=report_path.with_suffix('.layers.jsonl')
    if report_path in [source,candidate,settings_path] or layers_path in [source,candidate,settings_path]:
        raise ValueError('Audit report must not overwrite source, candidate, or settings')
    counts={'layers':0,'failed_layers':0,'per_filament_failures':0,'union_failures':0,'overlap_failures':0}
    max_distance=0.;max_area=0.;failed_examples=[]
    cleanup={'source_area_mm2':0.,'source_count':0,'candidate_area_mm2':0.,'candidate_count':0}
    with tempfile.TemporaryDirectory(prefix='layerloom-audit-',dir=report_path.parent) as tmp:
        tmp=Path(tmp)
        with TriangleStore(source,tmp/'reference') as a,TriangleStore(candidate,tmp/'candidate') as b:
            z0=float(a.bounds[0,2]);ztop=float(a.bounds[1,2])
            hash_match=a.source_sha256==settings.get('input_sha256')
            output_hash_match=b.source_sha256==settings.get('output_sha256')
            candidate_tokens_valid=all(p.noweave or (p.run is not None and len(p.run)==1) for p in b.parts)
            passthrough=_audit_passthrough(a,b,legacy_rounding=encoding is None)
            staged_layers=tmp/'layers.jsonl'
            with staged_layers.open('w') as output:
                i=0;lo=z0
                while True:
                    hi=z0+first+i*step;z=(lo+hi)/2
                    if z>=ztop-1e-10:break
                    phase=int(math.floor((z-z0)/step+1e-8))
                    reference=layer_regions(a,z,phase,first_layer_token=token if i==0 else None)
                    current=layer_regions(b,z,phase)
                    for name,regions in [('source',reference),('candidate',current)]:
                        for c,g in list(regions.items()):
                            regions[c],area,number=_numerical_cleanup(g)
                            cleanup[name+'_area_mm2']+=area;cleanup[name+'_count']+=number
                    colors=sorted(set(reference)|set(current))
                    row={'layer':i,'low_z':lo,'high_z':hi,'slice_z':z,'phase':phase,'filaments':{},'overlaps':{}}
                    for c in colors:
                        r=_geometry_comparison(reference.get(c,Polygon()),current.get(c,Polygon()),effective)
                        row['filaments'][c]=r
                        if not r['passed']:counts['per_filament_failures']+=1
                    row['union']=_geometry_comparison(unary_union(list(reference.values())),unary_union(list(current.values())),effective)
                    if not row['union']['passed']:counts['union_failures']+=1
                    for j,c in enumerate(colors):
                        for d in colors[j+1:]:
                            old=_polygonal(reference.get(c,Polygon()).intersection(reference.get(d,Polygon())))
                            new=_polygonal(current.get(c,Polygon()).intersection(current.get(d,Polygon())))
                            if old.is_empty and new.is_empty:continue
                            r=_geometry_comparison(old,new,effective);row['overlaps'][c+d]=r
                            if not r['passed']:counts['overlap_failures']+=1
                    comparisons=[*row['filaments'].values(),row['union'],*row['overlaps'].values()]
                    row['passed']=all(x['passed'] for x in comparisons)
                    max_distance=max(max_distance,max((x['observed_boundary_hausdorff_mm'] or 0.) for x in comparisons))
                    max_area=max(max_area,max(x['symmetric_difference_area_mm2'] for x in comparisons))
                    counts['layers']+=1
                    if not row['passed']:
                        counts['failed_layers']+=1
                        if len(failed_examples)<20:failed_examples.append({'layer':i,'slice_z':z})
                    output.write(json.dumps(row)+'\n')
                    i+=1;lo=hi
            count_matches=counts['layers']==int(settings['layers'])
            passed=(counts['layers']>0 and counts['failed_layers']==0 and hash_match and output_hash_match
                    and candidate_tokens_valid and passthrough['passed'] and count_matches)
            result=dict(audit_revision='serialized-contours-2',source_path=str(source),candidate_path=str(candidate),source_sha256=a.source_sha256,
                candidate_sha256=b.source_sha256,source_hash_matches_candidate_reference=hash_match,
                candidate_hash_matches_report=output_hash_match,candidate_single_token_parts_valid=candidate_tokens_valid,
                candidate_coordinate_encoding=encoding,
                layer_height_mm=step,first_layer_height_mm=first,first_layer_token=token,
                declared_layer_count=int(settings['layers']),layer_count_matches_report=count_matches,
                requested_tolerance_mm=requested,effective_certified_boundary_limit_mm=effective,
                numerical_policy={'intersection_grid_mm':GRID,'common_binary_grid_mm':EXPORT_GRID,
                    'minimum_numerical_allowance_mm':2*EXPORT_GRID,
                    'boundary_certificate_precision':'Float64 boundary copies with fixed-precision metadata cleared before conservative buffering; coordinates unchanged.',
                    'regularization':'Both independently decoded artifacts use the common binary-grid contour policy before comparison.',
                    'complete_vertical_quad_policy':'Recovered rectangle-edge vertices bypass intermediate intersection rounding; exactly-on-lattice sections receive pointwise precision metadata without topology normalization.',
                    'sub_grid_debris_cleanup':cleanup},
                **counts,passed=passed,all_layers_compared=True,serialized_contours_validated=passed,
                observed_max_boundary_hausdorff_mm=max_distance,max_symmetric_difference_area_mm2=max_area,
                first_failed_layers=failed_examples,passthrough=passthrough,
                toolpaths_validated=False,
                method='Independent serialized-artifact re-slicing; certified two-way boundary-buffer containment plus component/hole and overlap topology checks on every fixed-height slicing plane.',
                limitations=['Shares the slicing kernel with the exporter; not a separate kernel proof.',
                    'Comparison is to numerically regularized source contours; grids and cleanup are disclosed above.',
                    'Wall centerlines, extrusion widths, seams, and supports require downstream slicer/G-code validation.',
                    'Does not certify full-mesh manifold topology away from slicing planes.'],
                layers_report=str(layers_path),elapsed_seconds=time.monotonic()-started)
        # Publish diagnostics only after both stores are closed. These reports
        # never modify candidate bytes or its builder report.
        staged_json=tmp/'aggregate.json';staged_json.write_text(json.dumps(result,indent=2)+'\n')
        staged_layers.replace(layers_path);staged_json.replace(report_path)
    return result


def main(argv=None):
    """CLI entrypoint; failed checks return a nonzero status with diagnostics."""
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path,help='Original prepared labeled 3MF')
    parser.add_argument('candidate',type=Path,help='Serialized contour 3MF with its .report.json sidecar')
    parser.add_argument('--report',type=Path,help='Aggregate JSON path; adjacent layer metrics stream to JSONL')
    parser.add_argument('--tolerance-mm',type=float,help='Override reported XY tolerance; default uses the candidate settings')
    args=parser.parse_args(argv)
    result=audit_contours(args.source,args.candidate,args.report,args.tolerance_mm)
    print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
