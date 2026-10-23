"""Conservative solid repair with persistent source-face paint provenance.

Repair is explicit. Small holes are capped, zero-thickness opposite face pairs
are reported, overlapping positive solids are united, and only precision-
colliding vertices receive a bounded local displacement. There is no voxel
remeshing, decimation, or minimum component-size filter.
"""
from __future__ import annotations

from copy import deepcopy
import numpy as np
import trimesh
import manifold3d as mf
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .paint_model import PaintDocument, PaintMesh
from .surface_field_weave import mesh_defects


def _check_manifold_api():
    required = ((mf,'Mesh64'),(mf,'Mesh'),(mf.Manifold,'reserve_ids'),
                (mf.Manifold,'to_mesh64'),(mf.Manifold,'batch_boolean'))
    missing = [name for owner,name in required if not callable(getattr(owner,name,None))]
    if missing:
        raise RuntimeError("Paint repair requires manifold3d with Mesh64 and face provenance APIs "
                           "(tested with 3.5.2); upgrade manifold3d. Missing: "+', '.join(missing))


def _area_colors(vertices, faces, colors):
    tri = vertices[faces]
    area = np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    unique, inverse = np.unique(colors,axis=0,return_inverse=True)
    sums = np.bincount(inverse,weights=area)
    return {'#'+bytes(c).hex().upper():float(a) for c,a in zip(unique,sums)}


def _native_solid(vertices, faces, ids, *, single_precision=False):
    cls = mf.Mesh if single_precision else mf.Mesh64
    integer = np.uint32 if single_precision else np.uint64
    floating = np.float32 if single_precision else np.float64
    original = mf.Manifold.reserve_ids(1)
    solid = mf.Manifold(cls(np.ascontiguousarray(vertices,dtype=floating),
                           np.ascontiguousarray(faces,dtype=integer),
                           run_index=np.array([0,len(faces)*3],dtype=integer),
                           run_original_id=np.array([original],dtype=np.uint32),
                           face_id=np.asarray(ids,dtype=integer)))
    if solid.status() != mf.Error.NoError or solid.num_tri() == 0:
        raise ValueError(f"Repair could not construct a solid: {solid.status()}")
    return solid


def _precision_regularize(vertices, faces, translations, maximum):
    """Separate coincident vertex fans and restore sub-ULP triangle altitudes.

    Indexed topology is preserved. Only vertices implicated in a float32
    coordinate collision or zero-area triangle move, toward their local inward
    normal or along the local face plane. Movement is bounded and audited.
    """
    vertices = np.asarray(vertices,dtype=float).copy()
    baseline = vertices.copy()
    iterations = 0
    for iteration in range(16):
        changed = False
        for translation in translations:
            translation = np.asarray(translation)
            encoded = (vertices+translation).astype(np.float32).astype(float)
            tri = encoded[faces]
            normals = np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
            zero = np.all(normals==0,axis=1)
            _,inverse,counts = np.unique(encoded,axis=0,return_inverse=True,return_counts=True)
            duplicates = np.flatnonzero(counts[inverse]>1)
            _,first = np.unique(inverse,return_index=True)
            duplicates = np.setdiff1d(duplicates,first)
            if not len(duplicates) and not np.any(zero):
                continue
            changed = True
            vertex_normal = np.zeros_like(vertices)
            for corner in range(3):
                np.add.at(vertex_normal,faces[:,corner],normals)
            vertex_normal /= np.maximum(np.linalg.norm(vertex_normal,axis=1)[:,None],1e-99)
            for index in duplicates:
                step = max(float(np.abs(np.spacing(encoded[index].astype(np.float32))).max())*2,1e-6)
                direction = -vertex_normal[index]
                if np.linalg.norm(direction)<.1:
                    direction=np.array([1.,.3,.7]);direction/=np.linalg.norm(direction)
                vertices[index] += direction*step
            for face in faces[zero]:
                q=vertices[face]
                edge=q[1]-q[0]
                direction=np.cross(vertex_normal[face].sum(axis=0),edge)
                if np.linalg.norm(direction)<1e-20:
                    direction=np.cross(edge,[.123,.567,.891])
                if np.linalg.norm(direction)<1e-20:
                    raise ValueError("Local precision repair has no usable triangle direction")
                direction/=np.linalg.norm(direction)
                step=max(float(np.abs(np.spacing((q+translation).astype(np.float32))).max())*2,1e-6)
                vertices[face[2]] += direction*step
            distance=np.linalg.norm(vertices-baseline,axis=1)
            if distance.max(initial=0)>maximum:
                raise ValueError(f"Local precision repair exceeds {maximum:g} mm displacement limit")
        iterations=iteration+1
        if not changed:
            break
    mesh=trimesh.Trimesh(vertices,faces,process=False)
    checks=[mesh_defects(mesh,t) for t in translations]
    if any(any(row.values()) for row in checks):
        raise ValueError(f"Local precision repair did not resolve geometry: {checks}")
    return vertices, {'iterations':iterations,'vertices_moved':int(np.any(vertices!=baseline,axis=1).sum()),
                      'maximum_local_displacement_mm':float(np.linalg.norm(vertices-baseline,axis=1).max(initial=0)),
                      'checked_translations_mm':[list(map(float,t)) for t in translations],
                      'float32_checks':checks}


