"""Experimental layer-contour weaving; input and output geometry stay on disk.

The bounded working set is one cross-section, not a complete input/output mesh.
Closed bands have a small between-plane guard, so caps are not coincident.
Generic 3-D decimation is never used.
"""
from __future__ import annotations
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
import hashlib, io, json, math, os, shutil, signal, sys, tempfile, time, zipfile
try:
    import resource
except ImportError:
    resource = None
from xml.sax.saxutils import quoteattr, escape
import numpy as np
import shapely
from shapely.geometry import LineString, Polygon, GeometryCollection
from shapely.ops import polygonize, unary_union, linemerge
from .contour_options import ContourOptions
from .contour_store import TriangleStore
from .tokens import ALL_TOKENS, TOKEN_HEX, COLOR_OBJECT_LABELS

GRID = 1e-7
BAND_GUARD_MM = .0001
EXPORT_GRID = 2.**-16


def polygons(g):
    if g.is_empty: return
    if g.geom_type == 'Polygon': yield g
    elif hasattr(g, 'geoms'):
        for child in g.geoms: yield from polygons(child)


def _signature(g):
    ps=list(polygons(g))
    return len(ps),sum(len(p.interiors) for p in ps)


def slice_part(store, part_id, z):
    """Slice triangle chunks with half-open edge rules and nonzero winding.

    A section that cannot fit the working budget fails rather than loading a
    complete mesh or silently switching to another backend.
    """
    chunks=[]; count=0; quad_pending={}; generic_present=False
    limit=int(getattr(store,'memory_budget_mib',1024)*1024**2*.25)
    for tri in store.iter_triangles(part_id,z=float(z)):
        hit=np.zeros((len(tri),3),bool); pts=np.zeros((len(tri),3,2))
        for e in range(3):
            a,b=tri[:,e],tri[:,(e+1)%3]
            mask=((a[:,2]<=z)&(b[:,2]>z))|((b[:,2]<=z)&(a[:,2]>z))
            hit[:,e]=mask
            t=np.zeros(len(tri));np.divide(z-a[:,2],b[:,2]-a[:,2],out=t,where=mask)
            pts[:,e]=a[:,:2]+t[:,None]*(b[:,:2]-a[:,:2])
        valid=np.flatnonzero(hit.sum(axis=1)==2)
        if not len(valid): continue
        xy=pts[valid][hit[valid]].reshape(-1,2,2)
        normal=np.cross(tri[valid,1]-tri[valid,0],tri[valid,2]-tri[valid,0])
        tangent=np.column_stack((-normal[:,1],normal[:,0]))
        flip=np.einsum('ij,ij->i',xy[:,1]-xy[:,0],tangent)<0
        xy[flip]=xy[flip,::-1]
        count+=len(xy)
        if count*1024>limit:
            raise MemoryError('Layer segment working set exceeds budget; increase --memory-budget-mib')
        # Recover a proven vertical rectangle from its complementary sidewall
        # triangles before quantizing intersections. Otherwise their diagonal
        # introduces an arbitrary intermediate point; rounding that point can
        # bend a straight wall and open/close a very narrow hole. A lone vertical
        # triangle is NOT a rectangle and retains its ordinary intersection.
        # Keys include the diagonal and orientation, so duplicates preserve
        # nonzero-winding multiplicity and opposite shells cannot cancel here.
        selected=tri[valid];handled=np.zeros(len(xy),bool);complete=[]
        same01=np.all(selected[:,0,:2]==selected[:,1,:2],axis=1)
        same02=np.all(selected[:,0,:2]==selected[:,2,:2],axis=1)
        same12=np.all(selected[:,1,:2]==selected[:,2,:2],axis=1)
        for j in np.flatnonzero(same01|same02|same12):
            face=selected[j]
            if same01[j]:u,v,w=0,1,2
            elif same02[j]:u,v,w=0,2,1
            else:u,v,w=1,2,0
            low,high=sorted((float(face[u,2]),float(face[v,2])))
            third=float(face[w,2])
            if low==high or third not in (low,high):continue
            repeated=tuple(face[u,:2]);other=tuple(face[w,:2])
            if repeated==other:continue
            a,b=sorted((repeated,other));kind=int(repeated==b)
            # 0 denotes diagonal A-low/B-high; 1 denotes A-high/B-low.
            diagonal=int(third==low) if kind==0 else int(third==high)
            direction=1 if np.dot(np.subtract(b,a),tangent[j])>0 else -1
            key=(a,b,low,high,diagonal,direction)
            waiting=quad_pending.setdefault(key,([],[]))
            if waiting[1-kind]:
                waiting[1-kind].pop()
                complete.append((a,b) if direction>0 else (b,a))
                if not waiting[0] and not waiting[1]:del quad_pending[key]
            else:waiting[kind].append(xy[j].copy())
            handled[j]=True
        if np.any(~handled):
            chunks.append(xy[~handled]);generic_present=True
        if complete:chunks.append(np.asarray(complete,dtype=float))
    for waiting in quad_pending.values():
        for pending in waiting:
            if pending:
                chunks.append(np.asarray(pending));generic_present=True
    if not chunks:return Polygon()
    segments=np.concatenate(chunks)
    # A section consisting entirely of complete vertical rectangles already
    # has the original boundary vertices. Preserve them exactly: an intervening
    # decimal grid can erase a long, extremely thin but valid binary-lattice
    # hole before subsequent export-grid regularization restores the vertices.
    # Mixed/unpaired sections still require the ordinary intersection policy.
    if generic_present:segments=np.rint(segments/GRID)*GRID
    segments=segments[np.any(segments[:,0]!=segments[:,1],axis=1)]
    if not len(segments):return Polygon()
    # Closed cross-sections must balance all directed endpoints.
    _,idx=np.unique(segments.reshape(-1,2),axis=0,return_inverse=True)
    balance=np.bincount(idx[::2],minlength=idx.max()+1)-np.bincount(idx[1::2],minlength=idx.max()+1)
    if np.any(balance):
        raise ValueError(f'Open/unbalanced cross-section in part {part_id} at Z={z:g}')
    network=unary_union([LineString(s) for s in segments])
    faces=list(polygonize(network))
    kept=[];a=segments[:,0];b=segments[:,1]
    for face in faces:
        p=face.representative_point();x,y=p.x,p.y
        cross=(b[:,0]-a[:,0])*(y-a[:,1])-(x-a[:,0])*(b[:,1]-a[:,1])
        winding=np.count_nonzero((a[:,1]<=y)&(b[:,1]>y)&(cross>0))-np.count_nonzero((a[:,1]>y)&(b[:,1]<=y)&(cross<0))
        if winding:kept.append(face)
    result=unary_union(kept) if kept else Polygon()
    if not generic_present and not result.is_empty:
        coords=shapely.get_coordinates(result)
        if np.array_equal(coords,np.rint(coords/EXPORT_GRID)*EXPORT_GRID):
            # Readback vertices already lie exactly on the export lattice.
            # Attach that precision model without re-normalizing topology:
            # GEOS's default snap-rounder can remove a very narrow valid ring
            # even when it moves none of its existing vertices.
            result=shapely.set_precision(result,EXPORT_GRID,mode='pointwise')
    return result


