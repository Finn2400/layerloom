import zipfile

class CalibrationBuilder:
    def __init__(self, cube_size=40.0):
        self.cube_size = float(cube_size)
        self.objects = []
        self.obj_counter = 1

    def add_cube(self, x, y, z, name="Cube"):
        s = self.cube_size
        v = [
            (x, y, z), (x+s, y, z), (x+s, y+s, z), (x, y+s, z),
            (x, y, z+s), (x+s, y, z+s), (x+s, y+s, z+s), (x, y+s, z+s)
        ]
        self.objects.append({
            'id': self.obj_counter,
            'name': name.replace(" ", "_"), 
            'vertices': v
        })
        self.obj_counter += 1
        print(f"  -> Added {name} at ({x}, {y}, {z})")

    def add_stack(self, num_cubes, start_x=0, start_y=0, group_name="Stack"):
        for i in range(num_cubes):
            z = i * self.cube_size
            self.add_cube(start_x, start_y, z, f"{group_name}_Level_{i+1}")

    def add_grid(self, rows, cols, start_x=0, start_y=0, spacing=0, group_name="Grid"):
        for r in range(rows):
            for c in range(cols):
                x = start_x + r * (self.cube_size + spacing)
                y = start_y + c * (self.cube_size + spacing)
                self.add_cube(x, y, 0, f"{group_name}_Row{r+1}_Col{c+1}")

    def add_towers_grid(self, rows, cols, cubes_per_tower, spacing=10, group_name="Towers"):
        for r in range(rows):
            for c in range(cols):
                x = r * (self.cube_size + spacing)
                y = c * (self.cube_size + spacing)
                tower_name = f"{group_name}_R{r+1}_C{c+1}"
                self.add_stack(cubes_per_tower, start_x=x, start_y=y, group_name=tower_name)

    def add_flat_groups(self, group_rows, group_cols, cubes_per_r, cubes_per_c, group_spacing, cube_spacing=0, group_name="FlatGroup"):
        """Creates a grid of groups, where each group is a flat grid of cubes on the build plate."""
        # Calculate the physical footprint of a single group
        group_width = (cubes_per_r * self.cube_size) + ((cubes_per_r - 1) * cube_spacing)
        group_length = (cubes_per_c * self.cube_size) + ((cubes_per_c - 1) * cube_spacing)

        for gr in range(group_rows):
            for gc in range(group_cols):
                # Shift the starting coordinates by the entire footprint of the previous group + the desired gap
                start_x = gr * (group_width + group_spacing)
                start_y = gc * (group_length + group_spacing)
                
                sub_group_name = f"{group_name}_G{gr+1}-{gc+1}"
                # Delegate the actual cube placement to the standard grid function
                self.add_grid(cubes_per_r, cubes_per_c, start_x, start_y, cube_spacing, sub_group_name)

    def export(self, filename="calibration_print"):
        if not filename.endswith(".3mf"):
            filename += ".3mf"
            
        triangles = [
            (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
            (0, 1, 5), (0, 5, 4), (3, 6, 2), (3, 7, 6),
            (0, 7, 3), (0, 4, 7), (1, 2, 6), (1, 6, 5)
        ]

        objects_xml = ""
        build_xml = ""

        for obj in self.objects:
            v_xml = "".join([f'<vertex x="{v[0]}" y="{v[1]}" z="{v[2]}" />\n' for v in obj['vertices']])
            t_xml = "".join([f'<triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}" />\n' for t in triangles])
            
            objects_xml += f"""
            <object id="{obj['id']}" name="{obj['name']}" type="model">
              <mesh>
                <vertices>\n{v_xml}</vertices>
                <triangles>\n{t_xml}</triangles>
              </mesh>
            </object>"""
            
            build_xml += f'<item objectid="{obj["id"]}" />\n'

        model_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>{objects_xml}
  </resources>
  <build>
    {build_xml}
  </build>
</model>"""

        rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""

        content_types_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>"""

        print("\nSaving file...")
        with zipfile.ZipFile(filename, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('_rels/.rels', rels_xml.strip())
            zf.writestr('[Content_Types].xml', content_types_xml.strip())
            zf.writestr('3D/3dmodel.model', model_xml.strip())
            
        print(f"✅ Success! Generated perfectly watertight '{filename}' with {len(self.objects)} distinct objects.")


# ==========================================
# Interactive Menu Helpers
# ==========================================
def get_int(prompt, default):
    val = input(f"{prompt} [Default: {default}]: ").strip()
    return int(val) if val else default

def get_float(prompt, default):
    val = input(f"{prompt} [Default: {default}]: ").strip()
    return float(val) if val else default

def get_str(prompt, default):
    val = input(f"{prompt} [Default: '{default}']: ").strip()
    return val if val else default

def main():
    print("========================================")
    print(" Pure Python Calibration Cube Builder ")
    print("========================================")
    
    cube_size = get_float("Enter base cube size in mm", 40.0)
    app = CalibrationBuilder(cube_size=cube_size)
    
    while True:
        print("\n" + "="*40)
        print(f"Current Layout: {len(app.objects)} solid cubes loaded")
        print("1. Add a Single Cube")
        print("2. Add a Vertical Stack")
        print("3. Add a Flat Grid")
        print("4. Add a Grid of Towers")
        print("5. Add Flat Groups (Grid of Grids)")
        print("6. 💾 Export 3MF and Exit")
        print("7. ❌ Quit without saving")
        print("="*40)
        
        choice = input("Select an option (1-7): ").strip()
        
        if choice == '1':
            x = get_float("X Position", 0.0)
            y = get_float("Y Position", 0.0)
            z = get_float("Z Position", 0.0)
            name = get_str("Object Name", "Single_Cube")
            app.add_cube(x, y, z, name)
            
        elif choice == '2':
            num = get_int("How many cubes tall?", 4)
            x = get_float("X Position", 0.0)
            y = get_float("Y Position", 0.0)
            name = get_str("Group Name", "Stack")
            app.add_stack(num, x, y, name)
            
        elif choice == '3':
            r = get_int("Number of Rows (X-axis)", 2)
            c = get_int("Number of Columns (Y-axis)", 2)
            spacing = get_float("Spacing between cubes in mm", 0.0)
            name = get_str("Group Name", "Flat_Grid")
            app.add_grid(r, c, 0, 0, spacing, name)
            
        elif choice == '4':
            r = get_int("Number of Tower Rows", 2)
            c = get_int("Number of Tower Columns", 2)
            num = get_int("How many cubes tall per tower?", 4)
            spacing = get_float("Spacing between towers in mm", 10.0)
            name = get_str("Group Name", "Towers")
            app.add_towers_grid(r, c, num, spacing, name)
            
        elif choice == '5':
            gr = get_int("Number of Group Rows (X-axis)", 2)
            gc = get_int("Number of Group Columns (Y-axis)", 2)
            cr = get_int("Cubes per group row", 2)
            cc = get_int("Cubes per group column", 2)
            group_gap = get_float("Spacing BETWEEN groups in mm", 20.0)
            cube_gap = get_float("Spacing between cubes INSIDE group in mm", 0.0)
            name = get_str("Group Name", "Plate_Groups")
            app.add_flat_groups(gr, gc, cr, cc, group_gap, cube_gap, name)
            
        elif choice == '6':
            if len(app.objects) == 0:
                print("Warning: Your scene is empty! Exporting anyway...")
            filename = get_str("\nEnter output filename (no extension needed)", "calibration_test")
            app.export(filename)
            break
            
        elif choice == '7':
            print("Exiting without saving.")
            break
            
        else:
            print("Invalid choice. Please select 1-7.")

if __name__ == "__main__":
    main()
