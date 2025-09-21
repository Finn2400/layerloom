#!/usr/bin/env python3
import argparse, zipfile, sys
import xml.etree.ElementTree as ET

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS = {"m": CORE_NS}
M = lambda tag: f"{{{CORE_NS}}}{tag}"

IDENTITY_COLMAJ_12 = [1.0,0.0,0.0, 0.0,1.0,0.0, 0.0,0.0,1.0, 0.0,0.0,0.0]

def find_model_xml(zf: zipfile.ZipFile):
    for n in zf.namelist():
        if n.startswith("3D/") and n.lower().endswith(".model"):
            return n
    return None

def parse_transform_attr(s: str):
    if not s:
        return None
    parts = s.replace(",", " ").split()
    try:
        vals = [float(p) for p in parts]
    except Exception:
        return None
    return vals

def is_identity_transform(vals):
    """
    3MF uses 12-number column-major 3x4 (m11 m21 m31 m12 m22 m32 m13 m23 m33 m14 m24 m34).
    We consider identity if it matches [1,0,0, 0,1,0, 0,0,1, 0,0,0] within tiny epsilon.
    """
    if not vals or len(vals) != 12:
        return False
    eps = 1e-12
    for a, b in zip(vals, IDENTITY_COLMAJ_12):
        if abs(a - b) > eps:
            return False
    return True

def detect_needs_bake(root):
    build = root.find("m:build", NS)
    resources = root.find("m:resources", NS)
    if build is None or resources is None:
        raise RuntimeError("Invalid 3MF: missing <build> or <resources>")

    # Count build items and how many carry non-identity transforms
    items = build.findall("m:item", NS)
    with_tf = 0
    for it in items:
        tf_raw = it.get("transform")
        if tf_raw:
            vals = parse_transform_attr(tf_raw)
            if not is_identity_transform(vals):
                with_tf += 1

    # Scan resources for components objects
    objs = resources.findall("m:object", NS)
    with_components = 0
    for obj in objs:
        comps = obj.find("m:components", NS)
        if comps is not None and list(comps):
            with_components += 1

    needs = (with_tf > 0) or (with_components > 0)
    reasons = []
    if with_tf > 0: reasons.append("build_transform")
    if with_components > 0: reasons.append("components")
    if not reasons: reasons = ["none"]

    summary = {
        "needs_bake": needs,
        "reasons": reasons,
        "build_items_total": len(items),
        "build_items_with_transform": with_tf,
        "objects_total": len(objs),
        "objects_with_components": with_components,
    }
    return summary

def main():
    ap = argparse.ArgumentParser(description="Detect whether a 3MF requires baking (non-identity build transforms or components).")
    ap.add_argument("-i", "--input", required=True, help="Input .3mf file")
    args = ap.parse_args()

    try:
        with zipfile.ZipFile(args.input, "r") as zf:
            model_name = find_model_xml(zf)
            if not model_name:
                print("ERROR: 3D/*.model not found in archive.", file=sys.stderr)
                return 2
            xml_bytes = zf.read(model_name)
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        print(f"ERROR: failed to open/parse 3MF: {e}", file=sys.stderr)
        return 2

    info = detect_needs_bake(root)
    print(f"needs_bake: {'YES' if info['needs_bake'] else 'NO'}")
    print(f"reasons: {', '.join(info['reasons'])}")
    print(f"build_items: {info['build_items_total']} (with_transform: {info['build_items_with_transform']})")
    print(f"objects: {info['objects_total']} (with_components: {info['objects_with_components']})")
    return 0

if __name__ == "__main__":
    sys.exit(main())