def layer_regions(store,z,phase,first_layer_token=None):
    regions=defaultdict(list);coordinates=0
    limit=getattr(store, 'memory_budget_mib', 1024)*1024**2*.5
    for part in store.parts:
        if part.noweave or part.id in getattr(store,"direct_copy_ids",set()):continue
        if not part.run:raise ValueError(f'Missing PAT recipe: {part.label}')
        if part.bounds is not None and not part.bounds[0,2]<=z<=part.bounds[1,2]:continue
        p=slice_part(store,part.id,z)
        if not p.is_empty:
            coordinates += shapely.get_num_coordinates(p)
            if coordinates*2048 > limit:
                raise MemoryError('Combined layer sections exceed working-memory budget')
            regions[first_layer_token or part.run[phase%len(part.run)]].append(p)
    # A common binary lattice avoids sub-ULP cracks where independent
    # source regions meet. This numerical regularization precedes both the
    # zero-simplification reference and all tolerance candidates.
    result={}
    for c,xs in regions.items():
        if len(xs)==1 and shapely.get_precision(xs[0])==EXPORT_GRID:
            # slice_part already certified this complete-quad section on the
            # export lattice. A redundant fixed-precision union of its
            # MultiPolygon can erase a valid long, sub-grid-width hole even
            # though no vertex needs rounding. There is nothing to merge.
            result[c]=xs[0]
        else:
            result[c]=unary_union([shapely.set_precision(g,EXPORT_GRID) for g in xs])
    return result


