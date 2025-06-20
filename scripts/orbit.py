# SCRIPT_SETTINGS: {"num_orbits": {"type": "spinbox", "range": [1, 50], "step": 1, "label": "Total 360-degree Orbits per Print", "tooltip": "Total number of 360-degree rotations the camera will make over the entire print."}, "snapshots_per_loop": {"type": "spinbox", "range": [5, 60], "step": 1, "label": "Snapshots per 360-degree Loop", "tooltip": "Number of snapshots to take within each 360-degree rotation of the camera.'"}, "z_offset_for_snapshots": {"type": "doublespinbox", "label": "Snapshot Z Offset (mm)", "range": [-10.0, 10.0], "default": 0.0, "step": 0.1, "decimals": 1, "tooltip": "Additional Z offset applied to the snapshot height (can be negative or positive)."}, "first_snapshot_layer": {"type": "spinbox", "label": "First Snapshot Layer", "range": [0, 9999], "default": 1, "tooltip": "The first layer number (0-indexed) to begin taking snapshots. (Default: 1 for first print layer)"}, "orbit_radius_xy": {"type": "doublespinbox", "range": [10.0, 100.0], "step": 1.0, "decimals": 1, "label": "Orbit Radius (mm)", "tooltip": "The radius of the circular path around the object for snapshots."}, "start_angle": {"type": "spinbox", "label": "Start Angle (degrees)", "range": [0, 359], "default": 0, "step": 1, "tooltip": "The starting angle for the first snapshot of the print. 0 degrees is positive X axis."}}

import math
import re
import sys # Import sys for printing to stdout/stderr