def _repair_mesh(item, translations, max_hole_diameter_mm, max_local_displacement_mm):
    # Exact welding preserves every face and color; it does not snap geometry.
    vertices,inverse=np.unique(item.vertices,axis=0,return_inverse=True)
    mesh=trimesh.Trimesh(vertices,inverse[item.faces],process=False)
    adjacency=mesh.face_adjacency
    _,labels=connected_components(coo_matrix((np.ones(len(adjacency)),(adjacency[:,0],adjacency[:,1])),
                                            shape=(len(mesh.faces),len(mesh.faces))).tocsr(),directed=False)
    current=[item.face_rgba];original=[item.source_rgba]
    next_id=len(item.faces)
    solids=[];components=[];caps=[]
    for label in np.unique(labels):
        ids=np.flatnonzero(labels==label)
        used,local_faces=np.unique(mesh.faces[ids],return_inverse=True)
        part=trimesh.Trimesh(mesh.vertices[used],local_faces.reshape(-1,3),process=False)
        row={'component':int(label),'source_faces':len(ids),'source_area_mm2':float(part.area)}
        # Two opposite triangles enclose no volume. Do not discard any other
        # component by size, area, closure or apparent importance.
        if len(ids)==2 and np.array_equal(np.sort(part.faces[0]),np.sort(part.faces[1])):
            n=part.face_normals
            if np.dot(n[0],n[1]) < -.999999:
                row.update(action='remove_opposite_zero_thickness_duplicate_pair',source_face_ids=ids.tolist(),volume_mm3=0.)
                components.append(row)
                continue
        old=len(part.faces)
        trimesh.repair.fill_holes(part)
        added=len(part.faces)-old
        if not part.is_watertight or not part.is_winding_consistent or part.volume<=0:
            raise ValueError(f"{item.name}: component {label} needs more than small-hole positive-solid repair")
        cap_current=[];cap_original=[]
        for cap in part.faces[old:]:
            triangle=part.vertices[cap]
            diameter=float(np.linalg.norm(triangle[:,None]-triangle[None,:],axis=2).max())
            if diameter>max_hole_diameter_mm:
                raise ValueError(f"{item.name}: hole cap span {diameter:g} mm exceeds repair limit")
            neighbors=np.flatnonzero(np.isin(part.faces[:old],cap).sum(axis=1)>=2)
            if not len(neighbors):
                raise ValueError("Cannot determine paint from a repaired hole's boundary")
            # Select one exact adjacent current/source paint pair, weighted by
            # its shared boundary length, so erase still has a distinct source.
            votes={}
            for neighbor in neighbors:
                shared=np.intersect1d(part.faces[neighbor],cap)
                weight=float(np.linalg.norm(part.vertices[shared[0]]-part.vertices[shared[1]]))
                key=bytes(item.face_rgba[ids[neighbor]])+bytes(item.source_rgba[ids[neighbor]])
                votes[key]=votes.get(key,0.)+weight
            winner=max(votes,key=lambda k:(votes[k],k))
            cap_current.append(np.frombuffer(winner[:4],dtype=np.uint8))
            cap_original.append(np.frombuffer(winner[4:],dtype=np.uint8))
            caps.append({'face_id':next_id+len(cap_current)-1,'component':int(label),'span_mm':diameter,
                         'area_mm2':float(np.linalg.norm(np.cross(triangle[1]-triangle[0],triangle[2]-triangle[0]))/2),
                         'boundary_paint_pair':winner.hex().upper()})
        if added:
            current.append(np.array(cap_current));original.append(np.array(cap_original))
        provenance=np.r_[ids,np.arange(next_id,next_id+added)]
        next_id+=added
        solids.append(_native_solid(part.vertices,part.faces,provenance))
        row.update(action='retain_and_union_positive_solid',added_cap_faces=added,volume_mm3=float(part.volume))
        components.append(row)
    if not solids:
        raise ValueError("Repair found no positive-volume solids")
    united=mf.Manifold.batch_boolean(solids,mf.OpType.Add)
    if united.status()!=mf.Error.NoError:
        raise ValueError(f"Solid union failed: {united.status()}")
    exact=united.to_mesh64()
    quantization_bound=float(np.linalg.norm(np.asarray(exact.vert_properties).astype(np.float32).astype(float)
                                            -np.asarray(exact.vert_properties),axis=1).max(initial=0))
    colors=np.concatenate(current);source_colors=np.concatenate(original)
    exact_ids=np.asarray(exact.face_id,dtype=np.int64)
    exterior_current=_area_colors(exact.vert_properties,exact.tri_verts,colors[exact_ids])
    exterior_source=_area_colors(exact.vert_properties,exact.tri_verts,source_colors[exact_ids])
    # Let the manifold constructor remove collapsed numerical intersections,
    # retaining face provenance. Remaining point contacts are regularized below.
    encoded=_native_solid(exact.vert_properties,exact.tri_verts,exact_ids,single_precision=True)
    output=encoded.to_mesh64()
    output_ids=np.asarray(output.face_id,dtype=np.int64)
    output_vertices,precision=_precision_regularize(output.vert_properties,np.asarray(output.tri_verts),
                                                   translations,max_local_displacement_mm)
    precision['maximum_float32_coordinate_rounding_mm']=quantization_bound
    precision['coordinate_rounding_plus_local_displacement_bound_mm']=(quantization_bound+
                                                                      precision['maximum_local_displacement_mm'])
    result=PaintMesh(item.key,item.name,output_vertices,np.asarray(output.tri_verts),
                     colors[output_ids],source_colors[output_ids])
    final_mesh=trimesh.Trimesh(result.vertices,result.faces,process=False)
    area_after=_area_colors(result.vertices,result.faces,result.face_rgba)
    source_area_after=_area_colors(result.vertices,result.faces,result.source_rgba)
    report={'key':item.key,'name':item.name,'input_vertices':len(item.vertices),'input_faces':len(item.faces),
            'output_vertices':len(result.vertices),'output_faces':len(result.faces),
            'exact_welded_vertices':len(item.vertices)-len(vertices),'source_components':components,
            'positive_solid_components_retained':len(solids),'hole_caps':caps,
            'input_bounds_mm':mesh.bounds.tolist(),'output_bounds_mm':final_mesh.bounds.tolist(),
            'input_signed_volume_mm3':float(mesh.volume),'capped_component_sum_volume_mm3':sum(s.volume() for s in solids),
            'exact_union_volume_mm3':float(united.volume()),'final_volume_mm3':float(final_mesh.volume),
            'final_minus_exact_union_volume_mm3':float(final_mesh.volume-united.volume()),
            'input_area_by_current_rgba_mm2':_area_colors(item.vertices,item.faces,item.face_rgba),
            'exact_exterior_area_by_current_rgba_mm2':exterior_current,
            'final_area_by_current_rgba_mm2':area_after,
            'input_area_by_source_rgba_mm2':_area_colors(item.vertices,item.faces,item.source_rgba),
            'exact_exterior_area_by_source_rgba_mm2':exterior_source,
            'final_area_by_source_rgba_mm2':source_area_after,
            'precision':precision,'all_output_faces_have_exact_source_or_cap_color_provenance':True,
            'source_face_provenance_ids':output_ids}
    return result,report