def fixed_regions(store, z):
    """Sections of untouched parts constrain neighboring woven contours."""
    result = {}; coordinates = 0
    limit = getattr(store, 'memory_budget_mib', 1024)*1024**2*.5
    for part in store.parts:
        if not (part.noweave or part.id in getattr(store, 'direct_copy_ids', set())):
            continue
        if part.bounds is not None and not part.bounds[0, 2] <= z <= part.bounds[1, 2]:
            continue
        section = slice_part(store, part.id, z)
        if not section.is_empty:
            coordinates += shapely.get_num_coordinates(section)
            if coordinates*2048 > limit:
                raise MemoryError('Fixed layer sections exceed working-memory budget')
            result[f'_fixed_{part.id}'] = shapely.set_precision(section, EXPORT_GRID)
    return result


def _within(a,b,tolerance):
    if a.is_empty or b.is_empty:return a.is_empty and b.is_empty
    # Clear only the boundary precision metadata, without moving coordinates.
    # Otherwise GEOS rounds buffer vertices onto the input lattice and can
    # expand a nominal .02-mm buffer past .02 mm, invalidating the certificate.
    left=shapely.set_precision(a.boundary,0)
    right=shapely.set_precision(b.boundary,0)
    if tolerance==0:return left.equals(right)
    # Inscribed polygon buffers are conservative subsets of a true distance
    # buffer: containment certifies the distance bound, unlike sparse samples.
    return left.covered_by(right.buffer(tolerance,quad_segs=16)) and right.covered_by(left.buffer(tolerance,quad_segs=16))


def _regions_within(reference, candidate, tolerance):
    """Certify each material, combined outline, and positive-area overlaps.

    Per-material topology alone cannot detect a hole opening or closing where
    several materials meet. Use the same precision model for every operation.
    """
    if set(reference) != set(candidate):
        return False
    def matches(a, b):
        return _signature(a) == _signature(b) and _within(a, b, tolerance)
    if not all(matches(g, candidate[c]) for c, g in reference.items()):
        return False
    if not matches(unary_union(list(reference.values())),
                   unary_union(list(candidate.values()))):
        return False
    colors = list(reference)
    for i, c in enumerate(colors):
        for d in colors[i + 1:]:
            old = unary_union(list(polygons(reference[c].intersection(reference[d]))))
            new = unary_union(list(polygons(candidate[c].intersection(candidate[d]))))
            if not matches(old, new):
                return False
    return True


def simplify_regions(regions,tolerance):
    """Simplify shared linework once, retaining coverage sets and overlaps."""
    original={c:g for c,g in regions.items() if not g.is_empty}
    if not original:return {},{'accepted_tolerance_mm':0.,'fallback':False,'max_boundary_distance_mm':0.}
    # Reserve precision for serialization and subsequent operations.
    attempts=[tolerance*.75,tolerance*.375,0.] if tolerance else [0.]
    for attempt in attempts:
        network=unary_union([g.boundary for g in original.values()])
        if network.geom_type=='LineString': chains=[network]
        else:
            merged=linemerge(network);chains=list(merged.geoms) if hasattr(merged,'geoms') else [merged]
        simplified=[line.simplify(attempt,preserve_topology=True) for line in chains]
        cells=list(polygonize(unary_union(simplified)))
        assigned=defaultdict(list)
        for cell in cells:
            p=cell.representative_point()
            for c,g in original.items():
                if g.covers(p):assigned[c].append(cell)
        candidate={c:unary_union(assigned[c]) if assigned[c] else Polygon() for c in original}
        valid=_regions_within(original,candidate,tolerance)
        if valid:
            distance=max(float(original[c].boundary.hausdorff_distance(candidate[c].boundary)) for c in original)
            return candidate,{'accepted_tolerance_mm':attempt,'fallback':attempt==0 and tolerance>0,'max_boundary_distance_mm':distance,'certified_bound_mm':tolerance}
    # Returning original rings is a valid exact fallback even if polygonization
    # has different numerical seam choices.
    return original,{'accepted_tolerance_mm':0.,'fallback':True,'max_boundary_distance_mm':0.,'certified_bound_mm':0.}