def parse_gcode_info(lines):
    """
    Parses G-code lines to extract total layers and model bounding box information.
    Prioritizes EXCLUDE_OBJECT_DEFINE for bounding box.
    Returns a dictionary with 'total_layers', 'min_x', 'max_x', 'min_y', 'max_y', 'max_z'.
    Returns None for any missing info.
    """
    info = {
        "total_layers": None,
        "min_x": None, "max_x": None,
        "min_y": None, "max_y": None,
        "max_z": None
    }

    total_layers_found = False
    bbox_found = False

    for line in lines:
        line_upper = line.strip().upper()

        # Try to parse total layers from various common comments
        if not total_layers_found:
            # Priority 1: Exact specified format "; total layer number: X"
            match = re.search(r";\s*total layer number:\s*(\d+)", line, re.IGNORECASE)
            if match:
                info["total_layers"] = int(match.group(1))
                total_layers_found = True
            
            # Fallback 1: PrusaSlicer/SuperSlicer ";LAYERS:X"
            if not total_layers_found:
                match = re.search(r"LAYERS:\s*(\d+)", line_upper)
                if match:
                    info["total_layers"] = int(match.group(1))
                    total_layers_found = True
            
            # Fallback 2: Cura-style ";TOTAL_LAYERS:X"
            if not total_layers_found and line_upper.startswith(";TOTAL_LAYERS:"):
                try:
                    info["total_layers"] = int(line_upper.split(":")[1].strip())
                    total_layers_found = True
                except ValueError:
                    pass
            
            # Fallback 3: Slic3r/PrusaSlicer-style ";MAX_LAYER:X" (needs +1 for total)
            if not total_layers_found and line_upper.startswith(";MAX_LAYER:"):
                try:
                    info["total_layers"] = int(line_upper.split(":")[1].strip()) + 1
                    total_layers_found = True
                except ValueError:
                    pass

        # Try to parse bounding box from comments
        # Prioritize EXCLUDE_OBJECT_DEFINE with POLYGON, as this is explicit model info
        if not bbox_found:
            # Example: EXCLUDE_OBJECT_DEFINE NAME=purge_line POLYGON=[[78.9623,79.7196],[78.9814,79.7196],[78.9814,79.7484],[78.9623,79.7484]]
            exclude_obj_match = re.search(r"POLYGON=\[\[([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)\],\[([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)\],\[([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)\],\[([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)\]", line, re.IGNORECASE)
            if exclude_obj_match:
                try:
                    coords = [float(exclude_obj_match.group(i)) for i in range(1, 9)]
                    xs = [coords[j] for j in [0, 2, 4, 6]]
                    ys = [coords[j] for j in [1, 3, 5, 7]]
                    info["min_x"] = min(xs)
                    info["max_x"] = max(xs)
                    info["min_y"] = min(ys)
                    info["max_y"] = max(ys)
                    # if settings.get("debug_mode", False): # Removed verbose debug
                        # print(f"DEBUG: Orbit Script: Detected object bbox via POLYGON: X[{info['min_x']:.4f}:{info['max_x']:.4f}] Y[{info['min_y']:.4f}:{info['max_y']:.4f}].", file=sys.stdout) # Removed verbose debug
                except ValueError:
                    if settings.get("debug_mode", False):
                        print(f"DEBUG: Orbit Script: Error parsing POLYGON coordinates in line: {line.strip()}", file=sys.stdout)
                    pass

        # Generic bounding box comments (e.g., from Cura, sometimes PrusaSlicer)
        if not bbox_found:
            bbox_match = re.search(r"X:?([-\d.]+)\s*Y:?([-\d.]+)\s*Z:?([-\d.]+)\s*E:?([-\d.]+)\s*([-\d.]+)", line_upper)
            if bbox_match:
                # This regex is less reliable for general bounding box and more for start/end G-code points.
                # Prioritize explicit bbox comments if available from slicers.
                pass # Skipping this as it's often not for model bbox

            # More robust bbox comment parsing
            # Example: ";BOX:MINX:50.0 MINY:50.0 MAXX:150.0 MAXY:150.0 MAXZ:100.0"
            # Example: "; bounding_box: min_x=10.0 max_x=100.0 min_y=10.0 max_y=100.0 max_z=50.0"
            bbox_coords_match = re.search(r"(?:MINX|min_x)[=:]\s*([-\d.]+)\s*(?:MINY|min_y)[=:]\s*([-\d.]+)\s*(?:MAXX|max_x)[=:]\s*([-\d.]+)\s*(?:MAXY|max_y)[=:]\s*([-\d.]+)\s*(?:MAXZ|max_z)[=:]\s*([-\d.]+)", line, re.IGNORECASE)
            if bbox_coords_match:
                try:
                    info["min_x"] = float(bbox_coords_match.group(1))
                    info["min_y"] = float(bbox_coords_match.group(2))
                    info["max_x"] = float(bbox_coords_match.group(3))
                    info["max_y"] = float(bbox_coords_match.group(4))
                    info["max_z"] = float(bbox_coords_match.group(5))
                    bbox_found = True
                    # if settings.get("debug_mode", False): # Removed verbose debug
                        # print(f"DEBUG: Orbit Script: Detected object bbox: X[{info['min_x']:.4f}:{info['max_x']:.4f}] Y[{info['min_y']:.4f}:{info['max_y']:.4f}] Z_max:{info['max_z']:.4f}.", file=sys.stdout) # Removed verbose debug
                except ValueError:
                    if settings.get("debug_mode", False):
                        print(f"DEBUG: Orbit Script: Error parsing bbox coordinates in line: {line.strip()}", file=sys.stdout)
                    pass

        # Try to parse Max Z height from comments if not found via bbox
        if info["max_z"] is None:
            max_z_match = re.search(r"(?:max_z_height|max_z)\s*[=:]\s*([-\d.]+)", line, re.IGNORECASE)
            if max_z_match:
                try:
                    info["max_z"] = float(max_z_match.group(1))
                except ValueError:
                    pass

        # Optimization: If all critical info is found, no need to parse further
        if all(v is not None for v in [info["total_layers"], info["min_x"], info["max_x"], info["min_y"], info["max_y"], info["max_z"]]):
            # if settings.get("debug_mode", False): # Removed verbose debug
                # print("DEBUG: Orbit Script: All critical info found in parse_gcode_info. Early exit.", file=sys.stdout) # Removed verbose debug
            break
            
    # Fallback/sanity checks for missing info
    if info["total_layers"] is None:
        if settings.get("debug_mode", False):
            print("WARNING: Orbit Script: Total layers not detected. Defaulting to 1.", file=sys.stdout)
        info["total_layers"] = 1 # Prevent division by zero later
    
    # If bounding box info is still missing, provide sensible defaults (e.g., center of a 220x220 bed)
    # These will be explicitly overridden by toolpath_bounds from main.py if available and valid.
    if info["min_x"] is None: info["min_x"] = 0.0
    if info["max_x"] is None: info["max_x"] = 220.0
    if info["min_y"] is None: info["min_y"] = 0.0
    if info["max_y"] is None: info["max_y"] = 220.0
    if info["max_z"] is None: info["max_z"] = 250.0 # Default max Z for visualization

    if settings.get("debug_mode", False):
        print(f"DEBUG: Orbit Script: Final info from parse_gcode_info: {info}", file=sys.stdout)

    return info