def repair_paint_document(document, *, max_hole_diameter_mm=.25, max_local_displacement_mm=.001,
                          include_provenance=False):
    """Return ``(repaired_document, audit)`` without modifying the input.

    Automatic caps are limited to triangle/quad holes of the stated span.
    Ambiguous/open/negative-volume components raise instead of disappearing.
    Both source placement and the native export's shared centered placement
    must pass float32 geometry checks.
    """
    document.validate()
    _check_manifold_api()
    if max_hole_diameter_mm<=0 or max_local_displacement_mm<=0:
        raise ValueError("Repair bounds must be positive")
    bounds=np.array([np.min([m.vertices.min(axis=0) for m in document.meshes],axis=0),
                     np.max([m.vertices.max(axis=0) for m in document.meshes],axis=0)])
    center=np.array([128,128,0.])-np.r_[bounds.mean(axis=0)[:2],bounds[0,2]]
    meshes=[];rows=[]
    for item in document.meshes:
        result,row=_repair_mesh(item,[np.zeros(3),center],max_hole_diameter_mm,max_local_displacement_mm)
        if not include_provenance:
            row.pop('source_face_provenance_ids')
        meshes.append(result);rows.append(row)
    audit={'format':'layerloom-paint-repair-v1','source_path':document.source_path,
           'maximum_hole_cap_span_mm':max_hole_diameter_mm,'maximum_local_displacement_limit_mm':max_local_displacement_mm,
           'method':'Exact vertex welding; triangle/quad hole caps; removal only of opposite zero-thickness pairs; all positive solids united with face provenance; bounded local float32 regularization',
           'interpretation':'Input color area includes buried overlapping surfaces; compare final color area with exact union exterior to assess visible paint preservation.',
           'meshes':rows}
    metadata=deepcopy(document.metadata)
    metadata['repair']={k:v for k,v in audit.items() if k!='meshes'}
    result=PaintDocument(meshes,document.source_path,metadata)
    return result,audit