def _numerical_cleanup(g):
    """Remove only submicron, sub-nanometer-square polygonization debris.

    This is separately reported; it is not discretionary mesh simplification.
    """
    kept=[];removed=0.;count=0
    for p in polygons(g):
        def tiny(q):
            x0,y0,x1,y1=q.bounds
            return q.area<=1e-9 and math.hypot(x1-x0,y1-y0)<=.001
        if tiny(p):removed+=p.area;count+=1;continue
        holes=[]
        for ring in p.interiors:
            hole=Polygon(ring)
            if tiny(hole):removed+=hole.area;count+=1
            else:holes.append(ring.coords)
        kept.append(Polygon(p.exterior.coords,holes))
    if count == 0:
        return g, removed, count
    result = unary_union(kept)
    precision = shapely.get_precision(g)
    if precision:
        result = shapely.set_precision(result, precision)
    return result,removed,count


def extrude_polygon(poly,low,high):
    import trimesh
    if not hasattr(shapely,'constrained_delaunay_triangles'):
        raise RuntimeError('layer-contour requires Shapely >=2.1 with GEOS >=3.13')
    triangles=shapely.constrained_delaunay_triangles(poly)
    points=np.concatenate([np.asarray(t.exterior.coords)[:3] for t in triangles.geoms])
    vertices,idx=np.unique(points,axis=0,return_inverse=True)
    cap=idx.reshape(-1,3)
    t=vertices[cap];cross=(t[:,1,0]-t[:,0,0])*(t[:,2,1]-t[:,0,1])-(t[:,1,1]-t[:,0,1])*(t[:,2,0]-t[:,0,0])
    cap[cross<0]=cap[cross<0,::-1]
    edges=np.concatenate([cap[:,[0,1]],cap[:,[1,2]],cap[:,[2,0]]])
    _,inv,counts=np.unique(np.sort(edges,axis=1),axis=0,return_inverse=True,return_counts=True)
    rim=edges[counts[inv]==1]
    # A valid polygon may have boundary rings touching at a point. Keep the
    # cap's incident triangle fans separate so extrusion does not create a
    # four-face vertical edge at that coordinate.
    boundary_degree=np.bincount(rim.ravel(),minlength=len(vertices))
    for vertex in np.flatnonzero(boundary_degree>2):
        incident=np.flatnonzero(np.any(cap==vertex,axis=1));parents=list(range(len(incident)))
        def root(j):
            while parents[j]!=j:parents[j]=parents[parents[j]];j=parents[j]
            return j
        neighbors={}
        for j,faceid in enumerate(incident):
            for other in cap[faceid]:
                if other==vertex:continue
                if int(other) in neighbors:
                    x,y=root(j),root(neighbors[int(other)])
                    if x!=y:parents[y]=x
                else:neighbors[int(other)]=j
        fans=defaultdict(list)
        for j,faceid in enumerate(incident):fans[root(j)].append(faceid)
        for ids in list(fans.values())[1:]:
            new=len(vertices);vertices=np.vstack([vertices,vertices[vertex]])
            for faceid in ids:cap[faceid,cap[faceid]==vertex]=new
    edges=np.concatenate([cap[:,[0,1]],cap[:,[1,2]],cap[:,[2,0]]])
    _,inv,counts=np.unique(np.sort(edges,axis=1),axis=0,return_inverse=True,return_counts=True)
    rim=edges[counts[inv]==1];n=len(vertices)
    bottom=np.column_stack([vertices,np.full(n,low)]);top=np.column_stack([vertices,np.full(n,high)])
    side1=np.column_stack([rim[:,0],rim[:,1],rim[:,1]+n]);side2=np.column_stack([rim[:,0],rim[:,1]+n,rim[:,0]+n])
    mesh=trimesh.Trimesh(np.vstack([bottom,top]),np.vstack([cap[:,::-1],cap+n,side1,side2]),process=False)
    if not mesh.is_volume:raise ValueError(f'Layer extrusion is not a closed oriented solid: watertight={mesh.is_watertight}, winding={mesh.is_winding_consistent}, area={poly.area}, polygon_valid={poly.is_valid}, low={low}, cap_triangles={len(cap)}, cap_zero={int((cross==0).sum())}')
    # Keep the binary XY lattice exact. Decimal rounding can close a very thin
    # long hole even when every vertex moves only a fraction of a nanometre.
    v=np.asarray(mesh.vertices);f=np.asarray(mesh.faces,dtype=np.int64)
    for coords in [v,v.astype(np.float32).astype(float)]:
        t=coords[f];cross=np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0])
        if np.any(np.einsum('ij,ij->i',cross,cross)==0):raise ValueError('Layer has collapsed triangles at exported precision')
    return v,f


