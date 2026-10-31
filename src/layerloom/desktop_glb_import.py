"""Bounded, appearance-aware GLB import for the desktop region workflow.

This produces color regions for inspection, not closed material volumes. The
manifest explicitly records regions which cannot safely enter legacy weaving.
The older public glb-split command remains unchanged.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path

import numpy as np
import trimesh
from scipy.cluster.vq import kmeans2
from scipy.spatial import cKDTree

from .glb_import import _add_layerloom_metadata, linear_rgb_to_srgb, rgb01_to_hex


@dataclass(frozen=True)
class ImportSettings:
    colors: int = 64
    color_source: str = "auto"
    detail: str = "standard"
    units: str = "meters"
    up_axis: str = "y"

    def validate(self):
        if not 2 <= self.colors <= 256:
            raise ValueError("Choose between 2 and 256 source colors.")
        for value, allowed in [(self.color_source, {'auto', 'texture', 'vertex', 'material'}),
                               (self.detail, {'standard', 'fine', 'original'}),
                               (self.units, {'meters', 'millimeters', 'legacy'}),
                               (self.up_axis, {'y', 'z'})]:
            if value not in allowed:
                raise ValueError(f"Unsupported import option: {value}")


def _split(values):
    a, b, c = values[:, 0], values[:, 1], values[:, 2]
    ab, bc, ca = (a+b)/2, (b+c)/2, (c+a)/2
    return np.stack([np.stack([a, ab, ca], 1), np.stack([ab, b, bc], 1),
                     np.stack([ca, bc, c], 1), np.stack([ab, bc, ca], 1)], 1).reshape(-1, 3, values.shape[-1])


def _srgb_to_linear(values):
    return np.where(values <= .04045, values/12.92, ((values+.055)/1.055)**2.4)


def _unit_colors(values):
    values = np.asarray(values)
    return values.astype(float) / (np.iinfo(values.dtype).max if np.issubdtype(values.dtype, np.integer) else 1.)


def _check_supported(path):
    from pygltflib import GLTF2
    gltf = GLTF2().load(str(path))
    unsupported = set(gltf.extensionsRequired or [])
    # Fail clearly rather than silently approximating material appearance.
    unsupported |= set(gltf.extensionsUsed or []) & {
        'KHR_texture_transform', 'KHR_materials_pbrSpecularGlossiness',
        'KHR_materials_transmission', 'KHR_materials_volume', 'KHR_materials_iridescence',
        'KHR_materials_diffuse_transmission', 'KHR_texture_basisu', 'EXT_texture_webp',
    }
    if unsupported:
        raise ValueError("This import does not yet support: " + ', '.join(sorted(unsupported)) +
                         ". Export a basic base-color GLB first.")
    for material in gltf.materials or []:
        if material.alphaMode not in (None, 'OPAQUE'):
            raise ValueError("Transparent GLB materials need an opaque print model before import.")
        pbr = material.pbrMetallicRoughness
        info = pbr.baseColorTexture if pbr else None
        if info and info.texCoord not in (None, 0):
            raise ValueError("Only the first UV set is supported for base-color textures.")
    for sampler in gltf.samplers or []:
        if sampler.wrapS not in (None, 10497) or sampler.wrapT not in (None, 10497):
            raise ValueError("This import currently supports repeat-wrapped textures only.")


def _appearance(mesh, settings, budget):
    """Sample display RGB while retaining the original piecewise planar surface."""
    faces = mesh.faces
    tri = np.asarray(mesh.vertices)[faces]
    visual = mesh.visual
    material = getattr(visual, 'material', None)
    image = getattr(material, 'baseColorTexture', None)
    if image is None:
        image = getattr(material, 'image', None)
    factor = getattr(material, 'baseColorFactor', None)
    factor = np.ones(4) if factor is None else _unit_colors(factor)
    vertex = None
    if visual.kind == 'vertex':
        vertex = _unit_colors(visual.vertex_colors)
    else:
        raw = getattr(visual, 'vertex_attributes', {}).get('color')
        if raw is not None:
            vertex = _unit_colors(raw)
    choice = settings.color_source
    if choice == 'texture' and image is None:
        raise ValueError("Texture colors were selected, but a mesh has no base-color texture.")
    if choice == 'vertex' and vertex is None:
        raise ValueError("Vertex colors were selected, but a mesh has no vertex colors.")
    use_texture = image is not None and choice in ('auto', 'texture')
    use_vertex = vertex is not None and choice in ('auto', 'vertex')
    uv = None
    if use_texture:
        if visual.uv is None:
            raise ValueError("A textured mesh is missing UV coordinates.")
        uv = np.asarray(visual.uv)[faces]
    vertex_tri = vertex[faces] if use_vertex else None
    depth = 0
    # Relative detail scales with the model. Bound subdivision before allocation.
    target = np.ptp(mesh.vertices, axis=0).max() / (64 if settings.detail == 'fine' else 32)
    if use_texture and settings.detail != 'original':
        while depth < 6 and len(tri)*4 <= budget:
            areas = np.linalg.norm(np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]), axis=1)
            longest = np.linalg.norm(tri-np.roll(tri, 1, axis=1), axis=2).max(axis=1)
            if np.average(longest, weights=np.maximum(areas, 1e-30)) <= target:
                break
            tri, uv = _split(tri), _split(uv)
            if vertex_tri is not None:
                vertex_tri = _split(vertex_tri)
            depth += 1
    linear = np.tile(factor[:3] if choice in ('auto', 'material') else np.ones(3), (len(tri), 1))
    if vertex_tri is not None:
        linear *= vertex_tri[:, :, :3].mean(axis=1)
    if use_texture:
        rgba = trimesh.visual.uv_to_color(uv.mean(axis=1), image)
        linear *= _srgb_to_linear(rgba[:, :3]/255.)
    rgb = linear_rgb_to_srgb(linear)
    return tri, rgb, depth


def _palette(rgb, weights, count):
    unique, inv = np.unique(rgb, axis=0, return_inverse=True)
    if len(unique) <= count:
        return unique, inv
    rng = np.random.default_rng(7)
    ids = rng.choice(len(rgb), size=min(12000, len(rgb)), p=weights/weights.sum())
    sample = rgb[ids]
    # Avoid requesting more clusters than distinct sampled colors.
    count = min(count, len(np.unique(sample, axis=0)))
    centers, _ = kmeans2(sample, count, minit='++', seed=7, iter=25)
    _, groups = cKDTree(centers).query(rgb)
    return centers, groups


def import_model(source, output, manifest_path, settings=ImportSettings()):
    settings.validate()
    _check_supported(source)
    scene = trimesh.load(str(source), force='scene', process=False)
    scale = {'meters': 1000., 'millimeters': 1., 'legacy': .1}[settings.units]
    node_meshes = []
    for node in scene.graph.nodes_geometry:
        transform, key = scene.graph[node]
        original = scene.geometry[key]
        if not isinstance(original, trimesh.Trimesh):
            raise ValueError("Only triangle meshes can be imported as printable color regions.")
        mesh = original.copy()
        # TextureVisuals.copy() drops extra COLOR_0 attributes in trimesh 4.11.
        if original.visual.kind == 'texture':
            raw_color = original.visual.vertex_attributes.get('color')
            if raw_color is not None:
                mesh.visual.vertex_attributes['color'] = np.array(raw_color, copy=True)
        mesh.apply_transform(transform)
        mesh.vertices *= scale
        if settings.up_axis == 'y':
            mesh.vertices = mesh.vertices[:, [0, 2, 1]] * [1, -1, 1]
        node_meshes.append(mesh)
    total = sum(len(m.faces) for m in node_meshes)
    if not total:
        raise ValueError("No triangle geometry found in this model.")
    if total > 2_000_000:
        raise ValueError("This model exceeds the current 2 million triangle import limit.")
    budget = max(total, 200_000 if settings.detail != 'fine' else 800_000)
    triangles, colors, depths = [], [], []
    for mesh in node_meshes:
        tri, rgb, depth = _appearance(mesh, settings, max(len(mesh.faces), int(budget*len(mesh.faces)/total)))
        triangles.append(tri); colors.append(rgb); depths.append(depth)
    tri, rgb = np.concatenate(triangles), np.concatenate(colors)
    if not np.isfinite(tri).all() or not np.isfinite(rgb).all():
        raise ValueError("Model contains non-finite coordinates or colors.")
    area = np.linalg.norm(np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]), axis=1)/2
    if not area.sum() > 0:
        raise ValueError("Model has no nonzero-area triangles.")
    centers, groups = _palette(rgb, area, settings.colors)
    import lib3mf
    wrapper = lib3mf.get_wrapper(); model = wrapper.CreateModel()
    manifest = {'settings': asdict(settings), 'objects': [], 'input_triangles': total,
                'sampled_triangles': len(tri), 'refinement_depths': depths,
                'dimensions_mm': np.ptp(tri.reshape(-1, 3), axis=0).tolist(), 'geometry_issues': []}
    for group in np.unique(groups):
        t = tri[groups == group]
        vertices, inv = np.unique(t.reshape(-1, 3), axis=0, return_inverse=True)
        faces = inv.reshape(-1, 3)
        mesh = trimesh.Trimesh(vertices, faces, process=False)
        label = f'color_{int(group):03d}'
        if not mesh.is_volume:
            manifest['geometry_issues'].append(label)
        obj = model.AddMeshObject(); obj.SetName(label)
        _add_layerloom_metadata(obj, group_key=(int(group),), color_levels=settings.colors, avg_rgb=centers[group])
        for v in vertices:
            pos = lib3mf.Position()
            for axis in range(3): pos.Coordinates[axis] = float(v[axis])
            obj.AddVertex(pos)
        for f in faces:
            face = lib3mf.Triangle()
            for axis in range(3): face.Indices[axis] = int(f[axis])
            obj.AddTriangle(face)
        model.AddBuildItem(obj, wrapper.GetIdentityTransform())
        manifest['objects'].append({'name': label, 'source_hex': rgb01_to_hex(centers[group]),
                                    'triangle_count': len(faces), 'closed_volume': bool(mesh.is_volume)})
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    model.QueryWriter('3mf').WriteToFile(str(output))
    # Validate the stored coordinates too: lib3mf encodes positions as float32.
    from .paint_io import load_paint_document
    stored = load_paint_document(Path(output))
    for part in stored.meshes:
        v, inverse = np.unique(part.vertices, axis=0, return_inverse=True)
        f = inverse[part.faces]
        encoded = np.asarray(part.vertices, dtype=np.float32)[part.faces]
        collapsed = np.any(np.all(np.cross(encoded[:, 1]-encoded[:, 0], encoded[:, 2]-encoded[:, 0]) == 0, axis=1))
        duplicate = len(np.unique(np.sort(f, axis=1), axis=0)) != len(f)
        if collapsed or duplicate or not trimesh.Trimesh(v, f, process=False).is_volume:
            if part.name not in manifest['geometry_issues']:
                manifest['geometry_issues'].append(part.name)
    from .region_weave import source_status
    supported, reason = source_status(output) if manifest['geometry_issues'] else (False, None)
    manifest['surface_partition_supported'] = supported
    manifest['surface_partition_error'] = reason
    Path(manifest_path).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input'); parser.add_argument('--out', required=True)
    parser.add_argument('--manifest-out', required=True)
    parser.add_argument('--settings', default='{}')
    args = parser.parse_args(argv)
    import_model(args.input, args.out, args.manifest_out, ImportSettings(**json.loads(args.settings)))
    return 0