def run(settings, gcode_lines):
    """
    Applies a corkscrew camera movement to a G-code file for timelapse.
    The printer moves to a specified point on an XY circle at a specific Z height,
    takes a snapshot, and returns to its original position.

    Args:
        settings (dict): A dictionary containing various settings:
            - "firmware" (str): "klipper" or "marlin".
            - "travel_speed" (int): Speed for non-printing moves (mm/min).
            - "dwell_time" (int): Time to dwell at snapshot position (ms).
            - "retract_length" (float): Filament retract length (mm).
            - "retract_speed" (int): Filament retract speed (mm/s).
            - "z_hop_height" (float): Z-hop height for snapshot moves (mm).
            - "num_orbits" (int): Total 360-degree Orbits per Print.
            - "snapshots_per_loop" (int): Snapshots to take per 360-degree loop.
            - "z_offset_for_snapshots" (float): Additional Z offset for snapshots (can be negative).
            - "first_snapshot_layer" (int): The first layer (0-indexed) to begin taking snapshots.
            - "orbit_radius_xy" (float): The radius of the circular path around the object.
            - "start_angle" (int): The starting angle in degrees for the first snapshot.
            - "min_x", "max_x", "min_y", "max_y", "max_z": Object bounding box and max Z from settings.
            - "total_layers": Total layers from G-code info.
            - "bed_dimensions": {"x": float, "y": float} bed dimensions.
            - "min_z_print": The actual minimum Z coordinate of the toolpath.
        gcode_lines (list): List of G-code lines.

    Returns:
        tuple: (modified_gcode_lines (list), snapshot_points (list))
            modified_gcode_lines: List of G-code lines with added commands.
            snapshot_points: List of (x, y, z) tuples for visualization in the viewer.
    """
    final_gcode = []
    snapshot_points_list = [] # List to store (x, y, z) tuples for visualization

    current_x, current_y, current_z = 0.0, 0.0, 0.0  # Initialize current position
    is_relative = False  # Track G90 (absolute) and G91 (relative)

    # Global position tracking for the original position to return to
    original_pos_x, original_pos_y, original_pos_z = 0.0, 0.0, 0.0
    last_extruded_e = 0.0 # Initialize last_extruded_e here to prevent NameError

    # New tracking variables for layers and snapshots
    # This will be the sequential layer number based on ';LAYER:X' comments
    current_logical_layer = 0
    # Store the actual layer numbers where snapshots have been inserted
    layers_with_inserted_snapshots = set() 
    
    snapshots_taken_count = 0 # This will now be used directly for progress calculation
    
    # Extract settings with defaults
    debug_mode = settings.get("debug_mode", False)
    firmware = settings.get("firmware", "klipper").lower()
    travel_speed = settings.get("travel_speed", 9000)
    dwell_time = settings.get("dwell_time", 500)
    retract_length = settings.get("retract_length", 0.5)
    retract_speed = settings.get("retract_speed", 40)
    z_hop_height = settings.get("z_hop_height", 0.2)
    num_orbits = settings.get("num_orbits", 1)
    snapshots_per_loop = settings.get("snapshots_per_loop", 5)
    z_offset_for_snapshots = settings.get("z_offset_for_snapshots", 0.0)
    first_snapshot_layer = settings.get("first_snapshot_layer", 1)
    orbit_radius_xy = settings.get("orbit_radius_xy", 30.0)
    start_angle_deg = settings.get("start_angle", 0)

    bed_x = settings.get("bed_dimensions", {}).get("x", 220.0)
    bed_y = settings.get("bed_dimensions", {}).get("y", 220.0)
    
    max_z_print = settings.get("max_z", 250.0) # Max Z of the print itself (from toolpath)
    min_z_print = settings.get("min_z_print", 0.0) # Min Z of the print itself (from toolpath)

    # Calculate center of the bed for the orbit path (assuming model is centered on bed)
    model_center_x = bed_x / 2.0
    model_center_y = bed_y / 2.0

    if debug_mode:
        print(f"DEBUG: Orbit Script calculated center: ({model_center_x:.2f}, {model_center_y:.2f}) based on bed dimensions X={bed_x:.2f} Y={bed_y:.2f}", file=sys.__stdout__)
        print(f"DEBUG: Orbit Script using Z range: Min Z Print={min_z_print:.2f}, Max Z Print={max_z_print:.2f}", file=sys.__stdout__)


    corkscrew_radius = max(orbit_radius_xy, 0.0)

    total_snapshots_to_take = num_orbits * snapshots_per_loop
    
    # Handle the case where only one snapshot is requested to avoid division by zero
    if total_snapshots_to_take <= 1:
        # If 0 or 1 snapshot, the progress factor will always be 0 (for 1st/only snapshot)
        # This will place it at min_z_print.
        if debug_mode: print("DEBUG: Orbit Script: total_snapshots_to_take is 0 or 1. Z-scaling will be fixed to min_z_print.", file=sys.stdout)
        effective_total_snapshots_for_scaling = 1 # Treat as 1 for scaling (only first point)
    else:
        effective_total_snapshots_for_scaling = total_snapshots_to_take


    # Track the actual Z height for the purpose of detecting *distinct* layer changes.
    current_z_for_distinct_layer_check = -9999.0 

    # Pattern to find G0/G1 moves and capture X, Y, Z, E values
    gcode_move_pattern = re.compile(r"^(G0|G1)\s*(?:X([-\d.]+))?\s*(?:Y([-\d.]+))?\s*(?:Z([-\d.]+))?\s*(?:E([-\d.]+))?")

    for line_idx, line in enumerate(gcode_lines):
        original_line = line.strip()

        # Update current position based on G0/G1 commands
        move_match = gcode_move_pattern.match(original_line)
        if move_match:
            cmd = move_match.group(1)
            x_str, y_str, z_str, e_str = move_match.group(2), move_match.group(3), move_match.group(4), move_match.group(5)

            # Store previous position before updating
            original_pos_x, original_pos_y, original_pos_z = current_x, current_y, current_z

            if not is_relative: # Absolute positioning (G90)
                if x_str is not None: current_x = float(x_str)
                if y_str is not None: current_y = float(y_str)
                if z_str is not None: current_z = float(z_str)
            else: # Relative positioning (G91)
                if x_str is not None: current_x += float(x_str)
                if y_str is not None: current_y += float(y_str)
                if z_str is not None: current_z += float(z_str)
            
            if e_str is not None:
                if cmd == "G1": # Only G1 moves are typically for extrusion
                    last_extruded_e = float(e_str) # This assumes absolute E. For relative E, more complex.
                else: # G0 moves often don't have E, or E is zero for travel
                    pass 

        # Check for G90/G91 mode changes
        if original_line.upper().startswith("G90"):
            is_relative = False
        elif original_line.upper().startswith("G91"):
            is_relative = True

        # --- Layer Change and Snapshot Trigger Logic ---
        # A flag to know if this line represents a change to a new logical layer
        is_new_logical_layer = False

        # 1. Prioritize explicit layer comments (e.g., ";LAYER:123")
        layer_num_match = re.search(r"; ?LAYER:(\d+)", original_line, re.IGNORECASE)
        if layer_num_match:
            new_parsed_layer = int(layer_num_match.group(1))
            if new_parsed_layer > current_logical_layer:
                current_logical_layer = new_parsed_layer
                is_new_logical_layer = True
        else:
            # 2. Fallback to significant Z change if no explicit layer number
            if abs(current_z - current_z_for_distinct_layer_check) > 0.05: 
                if current_z > current_z_for_distinct_layer_check: # Only increment if Z goes up
                    current_z_for_distinct_layer_check = current_z
                    current_logical_layer += 1 
                    is_new_logical_layer = True
                else:
                    pass 
        
        # Snapshot insertion condition:
        # Only insert if it's a new logical layer, above the first_snapshot_layer,
        # and we haven't already inserted a snapshot for this logical layer,
        # AND we still have snapshots left to take.
        if is_new_logical_layer and current_logical_layer >= first_snapshot_layer and \
           current_logical_layer not in layers_with_inserted_snapshots and \
           snapshots_taken_count < total_snapshots_to_take:

            # Calculate the progress factor based on the number of snapshots taken so far
            # This ensures even distribution in Z regardless of actual layer count
            if effective_total_snapshots_for_scaling <= 1:
                progress_factor = 0.0 # For 0 or 1 snapshot, it's always at the start (min_z_print)
            else:
                progress_factor = float(snapshots_taken_count) / (effective_total_snapshots_for_scaling - 1)
            
            # --- Scaled Z Height Calculation ---
            z_range_actual = max_z_print - min_z_print
            if z_range_actual < 0.1: # Handle very thin prints or no Z movement gracefully
                snapshot_base_z = min_z_print # Stay at the base Z
            else:
                # Scale the base Z from min_z_print to max_z_print using the progress factor
                snapshot_base_z = min_z_print + (z_range_actual * progress_factor)

            # The final safe Z position for the camera, incorporating hop and user offset.
            safe_z_for_snapshot = snapshot_base_z + z_hop_height + z_offset_for_snapshots
            
            # The angle calculation also uses the same progress_factor for consistency
            angle_deg = start_angle_deg + (progress_factor * num_orbits * 360) 
            angle_rad = math.radians(angle_deg)
            
            target_x = model_center_x + corkscrew_radius * math.cos(angle_rad)
            target_y = model_center_y + corkscrew_radius * math.sin(angle_rad)
            
            final_gcode.append(f"; --- PrintPath Corkscrew Snapshot for Layer {current_logical_layer} (Z={current_z:.2f}, Scaled Snapshot Z={safe_z_for_snapshot:.2f}) ---\n")
            final_gcode.append(f"G90 ; Set to Absolute positioning (for move)\n") 
            final_gcode.append(f"G0 X{target_x:.3f} Y{target_y:.3f} Z{safe_z_for_snapshot:.3f} F{travel_speed} ; Move to corkscrew snapshot position\n")
            
            if dwell_time > 0:
                final_gcode.append(f"G4 P{dwell_time} ; Dwell for camera\n")
            
            final_gcode.append("TIMELAPSE_TAKE_FRAME\n")
            
            snapshot_points_list.append((target_x, target_y, safe_z_for_snapshot)) 

            final_gcode.append(f"G0 X{original_pos_x:.3f} Y{original_pos_y:.3f} Z{original_pos_z:.3f} F{travel_speed} ; Return to original print position\n")
            
            if retract_length > 0:
                final_gcode.append(f"G91 ; Set to Relative positioning (for unretraction)\n")
                final_gcode.append(f"G1 E{retract_length:.3f} F{retract_speed * 60:.0f} ; Unretract {retract_length}mm\n")
                final_gcode.append(f"G90 ; Set to Absolute positioning (after unretract)\n")
            
            final_gcode.append(f"; --- END PrintPath Corkscrew Snapshot for Layer {current_logical_layer} ---\n")
        
            snapshots_taken_count += 1 # Increment the count *after* a snapshot is successfully inserted
            layers_with_inserted_snapshots.add(current_logical_layer) 
            if debug_mode: print(f"DEBUG: Orbit Script: Inserted snapshot for logical layer {current_logical_layer} (Z={current_z:.2f}). Total snapshots taken: {snapshots_taken_count}/{total_snapshots_to_take}", file=sys.stdout)
        
        final_gcode.append(line) 

    return final_gcode, snapshot_points_list