class _Spool:
    def __init__(self,path,name):
        self.name=name;self.vpath=path/(name+'.v');self.fpath=path/(name+'.f')
        self.v=self.vpath.open('wb');self.f=self.fpath.open('wb');self.vertices=0;self.triangles=0
        self.source_color=None;self.source_extruder=None
    def append(self,v,f):
        np.asarray(v,dtype='<f8').tofile(self.v)
        (np.asarray(f,dtype='<u8')+self.vertices).tofile(self.f)
        self.vertices+=len(v);self.triangles+=len(f)
    def close(self):self.v.close();self.f.close()


def _arrays(path,dtype,chunk=32768):
    with path.open('rb') as fp:
        while True:
            a=np.fromfile(fp,dtype=dtype,count=chunk*3)
            if not len(a):break
            yield a.reshape(-1,3)


def _export(spools,path,manifest):
    from .export import _content_types_xml,_rels_root_xml
    colors = [TOKEN_HEX[token].upper() + 'FF' for token in ALL_TOKENS]
    for spool in spools.values():
        if spool.source_color and spool.source_color not in colors:
            colors.append(spool.source_color)
    def check_space():
        if shutil.disk_usage(path.parent).free <= manifest.get('reserve_disk_bytes', 0):
            raise OSError('Output disk reserve exhausted')
    check_space()
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        z.writestr('[Content_Types].xml',_content_types_xml());z.writestr('_rels/.rels',_rels_root_xml())
        with z.open('3D/3dmodel.model','w',force_zip64=True) as raw:
            out=io.TextIOWrapper(io.BufferedWriter(raw,1024**2),encoding='utf8')
            out.write('<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:layerloom="https://layerloom.dev/source-color/1" unit="millimeter"><resources>')
            out.write('<basematerials id="1">')
            for index,color in enumerate(colors):
                name=ALL_TOKENS[index] if index<len(ALL_TOKENS) else color
                out.write(f'<base name={quoteattr(name)} displaycolor={quoteattr(color)}/>')
            out.write('</basematerials>')
            for i,(token,s) in enumerate(spools.items(),2):
                name=f'{s.name}__PAT_{token}__' if token in ALL_TOKENS else s.name
                color=ALL_TOKENS.index(token) if token in ALL_TOKENS else (colors.index(s.source_color) if s.source_color else None)
                assignment=f' pid="1" pindex="{color}"' if color is not None else ''
                out.write(f'<object id="{i}" type="model" name={quoteattr(name)}{assignment}>')
                metadata = {}
                if token not in ALL_TOKENS:
                    metadata['noweave'] = 'true'
                if s.source_color:
                    metadata['source_hex'] = s.source_color
                if s.source_extruder is not None:
                    metadata['source_extruder'] = s.source_extruder
                if metadata:
                    # Object metadata belongs inside this core element. Custom
                    # names use the declared namespace shared with GLB import.
                    out.write('<metadatagroup>')
                    for key, value in metadata.items():
                        out.write(f'<metadata name={quoteattr("layerloom:" + key)} type="string" preserve="1">{escape(value)}</metadata>')
                    out.write('</metadatagroup>')
                out.write('<mesh><vertices>')
                for vv in _arrays(s.vpath,'<f8'):
                    check_space()
                    out.write(''.join(f'<vertex x="{x:.17g}" y="{y:.17g}" z="{zz:.17g}"/>' for x,y,zz in vv))
                out.write('</vertices><triangles>')
                for ff in _arrays(s.fpath,'<u8'):
                    check_space()
                    out.write(''.join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a,b,c in ff))
                out.write('</triangles></mesh></object>')
            assembly=len(spools)+2
            out.write(f'<object id="{assembly}" type="model" name="LayerLoom contour weave"><components>')
            for i in range(2,assembly):out.write(f'<component objectid="{i}"/>')
            out.write(f'</components></object></resources><build><item objectid="{assembly}"/></build></model>')
            out.flush();out.detach().flush()
        z.writestr('Metadata/layerloom_contour.json',json.dumps(manifest,indent=2))
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:raise ValueError('3MF ZIP integrity failure')


