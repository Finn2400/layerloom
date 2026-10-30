"""Preserve tiny disconnected solids through geometry-only 3MF encoding."""
from __future__ import annotations

import numpy as np
import trimesh

from .paint_repair import _precision_regularize
from .surface_field_weave import mesh_defects


def _signed_volume(vertices, faces):
    shifted=np.asarray(vertices,dtype=float)-np.asarray(vertices,dtype=float).mean(axis=0)
    tri=shifted[np.asarray(faces)]
    return float(np.einsum('ij,ij->i',tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6.)


def _encoding(mesh):
    center=mesh.bounds.mean(axis=0)
    # The shared 3MF writer formats both local vertices and part transforms to
    # nine decimal places. Even a sub-nanometer rounding difference can change
    # a subsequent float32 tie at the plate position, so mirror it exactly.
    def decimal9(values):
        values=np.asarray(values)
        return np.array([float(f'{float(value):.9f}') for value in values.flat]).reshape(values.shape)
    local=decimal9(np.asarray(mesh.vertices)-center).astype(np.float32).astype(float)
    center=decimal9(center)
    encoded=trimesh.Trimesh(local,mesh.faces,process=False)
    placed=(local+center+np.array([128.,128.,0.])).astype(np.float32).astype(float)
    return center,encoded,placed


def _outward_directions(mesh):
    """Area-weighted source directions without normalizing tiny face areas."""
    vertices=np.asarray(mesh.vertices)
    faces=np.asarray(mesh.faces)
    tri=vertices[faces]
    normals=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
    directions=np.zeros_like(vertices)
    for corner in range(3):
        np.add.at(directions,faces[:,corner],normals)
    return directions/np.maximum(np.linalg.norm(directions,axis=1)[:,None],1e-99)


def _encoded_checks(mesh):
    center,encoded,placed=_encoding(mesh)
    local_volume=_signed_volume(encoded.vertices,mesh.faces)
    placed_volume=_signed_volume(placed,mesh.faces)
    local_defects=mesh_defects(encoded)
    placed_mesh=trimesh.Trimesh(placed,mesh.faces,process=False)
    placed_defects=mesh_defects(placed_mesh)
    # Volume accumulated at world coordinates can suffer cancellation for these
    # microscopic shells. Use the centered determinant, including after placing.
    for candidate,defects,volume in ((encoded,local_defects,local_volume),
                                    (placed_mesh,placed_defects,placed_volume)):
        defects['not_closed_oriented_volume']=int(
            not candidate.is_watertight or not candidate.is_winding_consistent or volume<=0)
    return center,encoded,placed,local_defects,placed_defects,local_volume,placed_volume


def prepare_sub_layer_mesh(mesh, shared_translation, *, max_displacement_mm=.001):
    """Return a translated mesh and audit for a separate material-assigned part.

    Face indices and count never change. A small vertex correction is attempted
    only if the actual local-float32 / placed-float32 3MF pipeline needs it.
    The result is checked again after its final bounds determine the part center.
    No Manifold conversion is used: it may discard these sub-tolerance solids.
    """
    if not np.isfinite(max_displacement_mm) or max_displacement_mm<=0:
        raise ValueError('Sub-layer displacement limit must be positive')
    translation=np.asarray(shared_translation,dtype=float)
    if translation.shape!=(3,) or not np.isfinite(translation).all():
        raise ValueError('Shared translation must be a finite XYZ vector')
    faces=np.asarray(mesh.faces,dtype=np.int64).copy()
    baseline=np.asarray(mesh.vertices,dtype=float)+translation
    result=trimesh.Trimesh(baseline.copy(),faces,process=False)
    initial_volume=_signed_volume(baseline,faces)
    if not result.is_watertight or not result.is_winding_consistent or initial_volume<=0:
        raise ValueError('Sub-layer retention requires a closed, positively oriented source shell')
    original_area=float(result.area)
    repairs=[]
    for attempt in range(12):
        center,encoded,placed,local_defects,placed_defects,local_volume,placed_volume=_encoded_checks(result)
        if (not any(local_defects.values()) and not any(placed_defects.values())
                and local_volume>0 and placed_volume>0):
            break
        # An inward collision correction can invert a very flat positive shell.
        # First try the smallest bounded outward steps and accept only an exact
        # check of both storage and placed coordinates. The source is unchanged
        # unless a candidate passes every check.
        directions=_outward_directions(result)
        outward=None
        for step in (1e-6,2e-6,4e-6,8e-6,16e-6,32e-6,64e-6):
            candidate=trimesh.Trimesh(np.asarray(result.vertices)+directions*step,faces,process=False)
            if np.linalg.norm(candidate.vertices-baseline,axis=1).max(initial=0)>max_displacement_mm:
                break
            checks=_encoded_checks(candidate)
            if not any(checks[3].values()) and not any(checks[4].values()):
                outward=candidate
                break
        if outward is not None:
            result=outward
            repairs.append({'positive_volume_outward_step_mm':step})
            continue
        fixed,detail=_precision_regularize(encoded.vertices,faces,
                                          [np.zeros(3),center+[128.,128.,0.]],max_displacement_mm)
        result.vertices=fixed+center
        repairs.append(detail)
        if _signed_volume(_encoding(result)[2],faces)<=0:
            # A nearly flat shell can invert after placement without an edge
            # collision. A bounded outward displacement restores positive volume.
            step=max(float(np.abs(np.spacing(placed.astype(np.float32))).max())*2,1e-6)
            result.vertices=np.asarray(result.vertices)+_outward_directions(result)*step
            repairs.append({'positive_volume_outward_step_mm':step})
        if np.linalg.norm(np.asarray(result.vertices)-baseline,axis=1).max(initial=0)>max_displacement_mm:
            raise ValueError('Sub-layer float32 repair exceeds the displacement limit')
    else:
        raise ValueError('Sub-layer geometry cannot be preserved through float32 placement within bounded repair')
    displacement=float(np.linalg.norm(np.asarray(result.vertices)-baseline,axis=1).max(initial=0))
    decoded_local=np.asarray(encoded.vertices)+center
    decoded_displacement=float(np.linalg.norm(decoded_local-baseline,axis=1).max(initial=0))
    placed_baseline=baseline+[128.,128.,0.]
    placed_displacement=float(np.linalg.norm(placed-placed_baseline,axis=1).max(initial=0))
    if max(displacement,decoded_displacement,placed_displacement)>max_displacement_mm:
        raise ValueError('Sub-layer encoded displacement exceeds the repair limit')
    # A displacement envelope is appropriate for extremely flat shells whose
    # relative volume error can be arbitrarily large. Report the absolute error.
    envelope=(original_area+float(result.area))*max(placed_displacement,decoded_displacement,displacement)
    envelope+=4*np.pi*max(placed_displacement,decoded_displacement,displacement)**3/3
    volume_change=abs(placed_volume-initial_volume)
    if volume_change>max(envelope*2,1e-24):
        raise ValueError('Sub-layer volume change exceeds its measured local displacement envelope')
    if not np.array_equal(result.faces,faces):
        raise AssertionError('Sub-layer retention changed triangle topology')
    audit={'policy':'Retain each sub-layer shell as a separate material geometry part; no mesh boolean or component removal',
           'source_vertices':len(baseline),'source_triangles':len(faces),'output_vertices':len(result.vertices),
           'output_triangles':len(result.faces),'topology_and_face_count_preserved':True,
           'shared_translation_mm':translation.tolist(),'part_center_mm':center.tolist(),
           'coordinate_encoding':'Nine-decimal XML vertices and part transform, then local and placed float32',
           'maximum_vertex_displacement_mm':displacement,'maximum_decoded_local_displacement_mm':decoded_displacement,
           'maximum_placed_float32_displacement_mm':placed_displacement,'displacement_limit_mm':max_displacement_mm,
           'source_centered_signed_volume_mm3':initial_volume,'encoded_centered_signed_volume_mm3':local_volume,
           'placed_centered_signed_volume_mm3':placed_volume,'placed_volume_change_mm3':placed_volume-initial_volume,
           'volume_change_envelope_mm3':envelope*2,'encoded_defects':local_defects,'placed_defects':placed_defects,
           'repair_needed':bool(repairs),'repairs':repairs}
    return result,audit
