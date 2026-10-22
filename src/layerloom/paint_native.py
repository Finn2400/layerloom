"""Exact source-colored surface export for slicers with native facet painting."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from xml.sax.saxutils import quoteattr
import zipfile

import numpy as np
import trimesh

from .surface_field_weave import mesh_defects


def encode_paint_slot(slot: int) -> str:
    """Encode one unsplit leaf in the native reversed hexadecimal paint tree."""
    if not isinstance(slot, int) or slot < 0:
        raise ValueError("Paint slot must be a nonnegative integer")
    if slot < 3:
        return format(slot << 2, "X")
    chunks = [12]
    remainder = slot - 3
    while remainder >= 15:
        chunks.append(15)
        remainder -= 15
    chunks.append(remainder)
    return "".join(format(chunk, "X") for chunk in reversed(chunks))


def export_native_painted_3mf(document, path, *, center_on_plate=True, progress=None):
    """Write intact, validated painted facets; never repair or sample the input.

    Colors become physical filament slots, not LayerLoom woven recipes. A single
    shared translation optionally centers XY at (128,128) and grounds the model.
    This is an editable painted 3MF requiring slicing, not machine G-code.
    """
    document.validate()
    path = Path(path)
    if path.suffix.lower() != '.3mf':
        raise ValueError("Native paint export requires a .3mf filename")
    colors = np.unique(np.concatenate([m.face_rgba for m in document.meshes]), axis=0)
    if np.any(colors[:,3] != 255):
        raise ValueError("Native filament paint requires opaque colors")
    if len(colors) > 16:
        raise ValueError("Native paint export supports at most 16 physical filament colors")
    palette = ['#'+bytes(c[:3]).hex().upper() for c in colors]
    slots = {bytes(c):i+1 for i,c in enumerate(colors)}
    bounds = np.array([np.min([m.vertices.min(axis=0) for m in document.meshes],axis=0),
                       np.max([m.vertices.max(axis=0) for m in document.meshes],axis=0)])
    translation = np.array([128,128,0.])-np.r_[bounds.mean(axis=0)[:2],bounds[0,2]] if center_on_plate else np.zeros(3)
    rows = []
    for item in document.meshes:
        unique, inverse = np.unique(item.vertices, axis=0, return_inverse=True)
        mesh = trimesh.Trimesh(unique, inverse[item.faces], process=False)
        defects = mesh_defects(mesh,translation)
        if any(defects.values()):
            raise ValueError(f"{item.name}: native paint export needs repaired closed float32-safe geometry: {defects}")
        rows.append({'key':item.key,'name':item.name,'vertices':len(item.vertices),
                     'triangles':len(item.faces),'float32_defects':defects})
    if progress:
        progress(0, len(document.meshes))
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.stem+'.',suffix='.tmp',dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            archive.writestr('[Content_Types].xml','<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/><Default Extension="config" ContentType="application/octet-stream"/></Types>')
            archive.writestr('_rels/.rels','<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
            with archive.open('3D/3dmodel.model','w',force_zip64=True) as model:
                def write(text):
                    model.write(text.encode('utf-8'))
                write('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><metadata name="Application">LayerLoom Native Paint</metadata><resources>')
                for index,item in enumerate(document.meshes,1):
                    write(f'<object id="{index}" type="model" name={quoteattr(item.name)}><mesh><vertices>')
                    for start in range(0,len(item.vertices),8192):
                        write(''.join(f'<vertex x="{v[0]:.17g}" y="{v[1]:.17g}" z="{v[2]:.17g}"/>' for v in item.vertices[start:start+8192]))
                    write('</vertices><triangles>')
                    for start in range(0,len(item.faces),8192):
                        write(''.join(f'<triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}" paint_color="{encode_paint_slot(slots[bytes(c)])}"/>' for f,c in zip(item.faces[start:start+8192],item.face_rgba[start:start+8192])))
                    write('</triangles></mesh></object>')
                    if progress:
                        progress(index, len(document.meshes))
                write('</resources><build>')
                transform = '1 0 0 0 1 0 0 0 1 '+' '.join(f'{v:.17g}' for v in translation)
                for index in range(1,len(document.meshes)+1):
                    write(f'<item objectid="{index}" transform="{transform}" printable="1"/>')
                write('</build></model>')
            archive.writestr('Metadata/project_settings.config',json.dumps({'filament_colour':palette,'extruder_colour':palette},indent=2))
            config = '<config>'+''.join(f'<object id="{i}"><metadata key="name" value={quoteattr(item.name)}/><metadata key="extruder" value="1"/></object>' for i,item in enumerate(document.meshes,1))+'</config>'
            archive.writestr('Metadata/model_settings.config',config)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    report = {'format':'layerloom-native-paint-v1','output_3mf':str(path.resolve()),
              'mode':'native_source_paint','palette':palette,'physical_filament_slots':len(palette),
              'source_bounds_mm':bounds.tolist(),'shared_translation_mm':translation.tolist(),
              'meshes':rows,'geometry_policy':'Original triangle geometry; only shared build translation',
              'paint_policy':'Exact one native paint leaf per source triangle',
              'limitations':['Requires a slicer supporting Bambu/Orca/Prusa facet paint annotations.',
                             'Filament RGB is a label, not a measured printed-color guarantee.',
                             'Slicer determines painted-region depth and purge behavior.']}
    report_path=path.with_suffix('.native-paint-report.json')
    report['report_path']=str(report_path.resolve())
    report_path.write_text(json.dumps(report,indent=2)+'\n')
    return report