@contextmanager
def _cancellation():
    previous=None
    try:
        if __import__('threading').current_thread() is __import__('threading').main_thread():
            previous=signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt('Weave cancelled')))
        yield
    finally:
        if previous is not None:signal.signal(signal.SIGTERM,previous)


def weave_contours(input_path,output_path,*,step=.08,first_layer_color='auto',options=None,verbose=True):
    options=(options or ContourOptions()).validate(step)
    dest=Path(output_path).absolute();dest.parent.mkdir(parents=True,exist_ok=True)
    if Path(input_path).resolve()==dest.resolve():raise ValueError('Output must not replace the input')
    started=time.monotonic();budget=options.memory_budget_mib*1024**2
    reserve=int(options.reserve_disk_gib*1024**3)
    scratch=options.scratch_dir or str(dest.parent)
    Path(scratch).mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(scratch).free<=reserve:raise OSError('Insufficient scratch space: disk reserve would be exhausted')
    candidate=None;sidecars=[];spools={};peak_scratch=0;max_error=0.;fallback_count=0;debris_area=0.;debris_count=0
    try:
        with _cancellation(),tempfile.TemporaryDirectory(prefix='layerloom_contour_',dir=scratch) as td:
            work=Path(td)
            ingest_started=time.monotonic()
            with TriangleStore(input_path,work,memory_budget_mib=options.memory_budget_mib,reserve_disk_bytes=reserve) as store:
                ingest_seconds=time.monotonic()-ingest_started;layers_started=time.monotonic();first_layer_seconds=None
                z0=float(store.bounds[0,2]);ztop=float(store.bounds[1,2]);first=options.first_layer_height or step
                layer_count=(1+max(0,int(math.ceil((ztop-z0-first-step*.5)/step-1e-10)))) if z0+first*.5<ztop-1e-10 else 0
                if not layer_count:raise ValueError('Model is shorter than the first slicing plane')
                def schedule():
                    lo=z0
                    for index in range(layer_count):
                        hi=z0+first+index*step
                        if hi<=lo:raise ValueError('Invalid layer schedule')
                        yield lo,hi,(lo+hi)*.5
                        lo=hi
                token=None
                if first_layer_color not in (None,'none','off'):
                    if first_layer_color=='auto':
                        rr=layer_regions(store,z0+first*.5,0)
                        token=max(ALL_TOKENS,key=lambda t:rr[t].area if t in rr else -1)
                    elif first_layer_color in ALL_TOKENS:token=first_layer_color
                    else:raise ValueError('Invalid first layer color')
                # A fixed source mesh may bypass contour reconstruction only
                # when another woven region cannot approach it within the
                # simplification/numerical allowance. Otherwise the fixed
                # boundary must participate in shared topology certification.
                # Bounds are conservative in all three dimensions; touching
                # and nearly touching parts deliberately take the slower path.
                woven_parts = [p for p in store.parts if not p.noweave]
                padding = options.xy_tolerance_mm + 2 * EXPORT_GRID
                store.direct_copy_ids = set()
                for part in woven_parts:
                    if not part.run or len(part.run) != 1 or (token is not None and token != part.run):
                        continue
                    if part.bounds is None:
                        continue
                    nearby = any(
                        other.id != part.id and (
                            other.bounds is None or (
                                np.all(part.bounds[0] - padding <= other.bounds[1]) and
                                np.all(other.bounds[0] <= part.bounds[1] + padding)
                            )
                        )
                        for other in woven_parts
                    )
                    if not nearby:
                        store.direct_copy_ids.add(part.id)
                for c in ALL_TOKENS:spools[c]=_Spool(work,COLOR_OBJECT_LABELS[c])
                for part in store.parts:
                    if part.id not in store.direct_copy_ids:continue
                    spool=spools[part.run];base=spool.vertices
                    for vv in store.iter_vertices(part.id):np.asarray(vv,dtype='<f8').tofile(spool.v);spool.vertices+=len(vv)
                    for ff in store.iter_faces(part.id):(np.asarray(ff,dtype='<u8')+base).tofile(spool.f);spool.triangles+=len(ff)
                audit_tmp=work/'layers.jsonl'
                with audit_tmp.open('w') as audit:
                    for i,(lo,hi,z) in enumerate(schedule()):
                        phase=int(math.floor((z-z0)/step+1e-8))
                        reference=layer_regions(store,z,phase,token if i==0 else None)
                        immutable = fixed_regions(store, z)
                        reference.update(immutable)
                        for c,g in list(reference.items()):
                            reference[c],area,removed=_numerical_cleanup(g)
                            debris_area+=area;debris_count+=removed
                        if sum(shapely.get_num_coordinates(g) for g in reference.values())*2048>budget*.5:
                            raise MemoryError('Reference section exceeds working-memory budget')
                        # Float32-safe binary XY lattice. Numerical quantization is measured
                        # separately from optional contour simplification.
                        prepared={c:shapely.set_precision(g,EXPORT_GRID) for c,g in reference.items()}
                        for c,g in prepared.items():
                            if _signature(reference[c])!=_signature(g):
                                raise ValueError(f'Export precision changes section topology at layer {i}, {c}')
                            if not _within(reference[c],g,EXPORT_GRID*2):
                                raise ValueError('Export quantization exceeded numerical tolerance')
                        regions,metrics=simplify_regions(prepared,options.xy_tolerance_mm)
                        regions={c:shapely.set_precision(g,EXPORT_GRID) for c,g in regions.items()}
                        if (not _regions_within(reference,regions,max(options.xy_tolerance_mm,EXPORT_GRID*2))
                                or any(not regions[c].equals(reference[c]) for c in immutable)):
                            regions=prepared;metrics['fallback']=True
                        metrics['max_boundary_distance_mm']=max((reference[c].boundary.hausdorff_distance(g.boundary) for c,g in regions.items()),default=0.)
                        if sum(shapely.get_num_coordinates(g) for g in regions.values())*1024>budget*.5:
                            raise MemoryError('Polygon layer exceeds memory budget')
                        guard=min(BAND_GUARD_MM,(hi-lo)*.01)
                        low=lo if i==0 else lo+guard*.5
                        high=min(hi,ztop) if i==layer_count-1 else hi-guard*.5
                        count=0
                        for c,geometry in regions.items():
                            if c in immutable:
                                continue
                            for poly in polygons(geometry):
                                v,f=extrude_polygon(poly,low,high);spools[c].append(v,f);count+=len(f)
                        row=dict(layer=i,low_z=lo,high_z=hi,slice_z=z,phase=phase,triangles=count,**metrics)
                        audit.write(json.dumps(row)+'\n')
                        if first_layer_seconds is None:first_layer_seconds=time.monotonic()-started
                        max_error=max(max_error,metrics['max_boundary_distance_mm']);fallback_count+=int(metrics['fallback'])
                        if i%10==0 or i==layer_count-1:
                            if shutil.disk_usage(work).free<=reserve:raise OSError('Scratch disk reserve exhausted')
                            peak_scratch=max(peak_scratch,sum(p.stat().st_size for p in work.rglob('*') if p.is_file()))
                            if verbose:print(f'[contour] layer {i+1}/{layer_count} ({100*(i+1)/layer_count:.1f}%)',flush=True)
                # NOWEAVE geometry is copied without a mesh-sized allocation.
                for part in store.parts:
                    if not part.noweave:continue
                    key=f'passthrough_{part.id}';s=_Spool(work,key);s.name=part.label
                    s.source_color=part.source_color;s.source_extruder=part.source_extruder
                    for vv in store.iter_vertices(part.id):np.asarray(vv,dtype='<f8').tofile(s.v);s.vertices+=len(vv)
                    for ff in store.iter_faces(part.id):np.asarray(ff,dtype='<u8').tofile(s.f);s.triangles+=len(ff)
                    spools[key]=s
                for s in spools.values():s.close()
                spools={c:s for c,s in spools.items() if s.triangles}
                if not spools:raise ValueError('No printable geometry was generated')
                ordered={c:spools[c] for c in ALL_TOKENS if c in spools}
                ordered.update({c:s for c,s in spools.items() if c not in ALL_TOKENS});spools=ordered
                report=dict(mode='layer-contour',experimental=True,input_path=str(Path(input_path).absolute()),input_sha256=store.source_sha256,
                    algorithm_revision='contour-prisms-2',coordinate_encoding='float64-roundtrip-17g',
                    input_triangles=store.triangle_count,direct_copy_parts=len(store.direct_copy_ids),output_triangles=sum(s.triangles for s in spools.values()),output_vertices=sum(s.vertices for s in spools.values()),
                    xy_tolerance_mm=options.xy_tolerance_mm,first_layer_height=first,layer_height=step,first_layer_color=token,first_layer_token=token,layers=layer_count,
                    source_bounds=store.bounds.tolist(),contour_validated=True,toolpaths_validated=False,full_slice_tested=False,
                    serialized_contour_validated=False,reserve_disk_bytes=reserve,
                    passthrough_appearance=[dict(name=p.label,display_color=p.source_color,
                        source_extruder=p.source_extruder,printer_assignment_preserved=False)
                        for p in store.parts if p.noweave],
                    max_contour_distance_mm=max_error,exact_fallback_layers=fallback_count,
                    stage_seconds={'ingest_and_index':ingest_seconds,'layers_and_spools':time.monotonic()-layers_started},time_to_first_layer_seconds=first_layer_seconds,memory_budget_mib=options.memory_budget_mib,peak_scratch_bytes=peak_scratch,band_guard_mm=BAND_GUARD_MM,numerical_export_grid_mm=EXPORT_GRID,numerical_intersection_grid_mm=GRID,numerical_debris_area_sum_mm2=debris_area,numerical_debris_count=debris_count,
                    representation='Closed layer prisms with between-plane numerical guard; no whole-model Boolean or STL roundtrip',
                    limitations=['Between-plane geometry is specialized to this layer schedule; do not rotate, scale, or change layer heights.',
                                  'Wall displacement and seam behavior require separate slicing comparison.',
                                  'Source contours are regularized on the reported numerical binary grid before the simplification reference; sub-grid cracks can close.',
                                  'NOWEAVE display colors are preserved when uniform and known; source extruder numbers are provenance only and require printer mapping.',
                                  'Copied source meshes are not repaired or independently certified as float32-safe closed solids.',
                                  'Single-layer complexity is budget-limited; oversized sections fail instead of using legacy memory-heavy fallback.'])
                fd,temp=tempfile.mkstemp(prefix='.'+dest.stem+'_',suffix='.3mf',dir=dest.parent);os.close(fd);candidate=Path(temp)
                export_started=time.monotonic()
                _export(spools,candidate,report)
                report["stage_seconds"]["export_and_zip_validation"]=time.monotonic()-export_started
                report.update(output_path=str(dest),output_bytes=candidate.stat().st_size,elapsed_seconds=time.monotonic()-started,
                    peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024) if resource else None)
                digest=hashlib.sha256()
                with candidate.open('rb') as fp:
                    for block in iter(lambda:fp.read(1024**2),b''):digest.update(block)
                report['output_sha256']=digest.hexdigest()
                # Only the final replacement is atomic; reports never claim wall validation.
                report_path=dest.with_suffix('.report.json');layer_path=dest.with_suffix('.layers.jsonl')
                staged=[]
                for target in [report_path,layer_path]:
                    fd,name=tempfile.mkstemp(prefix='.'+target.name+'_',dir=dest.parent);os.close(fd)
                    path=Path(name);sidecars.append(path);staged.append(path)
                staged[0].write_text(json.dumps(report,indent=2)+'\n')
                shutil.copyfile(audit_tmp,staged[1])
                # The 3MF is the commit marker; no failure while staging reports
                # may replace an existing final. Sidecars carry the model hash.
                os.replace(staged[0],report_path);os.replace(staged[1],layer_path)
                os.replace(candidate,dest);candidate=None
                if verbose:print(f'[contour] complete: {dest} ({report["output_triangles"]} triangles)',flush=True)
                return report
    finally:
        for s in spools.values():s.close()
        if candidate is not None:candidate.unlink(missing_ok=True)
        for path in sidecars:path.unlink(missing_ok=True)
