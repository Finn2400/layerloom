"""Streaming, deposition-oriented comparison of planar Orca wall G-code.

One printed layer is resident at a time. Travel and path subdivision are ignored;
filament, wall class, layer Z and bead widths are not. Unsupported wall commands
fail closed instead of producing a misleading validation pass.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Iterator, TextIO

from shapely.geometry import MultiLineString
from shapely.ops import unary_union

_WORD = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))")
_WALL_NAMES = {
    "outer wall": "outer", "external perimeter": "outer",
    "inner wall": "inner", "perimeter": "inner",
    "overhang wall": "overhang", "overhang perimeter": "overhang",
}


@dataclass(frozen=True)
class WallSegment:
    start: tuple[float, float]
    end: tuple[float, float]
    tool: int
    feature: str
    width: float | None
    extrusion: float


@dataclass
class WallLayer:
    z: float
    segments: list[WallSegment]


def iter_wall_layers(stream: TextIO, *, max_layer_segments: int = 1_000_000) -> Iterator[WallLayer]:
    """Read G0/G1, G90/91, M82/83, G92, tools and Orca/Prusa annotations.

    Width annotations are required for width certification. Relative/absolute E
    are independent of XYZ mode. Arcs, nonplanar wall extrusion, inch units and
    volumetric E are explicitly unsupported. No entire-file read is performed.
    """
    xyz = [0.0, 0.0, 0.0]
    absolute_xyz = True
    absolute_e = True
    tool = 0
    es: dict[int, float] = {0: 0.0}
    feature: str | None = None
    width: float | None = None
    layer: WallLayer | None = None
    for lineno, raw in enumerate(stream, 1):
        code, _, comment = raw.partition(";")
        c = comment.strip()
        if c.lower().startswith(("type:", "feature:")):
            feature = _WALL_NAMES.get(c.split(":", 1)[1].strip().lower())
        if c.upper().startswith(("WIDTH:", "LINE_WIDTH:")):
            width = float(c.split(":", 1)[1].strip())
            if not math.isfinite(width) or width <= 0:
                raise ValueError(f"Invalid wall width at line {lineno}")
        code = re.sub(r"\([^)]*\)", "", code).strip().upper()
        if not code:
            continue
        # Strip optional line numbers and checksums; ignore unknown non-motion commands.
        code = re.sub(r"^N\d+\s*", "", code).split("*", 1)[0]
        match = re.match(r"([GMT])(\d+)", code)
        if not match:
            continue
        command = match.group(1) + str(int(match.group(2)))
        args = {k: float(v) for k, v in _WORD.findall(code[match.end():])}
        if not all(math.isfinite(v) for v in args.values()):
            raise ValueError(f"Non-finite G-code at line {lineno}")
        if command.startswith("T"):
            tool = int(match.group(2)); es.setdefault(tool, 0.0)
        elif command == "G90": absolute_xyz = True
        elif command == "G91": absolute_xyz = False
        elif command == "M82": absolute_e = True
        elif command == "M83": absolute_e = False
        elif command == "G20": raise ValueError("Inch G-code is not supported by the wall validator")
        elif command == "M200" and args.get("D", 0) > 0:
            raise ValueError("Volumetric E is not supported by the wall validator")
        elif command == "G92":
            for i, axis in enumerate("XYZ"):
                if axis in args: xyz[i] = args[axis]
            if "E" in args: es[tool] = args["E"]
        elif command in {"G0", "G1", "G2", "G3"}:
            nxt = [args.get(axis, xyz[i]) if absolute_xyz else xyz[i] + args.get(axis, 0.0)
                   for i, axis in enumerate("XYZ")]
            e = args.get("E", es[tool] if absolute_e else 0.0)
            delta = e - es[tool] if absolute_e else e
            if "E" in args: es[tool] = e if absolute_e else es[tool] + e
            xy_move = math.dist(xyz[:2], nxt[:2]) > 1e-12
            wall_motion = feature is not None and delta > 1e-10 and (xy_move or command in {"G2", "G3"})
            if wall_motion:
                if command in {"G2", "G3"}:
                    raise ValueError(f"Extruding wall arc at line {lineno}; disable arc fitting for validation")
                if abs(nxt[2] - xyz[2]) > 1e-6:
                    raise ValueError(f"Nonplanar wall at line {lineno}")
                z = round(nxt[2], 6)
                if layer is None or abs(z - layer.z) > 1e-6:
                    if layer is not None:
                        if z < layer.z:
                            raise ValueError("Wall layers are not monotonic; sequential-object/nonplanar mode unsupported")
                        yield layer
                    layer = WallLayer(z, [])
                layer.segments.append(WallSegment(tuple(xyz[:2]), tuple(nxt[:2]), tool, feature, width, delta))
                if len(layer.segments) > max_layer_segments:
                    raise MemoryError("A wall layer exceeds the configured segment budget")
            xyz = nxt
    if layer is not None:
        yield layer


def _geometry(segments):
    return unary_union(MultiLineString([(s.start, s.end) for s in segments]))


def hausdorff_bounds(a, b, *, residual_mm: float = .001) -> tuple[float, float]:
    """Conservative continuous-polyline Hausdorff enclosure.

    GEOS' vertex Hausdorff gives a lower bound. Full line coverage by round,
    inscribed polygonal buffers gives an upper bound (not sparse point samples).
    Bisection narrows the enclosure to residual_mm. Identical line sets return 0.
    """
    if residual_mm <= 0:
        raise ValueError("residual_mm must be positive")
    if a.equals(b): return 0.0, 0.0
    # GEOS discrete Hausdorff samples polygon boundaries, not filled interiors.
    # For swept-area sets start from zero; coverage still supplies both bounds.
    polygonal = a.geom_type in {'Polygon','MultiPolygon'} or b.geom_type in {'Polygon','MultiPolygon'}
    lower = 0.0 if polygonal else float(a.hausdorff_distance(b))
    upper = max(lower + residual_mm / 2, residual_mm / 2)
    def coverage(distance):
        # An inscribed q-segment quarter-circle contains the circle with radius
        # distance*cos(pi/(4*q)). Use this to bound failed coverage too, and
        # keep arc approximation error below one eighth of the error budget.
        cosine = max(-1.0, min(1.0, 1 - residual_mm / (8 * distance)))
        q = max(32, math.ceil(math.pi / (4 * math.acos(cosine))))
        if q > 4096:
            raise ValueError("Wall coordinates require excessive buffer precision")
        covered = a.difference(b.buffer(distance, quad_segs=q)).is_empty and b.difference(a.buffer(distance, quad_segs=q)).is_empty
        return covered, distance * math.cos(math.pi / (4*q))
    for _ in range(60):
        covered, inner_radius = coverage(upper)
        if covered: break
        lower = max(lower, inner_radius)
        upper *= 2
    else: raise RuntimeError("Could not bound wall displacement")
    while upper - lower > residual_mm:
        mid = (lower + upper) / 2
        covered, inner_radius = coverage(mid)
        if covered: upper = mid
        else: lower = max(lower, inner_radius)
    return lower, upper + 1e-9


def compare_layers(reference: WallLayer, candidate: WallLayer, *, tolerance_mm=.02, width_tolerance_mm=.02, residual_mm=.001):
    rows = []; passed = True
    keys = sorted({(s.tool, s.feature) for s in reference.segments + candidate.segments})
    for key in keys:
        a = [s for s in reference.segments if (s.tool, s.feature) == key]
        b = [s for s in candidate.segments if (s.tool, s.feature) == key]
        row = dict(tool=key[0], feature=key[1], reference_segments=len(a), candidate_segments=len(b))
        if not a or not b:
            row.update(passed=False, reason="missing wall feature or filament")
        else:
            ag, bg = _geometry(a), _geometry(b)
            lower, upper = hausdorff_bounds(ag, bg, residual_mm=residual_mm)
            widths_known = all(s.width is not None for s in a+b)
            # Compare each width-class against compatible target paths spatially;
            # min/max widths alone would miss swapped widths on different walls.
            widths_ok = widths_known
            if widths_ok:
                for source, target in [(a,b),(b,a)]:
                    for width in sorted({s.width for s in source}):
                        ss = [s for s in source if s.width == width]
                        tt = [s for s in target if abs(s.width-width) <= width_tolerance_mm+1e-9]
                        if not tt or not _geometry(ss).difference(_geometry(tt).buffer(tolerance_mm,quad_segs=32)).is_empty:
                            widths_ok = False; break
                    if not widths_ok: break
            row.update(max_displacement_lower_mm=lower, max_displacement_upper_mm=upper,
                       widths_known=widths_known,widths_passed=widths_ok,
                       reference_length_mm=ag.length,candidate_length_mm=bg.length,
                       passed=upper<=tolerance_mm and widths_ok)
        passed &= row['passed']; rows.append(row)
    # Also report physical wall-set distances with feature names combined. A
    # slicer may reclassify a short span as overhang without moving deposition;
    # keep the strict per-feature gate above and make that distinction visible.
    deposition=[]
    for tool in sorted({s.tool for s in reference.segments + candidate.segments}):
        aa=[s for s in reference.segments if s.tool==tool]
        bb=[s for s in candidate.segments if s.tool==tool]
        item=dict(tool=tool)
        if aa and bb:
            low,high=hausdorff_bounds(_geometry(aa),_geometry(bb),residual_mm=residual_mm)
            item.update(max_displacement_lower_mm=low,max_displacement_upper_mm=high,passed=high<=tolerance_mm)
        else:item.update(passed=False,reason='missing filament wall deposition')
        deposition.append(item)
    return dict(z_mm=reference.z, passed=bool(passed and abs(reference.z-candidate.z)<=1e-6), groups=rows,
                combined_feature_deposition=deposition)


def compare_gcode(reference, candidate, *, tolerance_mm=.02, width_tolerance_mm=.02, report_path=None):
    """Write per-layer JSONL incrementally; return only a compact aggregate."""
    if tolerance_mm <= 0 or width_tolerance_mm < 0: raise ValueError("Invalid tolerances")
    report = None
    summary = dict(reference=str(reference),candidate=str(candidate),tolerance_mm=tolerance_mm,
                   width_tolerance_mm=width_tolerance_mm,layers=0,failed_layers=0,combined_feature_geometry_failed_layers=0,
                   max_displacement_upper_mm=0.0,max_combined_feature_displacement_upper_mm=0.0,wall_paths_validated=False)
    try:
        if report_path: report = Path(report_path).open('w')
        with Path(reference).open() as a, Path(candidate).open() as b:
            ia,ib=iter_wall_layers(a),iter_wall_layers(b)
            la,lb=next(ia,None),next(ib,None)
            while la is not None or lb is not None:
                if la is None or (lb is not None and lb.z < la.z-1e-6):
                    row=dict(z_mm=lb.z,passed=False,reason='extra candidate wall layer');lb=next(ib,None)
                elif lb is None or la.z < lb.z-1e-6:
                    row=dict(z_mm=la.z,passed=False,reason='missing candidate wall layer');la=next(ia,None)
                else:
                    row=compare_layers(la,lb,tolerance_mm=tolerance_mm,width_tolerance_mm=width_tolerance_mm)
                    la,lb=next(ia,None),next(ib,None)
                summary['layers']+=1;summary['failed_layers']+=not row['passed']
                summary['combined_feature_geometry_failed_layers']+=('reason' in row or any(not g['passed'] for g in row.get('combined_feature_deposition',[])))
                for group in row.get('groups',[]):
                    summary['max_displacement_upper_mm']=max(summary['max_displacement_upper_mm'],group.get('max_displacement_upper_mm',0))
                for group in row.get('combined_feature_deposition',[]):
                    summary['max_combined_feature_displacement_upper_mm']=max(summary['max_combined_feature_displacement_upper_mm'],group.get('max_displacement_upper_mm',0))
                if report: report.write(json.dumps(row)+'\n')
        summary['wall_paths_validated']=summary['layers']>0 and summary['failed_layers']==0
    finally:
        if report:report.close()
    return summary


def render_wall_comparison(reference,candidate,layer_report,output_prefix,*,max_panels=6):
    """Render first/last and worst reported layers without retaining all paths."""
    import heapq
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from PIL import Image,ImageDraw
    worst=[];first=last=None
    with Path(layer_report).open() as f:
        for line in f:
            row=json.loads(line);z=row['z_mm']
            if first is None:first=z
            last=z
            error=max([g.get('max_displacement_upper_mm',0) for g in row.get('groups',[])]+[0])
            score=error+(1e6 if not row['passed'] else 0)
            heapq.heappush(worst,(score,z))
            if len(worst)>max(1,max_panels-2):heapq.heappop(worst)
    zs=sorted({z for z in [first,last]+[z for _,z in worst] if z is not None})
    if not zs:raise ValueError('No printed wall layers to visualize')
    output_prefix=Path(output_prefix);output_prefix.parent.mkdir(parents=True,exist_ok=True)
    images=[]
    with Path(reference).open() as a,Path(candidate).open() as b:
        ia,ib=iter_wall_layers(a),iter_wall_layers(b);la,lb=next(ia,None),next(ib,None)
        for i,z in enumerate(zs):
            while la and la.z<z-1e-6:la=next(ia,None)
            while lb and lb.z<z-1e-6:lb=next(ib,None)
            aa=la.segments if la and abs(la.z-z)<1e-6 else []
            bb=lb.segments if lb and abs(lb.z-z)<1e-6 else []
            fig,ax=plt.subplots(figsize=(6,5))
            if aa:ax.add_collection(LineCollection([(s.start,s.end) for s in aa],colors='#777777',linewidths=1.1,label='Reference'))
            if bb:ax.add_collection(LineCollection([(s.start,s.end) for s in bb],colors='#007ac2',linewidths=.55,linestyles='dashed',label='Candidate'))
            ax.autoscale();ax.set_aspect('equal');ax.set_title(f'Wall centerlines at Z={z:.6f} mm')
            ax.grid(alpha=.15);ax.legend();fig.tight_layout()
            path=Path(str(output_prefix)+f'-{i+1}.png');fig.savefig(path,dpi=150);plt.close(fig);images.append(path)
    # Contact sheet holds raster panels, not every layer's vector geometry.
    width=900;height=750;cols=2;rows=math.ceil(len(images)/cols)
    sheet=Image.new('RGB',(cols*width,rows*height),'white')
    for i,path in enumerate(images):
        with Image.open(path) as im:sheet.paste(im.convert('RGB').resize((width,height)),((i%cols)*width,(i//cols)*height))
    contact=Path(str(output_prefix)+'-contact.png');sheet.save(contact)
    return {'contact_sheet':str(contact),'panels':[str(p) for p in images],'z_mm':zs,'visual_signoff':'pending inspection'}


def diagnose_wall_differences(reference,candidate,layer_report,output_prefix,*,max_layers=4):
    """Bounded worst-layer centerline/nominal swept-footprint diagnostics.

    This does not change the strict gate. Swept areas model annotated width/2
    round buffers, not measured extrusion, flow or the whole solid infill.
    """
    import heapq
    import numpy as np
    import shapely
    from shapely.geometry import Point,box
    from shapely.ops import nearest_points
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    worst=[]
    with Path(layer_report).open() as f:
        for line in f:
            row=json.loads(line)
            for g in row.get('combined_feature_deposition',[]):
                item=(g.get('max_displacement_upper_mm',0),row['z_mm'],g['tool'])
                heapq.heappush(worst,item)
                if len(worst)>max_layers:heapq.heappop(worst)
    requested=sorted(worst,key=lambda item:(item[1],item[2]))
    output_prefix=Path(output_prefix);output_prefix.parent.mkdir(parents=True,exist_ok=True)
    details=[]
    def footprint(segments):
        values={s.width for s in segments}
        if None in values:raise ValueError('Swept-area diagnostic requires width annotations')
        widths=sorted(values)
        return unary_union([_geometry([s for s in segments if s.width==w]).buffer(w/2,quad_segs=32) for w in widths])
    def draw_geometry(ax,g,color,alpha=.2):
        polys=list(g.geoms) if hasattr(g,'geoms') else [g]
        for poly in polys:
            if poly.is_empty or poly.geom_type!='Polygon':continue
            xy=np.asarray(poly.exterior.coords);ax.fill(xy[:,0],xy[:,1],color=color,alpha=alpha)
            for ring in poly.interiors:
                xy=np.asarray(ring.coords);ax.plot(xy[:,0],xy[:,1],color=color,alpha=.5,lw=.4)
    with Path(reference).open() as aa,Path(candidate).open() as bb:
        ia,ib=iter_wall_layers(aa),iter_wall_layers(bb);la,lb=next(ia,None),next(ib,None)
        for _,z,tool in requested:
            while la and la.z<z-1e-6:la=next(ia,None)
            while lb and lb.z<z-1e-6:lb=next(ib,None)
            if not la or not lb or abs(la.z-z)>1e-6 or abs(lb.z-z)>1e-6:continue
            a=[s for s in la.segments if s.tool==tool];b=[s for s in lb.segments if s.tool==tool]
            if not a or not b:continue
            ag,bg=_geometry(a),_geometry(b);worst_point=None
            for label,source,target in [('reference',ag,bg),('candidate',bg,ag)]:
                dense=shapely.segmentize(source,max_segment_length=.002)
                coords=shapely.get_coordinates(dense)
                if len(coords)>500_000:raise MemoryError('Worst-point diagnostic exceeds per-layer sample budget')
                distances=shapely.distance(shapely.points(coords),target);i=int(np.argmax(distances))
                if worst_point is None or distances[i]>worst_point[0]:
                    point=Point(coords[i]);near=nearest_points(point,target)[1]
                    worst_point=(float(distances[i]),label,point,near)
            distance,label,point,near=worst_point
            af,bf=footprint(a),footprint(b);missing=af.difference(bf);extra=bf.difference(af)
            x,y=point.coords[0];window=box(x-.6,y-.6,x+.6,y+.6)
            low,high=hausdorff_bounds(af.boundary,bf.boundary)
            set_low,set_high=hausdorff_bounds(af,bf)
            def nearby(segments):
                return [dict(start=s.start,end=s.end,feature=s.feature,width_mm=s.width)
                        for s in segments if shapely.LineString([s.start,s.end]).distance(point)<.6]
            row=dict(z_mm=z,tool=tool,worst_sampled_centerline_distance_mm=distance,
                sampling_residual_mm=.001,direction=label,point=list(point.coords[0]),nearest=list(near.coords[0]),
                reference_wall_length_mm=ag.length,candidate_wall_length_mm=bg.length,
                reference_swept_area_mm2=af.area,candidate_swept_area_mm2=bf.area,
                missing_swept_area_mm2=missing.area,extra_swept_area_mm2=extra.area,
                swept_boundary_distance_lower_mm=low,swept_boundary_distance_upper_mm=high,
                swept_set_distance_lower_mm=set_low,swept_set_distance_upper_mm=set_high,
                local_missing_swept_area_mm2=missing.intersection(window).area,
                local_extra_swept_area_mm2=extra.intersection(window).area,
                reference_nearby_segments=nearby(a),candidate_nearby_segments=nearby(b))
            fig,(ax,zoom)=plt.subplots(1,2,figsize=(12,5))
            for segs,color,style in [(a,'#777777','solid'),(b,'#007ac2','dashed')]:
                for axis in [ax,zoom]:axis.add_collection(LineCollection([(s.start,s.end) for s in segs],colors=color,linewidths=.8,linestyles=style))
            ax.plot(x,y,'ro',ms=4);ax.autoscale();ax.set_aspect('equal');ax.set_title(f'Z={z:g} mm, tool {tool}: worst point')
            draw_geometry(zoom,af.intersection(window),'#777777',.12)
            draw_geometry(zoom,bf.intersection(window),'#007ac2',.12)
            draw_geometry(zoom,missing.intersection(window),'#d62728',.65)
            draw_geometry(zoom,extra.intersection(window),'#007ac2',.65)
            zoom.plot([x,near.x],[y,near.y],'-r',lw=1.4);zoom.plot(x,y,'ro',ms=3)
            zoom.set_xlim(x-.6,x+.6);zoom.set_ylim(y-.6,y+.6);zoom.set_aspect('equal')
            zoom.set_title(f'Gray reference / blue candidate; centerline {distance:.4f} mm\nRed missing / blue extra nominal swept area')
            for axis in [ax,zoom]:axis.grid(alpha=.2)
            fig.tight_layout();path=Path(str(output_prefix)+f'-{len(details)+1}.png');fig.savefig(path,dpi=160);plt.close(fig)
            row['image']=str(path);details.append(row)
    report=dict(reference=str(reference),candidate=str(candidate),strict_gate_unchanged=True,
                interpretation='Nominal wall-only swept footprint uses annotated width/2 round buffers. It excludes infill and is not a measured physical bead or print test.',layers=details)
    path=Path(str(output_prefix)+'.json');path.write_text(json.dumps(report,indent=2)+'\n')
    return dict(report=str(path),images=[r['image'] for r in details])

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('reference');p.add_argument('candidate')
    p.add_argument('--tolerance-mm',type=float,default=.02);p.add_argument('--width-tolerance-mm',type=float,default=.02)
    p.add_argument('--report',required=True);p.add_argument('--plot-prefix');p.add_argument('--diagnostic-prefix',help='Worst-point close-ups and nominal width/2 swept-footprint diagnostics')
    a=p.parse_args(argv)
    result=compare_gcode(a.reference,a.candidate,tolerance_mm=a.tolerance_mm,width_tolerance_mm=a.width_tolerance_mm,report_path=a.report)
    if a.plot_prefix:result['visuals']=render_wall_comparison(a.reference,a.candidate,a.report,a.plot_prefix)
    if a.diagnostic_prefix:result['footprint_diagnostics']=diagnose_wall_differences(a.reference,a.candidate,a.report,a.diagnostic_prefix)
    Path(a.report+'.summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    return 0 if result['wall_paths_validated'] else 1

if __name__=='__main__':raise SystemExit(main())
