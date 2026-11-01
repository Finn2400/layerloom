"""Construct printable layer volumes from color patches on an intact solid.

Region assignments remain part identities in Arrange. This adapter reconstructs
only the intact surface, preserving its per-face assignments, and shares the
validated layer partitioner with Paint without entering the Paint workspace.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile
import os

import numpy as np
import trimesh

from .paint_io import load_paint_document
from .paint_model import PaintDocument, PaintMesh


def prepare_document(path, *, require_assignments=True):
    """Reassemble exact shared vertices; retain separate solid shells and cavities.

    No hole filling, tolerance welding, remeshing, or normal repair is performed.
    Synthetic RGB IDs preserve independent region overrides even if two parts
    have identical displayed colors. They never become printable colors.
    """
    imported = load_paint_document(Path(path))
    chunks, rgba, assignments, rows = [], [], {}, []
    for index, part in enumerate(imported.meshes, 1):
        if index >= 2**24:
            raise ValueError('Too many color regions.')
        match = re.findall(r'__PAT_([a-z]+)__', part.name)
        if require_assignments and not match:
            raise ValueError(f'Assign a filament recipe to {part.name} before weaving.')
        rgb = [(index >> 16) & 255, (index >> 8) & 255, index & 255]
        key = '#{:02X}{:02X}{:02X}'.format(*rgb)
        if match:
            assignments[key] = match[-1]
        rows.append({'region': part.name, 'assignment_id': key,
                     'recipe': match[-1] if match else None, 'triangles': len(part.faces)})
        chunks.append(part.vertices[part.faces])
        rgba.append(np.tile([*rgb, 255], (len(part.faces), 1)))
    triangles = np.concatenate(chunks)
    colors = np.concatenate(rgba)
    vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    faces = inverse.reshape(-1, 3)
    if len(np.unique(np.sort(faces, axis=1), axis=0)) != len(faces):
        raise ValueError('The combined source contains duplicate faces or internal walls. '
                         'Use separate closed solids or correct the intact source first.')
    surface = trimesh.Trimesh(vertices, faces, process=False)
    if not surface.is_watertight or not surface.is_winding_consistent:
        raise ValueError('The intact source is open or inconsistently oriented. '
                         'Closing color regions cannot repair missing source surfaces.')
    groups = trimesh.graph.connected_components(surface.face_adjacency,
                 nodes=np.arange(len(faces)), min_len=1)
    shells = []
    for ids in groups:
        part = surface.submesh([ids], append=True, repair=False)
        volume = float(part.volume)
        if volume == 0:
            raise ValueError('The source contains a zero-volume shell.')
        shells.append((ids, part, volume))
    positive = [i for i, (_, _, volume) in enumerate(shells) if volume > 0]
    if not positive:
        raise ValueError('The source has no outward-oriented solid. Correct its normals before weaving.')
    members = {i: [shells[i][0]] for i in positive}
    for i, (ids, mesh, volume) in enumerate(shells):
        if volume > 0:
            continue
        # Cavity walls belong to the smallest enclosing positive shell.
        owners = [j for j in positive if shells[j][1].contains(mesh.vertices[:1])[0]]
        if not owners:
            raise ValueError('An inward-oriented shell has no enclosing solid.')
        owner = min(owners, key=lambda j: shells[j][2])
        members[owner].append(ids)
    meshes = []
    for i, ids in members.items():
        ids = np.concatenate(ids)
        used, remapped = np.unique(faces[ids], return_inverse=True)
        meshes.append(PaintMesh(f'solid-{i}', f'Imported solid {i+1}', vertices[used],
                                remapped.reshape(-1, 3), colors[ids]))
    document = PaintDocument(meshes, str(Path(path).resolve()), {'region_assignments': rows})
    document.validate()
    return document, assignments


def source_status(path):
    """Preflight uses the same solid and float32 checks as the exporter."""
    from .paint_export import _source_geometry
    try:
        document, _ = prepare_document(path, require_assignments=False)
        assignments = {'#{:02X}{:02X}{:02X}'.format(*rgb[:3]): {'token': 'c'}
                       for mesh in document.meshes for rgb in np.unique(mesh.face_rgba, axis=0)}
        _source_geometry(document, assignments)
        return True, None
    except ValueError as exc:
        return False, str(exc)


def validate_written_output(path):
    """Check actual serialized and placed mesh geometry, not just engine objects."""
    from .surface_field_weave import mesh_defects
    from .paint_export import _as_manifold
    import manifold3d as mf
    document = load_paint_document(Path(path))
    solids, rows = [], []
    for part in document.meshes:
        vertices, inverse = np.unique(part.vertices, axis=0, return_inverse=True)
        mesh = trimesh.Trimesh(vertices, inverse[part.faces], process=False)
        defects = mesh_defects(mesh)
        if any(defects.values()) or not mesh.is_volume:
            raise ValueError(f'Saved output {part.name} failed geometry validation: {defects}')
        solids.append(_as_manifold(mesh, part.name))
        rows.append({'part': part.name, 'triangles': len(mesh.faces), 'volume_mm3': float(mesh.volume),
                     'defects': defects})
    union = mf.Manifold.batch_boolean(solids, mf.OpType.Add)
    total = sum(row['volume_mm3'] for row in rows)
    overlap = total-float(union.volume())
    if union.status() != mf.Error.NoError or abs(overlap) > max(1e-5, total*1e-5):
        raise ValueError('Saved output contains overlapping or inconsistent material volumes.')
    return {'parts': rows, 'volume_mm3': total, 'overlap_mm3': max(0., overlap)}


def weave_regions(source, output, *, layer_height=.16, pitch=.5, palette_name='Normal',
                  first_layer_color='off', precision=.001, max_cells=2_000_000):
    from .paint_export import export_painted_3mf
    document, overrides = prepare_document(source)
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Export and validation both finish before replacing an existing user file.
    with tempfile.TemporaryDirectory(prefix='layerloom_regions_', dir=output.parent) as folder:
        staged = Path(folder)/output.name
        report = export_painted_3mf(document, staged, layer_height=layer_height, pitch=pitch,
                    palette_name=palette_name, recipe_overrides=overrides, precision=precision,
                    max_cells=max_cells, first_layer_color=first_layer_color)
        encoded = validate_written_output(staged)
        if abs(encoded['volume_mm3']-report['output_volume_mm3']) > max(1e-5, report['output_volume_mm3']*1e-5):
            raise ValueError('Saved coordinates changed material volume; export cancelled.')
        report['output_3mf'] = str(output)
        report['format'] = 'layerloom-region-weave-v1'
        report['region_assignments'] = document.metadata['region_assignments']
        report['saved_geometry'] = encoded
        destination_report = output.with_suffix('.region-report.json')
        report['report_path'] = str(destination_report)
        staged_report = Path(folder)/destination_report.name
        staged_report.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        os.replace(staged, output)
        try:
            os.replace(staged_report, destination_report)
        except OSError as exc:
            report['report_written'] = False
            report['report_warning'] = str(exc)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input'); parser.add_argument('--out', required=True)
    parser.add_argument('--layer-height', type=float, default=.16)
    parser.add_argument('--pitch', type=float, default=.5)
    parser.add_argument('--precision', type=float, default=.001)
    parser.add_argument('--palette', default='Normal')
    parser.add_argument('--first-layer-color', default='off')
    args = parser.parse_args(argv)
    report = weave_regions(args.input, args.out, layer_height=args.layer_height, pitch=args.pitch,
                          precision=args.precision, palette_name=args.palette, first_layer_color=args.first_layer_color)
    print(json.dumps({'output': report['output_3mf'], 'validation': report['validation']}))
    return 0
