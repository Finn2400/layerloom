"""Paint-aware input and lossless editable projects, separate from weaving.

The .llpaint format is a ZIP with a versioned JSON manifest and NumPy arrays;
loading never enables pickle.  3MF import reads color and vendor painting before
the legacy geometry normalizer can discard it. Slicer split annotations are
decoded as planar subdivisions, with adjacent faces refined to avoid T-junctions.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import posixpath
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET

import numpy as np

from .paint_model import PaintDocument, PaintMesh

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MAT = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
PROD = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DEFAULT_RGBA = np.array([180, 180, 180, 255], dtype=np.uint8)
NS = {"c": CORE}
_FORMAT = "layerloom-paint"
_VERSION = 1


def _tag(elem: ET.Element) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def _hex_rgba(value: str) -> np.ndarray:
    value = str(value).strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", value):
        raise ValueError(f"Invalid paint color: {value!r}")
    if len(value) == 6:
        value += "ff"
    return np.frombuffer(bytes.fromhex(value), dtype=np.uint8).copy()


def _rgba_hex(rgba: np.ndarray) -> str:
    return "#" + bytes(np.asarray(rgba, dtype=np.uint8)).hex().upper()


def _warn(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)


def _linear_to_srgb(rgb):
    rgb = np.clip(np.asarray(rgb, dtype=float), 0., 1.)
    return np.where(rgb <= .0031308, rgb * 12.92, 1.055 * rgb ** (1. / 2.4) - .055)


def _srgb_to_linear(rgb):
    rgb = np.clip(np.asarray(rgb, dtype=float), 0., 1.)
    return np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)


def _atomic_write(path: Path, writer) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        writer(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_paint_document(document: PaintDocument, path: str | os.PathLike) -> None:
    """Save .llpaint losslessly, or export current colors to standard .3mf.

    .llpaint retains original colors for erasing. A standard 3MF carries current
    source surface colors, not the printable filament volumes from paint_export.
    """
    document.validate()
    path = Path(path)
    if path.suffix.lower() == ".3mf":
        export_source_3mf(document, path)
        return
    if path.suffix.lower() != ".llpaint":
        raise ValueError("Editable paint projects use the .llpaint extension")
    manifest = {"format": _FORMAT, "version": _VERSION, "units": "millimeter",
                "source_path": document.source_path, "metadata": document.metadata,
                "meshes": [{"key": m.key, "name": m.name, "array_file": f"meshes/{i:06d}.npz"}
                           for i, m in enumerate(document.meshes)]}
    encoded = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode("utf-8")

    def write(tmp):
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", encoded)
            for info, mesh in zip(manifest["meshes"], document.meshes):
                buf = io.BytesIO()
                np.savez(buf, vertices=mesh.vertices, faces=mesh.faces,
                         face_rgba=mesh.face_rgba, source_rgba=mesh.source_rgba)
                archive.writestr(info["array_file"], buf.getvalue())
    _atomic_write(path, write)


def _load_project(path: Path) -> PaintDocument:
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != _FORMAT or manifest.get("version") != _VERSION:
            raise ValueError("Unsupported LayerLoom paint project format or version")
        if manifest.get("units") != "millimeter":
            raise ValueError("Paint project coordinates must be millimeters")
        meshes = []
        for i, info in enumerate(manifest["meshes"]):
            member = f"meshes/{i:06d}.npz"
            if info.get("array_file") != member:
                raise ValueError("Invalid paint project array member")
            with np.load(io.BytesIO(archive.read(member)), allow_pickle=False) as data:
                meshes.append(PaintMesh(info["key"], info["name"], data["vertices"],
                                        data["faces"], data["face_rgba"], data["source_rgba"]))
        doc = PaintDocument(meshes, manifest.get("source_path"), manifest.get("metadata", {}))
    doc.validate()
    return doc


def export_source_3mf(document: PaintDocument, path: str | os.PathLike) -> None:
    """Export standards-based flat per-face colors without altering geometry."""
    document.validate()
    ET.register_namespace("", CORE)
    ET.register_namespace("m", MAT)
    root = ET.Element(f"{{{CORE}}}model", {"unit": "millimeter", "requiredextensions": "m"})
    resources = ET.SubElement(root, f"{{{CORE}}}resources")
    build = ET.SubElement(root, f"{{{CORE}}}build")
    for i, mesh in enumerate(document.meshes):
        oid, pid = 2 * i + 1, 2 * i + 2
        colors, indices = np.unique(mesh.face_rgba, axis=0, return_inverse=True)
        group = ET.SubElement(resources, f"{{{MAT}}}colorgroup", {"id": str(pid)})
        for rgba in colors:
            ET.SubElement(group, f"{{{MAT}}}color", {"color": _rgba_hex(rgba)})
        obj = ET.SubElement(resources, f"{{{CORE}}}object", {"id": str(oid), "type": "model", "name": mesh.name})
        geo = ET.SubElement(obj, f"{{{CORE}}}mesh")
        verts = ET.SubElement(geo, f"{{{CORE}}}vertices")
        tris = ET.SubElement(geo, f"{{{CORE}}}triangles")
        for v in mesh.vertices:
            ET.SubElement(verts, f"{{{CORE}}}vertex", dict(zip(("x", "y", "z"), (format(x, ".17g") for x in v))))
        for face, ci in zip(mesh.faces, indices):
            ET.SubElement(tris, f"{{{CORE}}}triangle", {"v1": str(face[0]), "v2": str(face[1]), "v3": str(face[2]),
                          "pid": str(pid), "p1": str(ci), "p2": str(ci), "p3": str(ci)})
        ET.SubElement(build, f"{{{CORE}}}item", {"objectid": str(oid)})
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def write(tmp):
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
            z.writestr("_rels/.rels", f'<Relationships xmlns="{REL}"><Relationship Id="r1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/></Relationships>')
            z.writestr("3D/3dmodel.model", xml)
    _atomic_write(Path(path), write)


def _package_member(base: str, target: str) -> str:
    target = target.replace("\\", "/")
    if ":" in target:
        raise ValueError("External 3MF resource URLs are unsupported")
    name = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                             else posixpath.join(posixpath.dirname(base), target))
    if name == ".." or name.startswith("../"):
        raise ValueError("3MF resource escapes the package")
    return name


def _transform(value: str | None, unit: float) -> np.ndarray:
    if not value:
        return np.eye(4)
    vals = np.asarray([float(x) for x in value.split()], dtype=np.float64)
    if vals.shape != (12,) or not np.isfinite(vals).all():
        raise ValueError("Invalid 3MF transform")
    out = np.eye(4)
    out[:3] = vals.reshape(4, 3).T
    out[:3, 3] *= unit
    return out


def _units(value: str) -> float:
    factors = {"micron": .001, "millimeter": 1., "centimeter": 10., "inch": 25.4,
               "foot": 304.8, "meter": 1000.}
    if value not in factors:
        raise ValueError(f"Unsupported 3MF unit: {value}")
    return factors[value]


def _vendor_settings(z: zipfile.ZipFile) -> tuple[list[np.ndarray], dict]:
    palette = []
    if "Metadata/project_settings.config" in z.namelist():
        config = json.loads(z.read("Metadata/project_settings.config"))
        colors = config.get("filament_colour") or config.get("extruder_colour") or []
        if isinstance(colors, str):
            colors = colors.split(";")
        palette = [_hex_rgba(c) for c in colors]
    if not palette:
        for name in ("Metadata/Slic3r_PE.config", "Metadata/Slic3r.config"):
            if name in z.namelist():
                text = z.read(name).decode("utf-8", errors="replace")
                for key in ("filament_colour", "extruder_colour"):
                    match = re.search(rf"(?m)^\s*;?\s*{key}\s*=\s*(.+)$", text)
                    if match:
                        palette = [_hex_rgba(c.strip().strip('"')) for c in match.group(1).split(";") if c.strip().strip('"')]
                        if palette:
                            break
    settings = {}
    for name in ("Metadata/model_settings.config", "Metadata/Slic3r_PE_model.config"):
        if name not in z.namelist():
            continue
        root = ET.fromstring(z.read(name))
        for obj in root.findall("object"):
            info = {m.get("key"): m.get("value", "") for m in obj.findall("metadata") if m.get("key")}
            info["parts"] = {}
            info["ranges"] = []
            for part in obj.findall("part"):
                pinfo = {m.get("key"): m.get("value", "") for m in part.findall("metadata") if m.get("key")}
                pinfo["subtype"] = part.get("subtype", "normal_part")
                info["parts"][part.get("id")] = pinfo
            for volume in obj.findall("volume"):
                vinfo = {m.get("key"): m.get("value", "") for m in volume.findall("metadata") if m.get("key")}
                if "firstid" in volume.attrib and "lastid" in volume.attrib:
                    info["ranges"].append((int(volume.get("firstid")), int(volume.get("lastid")), vinfo))
            settings[obj.get("id")] = info
    return palette, settings


def _split_children(face: tuple[int, int, int], splits: int, special: int, midpoint) -> list[tuple[int, int, int]]:
    if special > 2 or (splits == 3 and special != 0):
        raise ValueError("Invalid slicer paint split side")
    a, b, c = face[special:] + face[:special]
    if splits == 1:
        m = midpoint(b, c)
        return [(a, b, m), (m, c, a)]
    if splits == 2:
        ab, ca = midpoint(a, b), midpoint(c, a)
        return [(a, ab, ca), (ab, b, ca), (b, c, ca)]
    ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
    return [(a, ab, ca), (ab, b, bc), (bc, c, ca), (ab, bc, ca)]


def decode_paint_annotation(code: str, face: tuple[int, int, int], midpoint) -> list[tuple[tuple[int, int, int], int]]:
    """Decode Prusa/Bambu/Orca reversed-nibble triangle paint trees.

    Encoding reference: TriangleSelector::serialize/deserialize/perform_split,
    FacetsAnnotation::set_triangle_from_string in those slicers. A leaf has
    low two bits zero; high bits store state, with base-15 extension chunks.
    Children are visited in reverse geometric child order. No slicer code is
    executed or imported here.
    """
    if not code:
        return [(face, 0)]
    if not re.fullmatch(r"[0-9a-fA-F]+", code):
        raise ValueError("Invalid hexadecimal slicer paint annotation")
    nibbles = [int(c, 16) for c in reversed(code)]
    cursor = 0
    pending = [(face, 0)]
    leaves = []
    while pending:
        tri, depth = pending.pop()
        if cursor >= len(nibbles):
            raise ValueError("Truncated slicer paint tree")
        value = nibbles[cursor]
        cursor += 1
        splits = value & 3
        if splits:
            if depth >= 48:
                raise ValueError("Slicer paint tree exceeds supported depth")
            children = _split_children(tri, splits, value >> 2, midpoint)
            pending.extend((child, depth + 1) for child in children)
        else:
            state = value >> 2
            if state == 3:
                while True:
                    if cursor >= len(nibbles):
                        raise ValueError("Truncated slicer paint state")
                    value = nibbles[cursor]
                    cursor += 1
                    state += value
                    if value != 15:
                        break
            leaves.append((tri, state))
    if cursor != len(nibbles):
        raise ValueError("Trailing data in slicer paint annotation")
    return leaves


def _conform_faces(faces, colors, midpoint_indices):
    """Refine neighboring faces using existing edge midpoints; preserve shape."""
    out_faces, out_colors = [], []
    pending = list(zip(faces, colors))
    while pending:
        face, color = pending.pop()
        face = tuple(face)
        edges = [(face[i], face[(i + 1) % 3]) for i in range(3)]
        known = [tuple(sorted(e)) in midpoint_indices for e in edges]
        count = sum(known)
        if not count:
            out_faces.append(face)
            out_colors.append(color)
            continue
        special = ((known.index(True) + 2) % 3 if count == 1 else
                   (known.index(False) + 2) % 3 if count == 2 else 0)
        children = _split_children(face, count, special, lambda a, b: midpoint_indices[tuple(sorted((a, b)))])
        pending.extend((child, color) for child in children)
    return out_faces, out_colors


def _load_3mf(path: Path) -> PaintDocument:
    warnings: list[str] = []
    meshes = []
    statistics = {"paint_annotations": 0, "paint_split_annotations": 0}
    with zipfile.ZipFile(path) as z:
        palette, settings = _vendor_settings(z)
        root_member = "3D/3dmodel.model"
        if "_rels/.rels" in z.namelist():
            for relation in ET.fromstring(z.read("_rels/.rels")):
                if relation.get("Type", "").endswith("/3dmodel"):
                    if relation.get("TargetMode") == "External":
                        raise ValueError("External 3MF root models are unsupported")
                    root_member = _package_member("", relation.get("Target", ""))
                    break
        models = {}
        image_cache = {}

        def model(member):
            if member not in models:
                root = ET.fromstring(z.read(member))
                resources = root.find("c:resources", NS)
                if resources is None:
                    raise ValueError(f"No 3MF resources in {member}")
                models[member] = (root, {r.get("id"): r for r in resources}, _units(root.get("unit", "millimeter")))
            return models[member]

        def slot_color(slot, fallback):
            slot = int(slot or 0)
            if slot <= 0:
                return fallback
            if slot > len(palette):
                raise ValueError(f"Paint references filament slot {slot}, but the 3MF contains only {len(palette)} filament colors. Save a full slicer project including its settings.")
            return palette[slot - 1]

        def property_color(member, resources, pid, indices, fallback):
            if pid is None:
                return fallback
            resource = resources.get(str(pid))
            if resource is None:
                raise ValueError(f"Missing 3MF color property resource {pid}")
            kind = _tag(resource)
            entries = list(resource)
            if kind in ("colorgroup", "basematerials"):
                try:
                    colors = [_hex_rgba(entries[i].get("color" if kind == "colorgroup" else "displaycolor")) for i in indices]
                except (IndexError, TypeError) as exc:
                    raise ValueError("3MF color property index is invalid") from exc
                if not all(np.array_equal(colors[0], c) for c in colors[1:]):
                    _warn(warnings, "Per-corner 3MF gradients were sampled to one average color per triangle.")
                return np.rint(np.mean(colors, axis=0)).astype(np.uint8)
            if kind == "texture2dgroup":
                from PIL import Image
                texture = resources.get(resource.get("texid"))
                if texture is None:
                    raise ValueError("Missing 3MF texture resource")
                tex_member = _package_member(member, texture.get("path", ""))
                if tex_member not in image_cache:
                    image_cache[tex_member] = np.asarray(Image.open(io.BytesIO(z.read(tex_member))).convert("RGBA"))
                image = image_cache[tex_member]
                uv = np.mean([[float(entries[i].get("u", "0")), float(entries[i].get("v", "0"))] for i in indices], axis=0)
                for axis, name in enumerate(("tilestyleu", "tilestylev")):
                    style = texture.get(name, "wrap")
                    if style == "wrap":
                        uv[axis] %= 1.
                    elif style == "mirror":
                        uv[axis] = 1. - abs((uv[axis] % 2.) - 1.)
                    else:
                        uv[axis] = np.clip(uv[axis], 0., 1.)
                y = int(round((1 - uv[1]) * (image.shape[0] - 1)))
                x = int(round(uv[0] * (image.shape[1] - 1)))
                _warn(warnings, "3MF textures were sampled at triangle centers; texture detail smaller than a triangle is not retained.")
                return image[y, x].copy()
            raise ValueError(f"Unsupported 3MF surface property {kind!r}; export flat colors or a texture first")

        def visit(member, oid, world, inherited, chain):
            identity = (member, oid)
            if identity in chain:
                raise ValueError("Cyclic 3MF component reference")
            root, resources, unit = model(member)
            obj = resources.get(oid)
            if obj is None or _tag(obj) != "object":
                raise ValueError(f"Missing 3MF object {oid} in {member}")
            if obj.get("type", "model") not in ("model", ""):
                _warn(warnings, "Non-model 3MF resources were excluded from the paint document.")
                return
            own = dict(settings.get(oid, {}))
            config = dict(inherited)
            config.update({k: v for k, v in own.items() if k not in ("parts", "ranges")})
            if config.get("subtype", "normal_part") not in ("normal_part", "model_part", ""):
                _warn(warnings, "Slicer modifier/support volumes were excluded from paintable model geometry.")
                return
            parts = own.get("parts", {})
            components = obj.find("c:components", NS)
            if components is not None:
                for component in components:
                    target = component.get(f"{{{PROD}}}path") or component.get("path")
                    child_member = _package_member(member, target) if target else member
                    child_id = component.get("objectid")
                    child_info = dict(config)
                    child_info.update(parts.get(child_id, {}))
                    visit(child_member, child_id, world @ _transform(component.get("transform"), unit), child_info, chain + (identity,))
            geo = obj.find("c:mesh", NS)
            if geo is None:
                return
            vs, ts = geo.find("c:vertices", NS), geo.find("c:triangles", NS)
            if vs is None or ts is None or not len(vs) or not len(ts):
                return
            vertices = [np.array([float(v.get(a)) * unit for a in ("x", "y", "z")]) for v in vs]
            midpoints = {}

            def midpoint(a, b):
                key = tuple(sorted((a, b)))
                if key not in midpoints:
                    midpoints[key] = len(vertices)
                    vertices.append((vertices[a] + vertices[b]) * .5)
                return midpoints[key]

            base = DEFAULT_RGBA
            for md in obj.iter():
                if _tag(md) == "metadata" and (md.get("name") or "").split(":")[-1] == "source_hex" and md.text:
                    base = _hex_rgba(md.text)
            base = slot_color(config.get("extruder"), base)
            faces, colors = [], []
            ranges = own.get("ranges", [])
            object_pid, object_pi = obj.get("pid"), int(obj.get("pindex", "0"))
            for ti, tri in enumerate(ts):
                face = tuple(int(tri.get(k)) for k in ("v1", "v2", "v3"))
                if min(face) < 0 or max(face) >= len(vs):
                    raise ValueError("3MF triangle references an invalid vertex")
                default = base
                for first, last, info in ranges:
                    if first <= ti <= last:
                        default = slot_color(info.get("extruder"), default)
                        break
                p1 = int(tri.get("p1", str(object_pi)))
                indices = [p1, int(tri.get("p2", str(p1))), int(tri.get("p3", str(p1)))]
                default = property_color(member, resources, tri.get("pid", object_pid), indices, default)
                annotation = next((value for key, value in tri.attrib.items() if key.rsplit("}", 1)[-1] in ("paint_color", "mmu_segmentation") and value), "")
                if annotation:
                    statistics["paint_annotations"] += 1
                    leaves = decode_paint_annotation(annotation, face, midpoint)
                    statistics["paint_split_annotations"] += int(len(leaves) > 1)
                else:
                    leaves = [(face, 0)]
                for leaf, state in leaves:
                    faces.append(leaf)
                    colors.append(slot_color(state, default))
            if midpoints:
                faces, colors = _conform_faces(faces, colors, midpoints)
            v = np.asarray(vertices)
            v = v @ world[:3, :3].T + world[:3, 3]
            f = np.asarray(faces, dtype=np.int64)
            if np.linalg.det(world[:3, :3]) < 0:
                f = f[:, ::-1]
            name = config.get("name") or obj.get("name") or f"Object {oid}"
            meshes.append(PaintMesh(f"mesh-{len(meshes):06d}", name, v, f, np.asarray(colors, dtype=np.uint8)))

        root, resources, unit = model(root_member)
        build = root.find("c:build", NS)
        if build is None:
            raise ValueError("3MF has no build section")
        for item in build:
            if item.get("printable", "1").lower() in ("0", "false", "no"):
                continue
            target = item.get(f"{{{PROD}}}path") or item.get("path")
            member = _package_member(root_member, target) if target else root_member
            visit(member, item.get("objectid"), _transform(item.get("transform"), unit), {}, ())
    doc = PaintDocument(meshes, str(path.resolve()), {"format": "3mf", "units": "millimeter", "warnings": warnings, **statistics})
    doc.validate()
    return doc


def _load_mesh(path: Path) -> PaintDocument:
    """Read generic meshes without remeshing; sample appearance per triangle."""
    import trimesh
    warnings = []
    scene = trimesh.load(str(path), force="scene", process=False)
    if not isinstance(scene, trimesh.Scene):
        scene = trimesh.Scene(scene)
    meshes = []
    is_gltf = path.suffix.lower() in (".glb", ".gltf")
    for node in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node]
        mesh = scene.geometry[geometry_name]
        if not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces):
            continue
        v = np.asarray(mesh.vertices, dtype=np.float64).copy()
        v = v @ transform[:3, :3].T + transform[:3, 3]
        if is_gltf:
            v *= 1000.  # glTF normative length unit is meter.
        faces = np.asarray(mesh.faces, dtype=np.int64).copy()
        if np.linalg.det(transform[:3, :3]) < 0:
            faces = faces[:, ::-1]
        visual = mesh.visual
        kind = visual.kind
        if kind == "face":
            rgba = np.asarray(visual.face_colors, dtype=np.uint8).copy()
        elif kind == "vertex":
            values = np.asarray(visual.vertex_colors)[faces].mean(axis=1) / 255.
            if is_gltf:
                values[:, :3] = _linear_to_srgb(values[:, :3])
            rgba = np.rint(values * 255.).astype(np.uint8)
            _warn(warnings, "Vertex colors were sampled to one average color per triangle.")
        elif kind == "texture":
            material = visual.material
            if getattr(material, "materials", None):
                raise ValueError("MultiMaterial meshes must be split by material before paint import")
            image = getattr(material, "baseColorTexture", None)
            if image is None:
                image = getattr(material, "image", None)
            factor = getattr(material, "baseColorFactor", None)
            if factor is None:
                factor = getattr(material, "diffuse", None)
            factor = np.array([255, 255, 255, 255] if factor is None else factor, dtype=float)
            if factor.max(initial=0) <= 1:
                factor *= 255.
            if len(factor) == 3:
                factor = np.r_[factor, 255.]
            multiplier = np.tile(factor / 255., (len(faces), 1))
            if is_gltf:
                vertex_color = getattr(visual, "vertex_attributes", {}).get("color")
                if vertex_color is not None:
                    raw = np.asarray(vertex_color)
                    values = raw.astype(float)
                    if np.issubdtype(raw.dtype, np.integer):
                        values /= np.iinfo(raw.dtype).max
                    if values.shape[1] == 3:
                        values = np.column_stack((values, np.ones(len(values))))
                    multiplier *= values[faces].mean(axis=1)
            if image is not None:
                if visual.uv is None:
                    raise ValueError("Textured mesh has no UV coordinates")
                uv = np.asarray(visual.uv)[faces].mean(axis=1)
                sampled = trimesh.visual.uv_to_color(uv, image)
                values = sampled.astype(float) / 255.
                if is_gltf:
                    values[:, :3] = _srgb_to_linear(values[:, :3])
                values *= multiplier
                if is_gltf:
                    values[:, :3] = _linear_to_srgb(values[:, :3])
                rgba = np.rint(values * 255.).clip(0, 255).astype(np.uint8)
                _warn(warnings, "Textures were sampled at triangle centers; texture detail smaller than a triangle is not retained.")
            else:
                if is_gltf:
                    multiplier[:, :3] = _linear_to_srgb(multiplier[:, :3])
                rgba = np.rint(multiplier * 255.).clip(0, 255).astype(np.uint8)
        else:
            rgba = np.tile(DEFAULT_RGBA, (len(faces), 1))
        meshes.append(PaintMesh(f"mesh-{len(meshes):06d}", str(node), v, faces, rgba))
    if is_gltf:
        _warn(warnings, "GLB/GLTF coordinates were converted from meters to millimeters. Check physical size before export.")
        _warn(warnings, "glTF appearance is sampled through trimesh; advanced material extensions and texture transforms are not supported.")
    doc = PaintDocument(meshes, str(path.resolve()), {"format": path.suffix.lower().lstrip("."), "units": "millimeter", "warnings": warnings})
    doc.validate()
    return doc


def load_paint_document(path: str | os.PathLike) -> PaintDocument:
    """Load .llpaint, painted .3mf, or a GLB/GLTF/STL/OBJ/PLY mesh."""
    path = Path(path).expanduser()
    if path.suffix.lower() == ".llpaint":
        return _load_project(path)
    if path.suffix.lower() == ".3mf":
        return _load_3mf(path)
    if path.suffix.lower() in (".glb", ".gltf", ".stl", ".obj", ".ply"):
        return _load_mesh(path)
    raise ValueError(f"Unsupported paint input format: {path.suffix}")
