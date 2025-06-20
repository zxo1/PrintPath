# SCRIPT_SETTINGS: {"num_orbits": {"type": "spinbox", "range": [1, 50], "step": 1, "label": "Total 360-degree Orbits per Print", "tooltip": "Total number of 360-degree rotations the camera will make over the entire print."}, "snapshots_per_loop": {"type": "spinbox", "range": [5, 60], "step": 1, "label": "Snapshots per 360-degree Loop", "tooltip": "Number of snapshots to take within each 360-degree rotation of the camera.'"}, "z_offset_for_snapshots": {"type": "doublespinbox", "label": "Snapshot Z Offset (mm)", "range": [-10.0, 10.0], "default": 0.0, "step": 0.1, "decimals": 1, "tooltip": "Additional Z offset applied to the snapshot height (can be negative or positive)."}, "first_snapshot_layer": {"type": "spinbox", "label": "First Snapshot Layer", "range": [0, 9999], "default": 1, "tooltip": "The first layer number (0-indexed) to begin taking snapshots. (Default: 1 for first print layer)"}, "orbit_radius_xy": {"type": "doublespinbox", "range": [10.0, 100.0], "step": 1.0, "decimals": 1, "label": "Orbit Radius (mm)", "tooltip": "The radius of the circular path around the object for snapshots."}, "start_angle": {"type": "spinbox", "label": "Start Angle (degrees)", "range": [0, 359], "default": 0, "tooltip": "The starting angle for the orbit (0 is front of bed)."}, "orbit_height": {"type": "combobox", "label": "Orbit Height", "items": ["Current Layer Z", "Fixed Z"], "default": "Current Layer Z", "tooltip": "Whether to snap at the current layer's Z or a fixed Z height."}, "fixed_z_height": {"type": "doublespinbox", "label": "Fixed Z Height (mm)", "range": [0.0, 300.0], "default": 50.0, "step": 1.0, "decimals": 1, "tooltip": "The fixed Z height for snapshots, if 'Fixed Z' is selected for Orbit Height."}}

import math
import re
import sys # Import sys for printing to stdout
# QPointF import is not needed here as per new design, it's handled in main.py/gcode_viewer.py
# from PyQt5.QtCore import QPointF 

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
                try:
                    info["total_layers"] = int(match.group(1))
                    total_layers_found = True
                except ValueError:
                    pass
            
            # Priority 2: PrusaSlicer format "Layers: N"
            if not total_layers_found and line_upper.startswith("LAYERS:"):
                match = re.search(r"LAYERS:\s*(\d+)", line_upper)
                if match:
                    try:
                        info["total_layers"] = int(match.group(1))
                        total_layers_found = True
                    except ValueError:
                        pass

            # Priority 3: Klipper/Fluidd/OctoPrint standard ";TOTAL_LAYERS:N"
            if not total_layers_found and line_upper.startswith(";TOTAL_LAYERS:"):
                try:
                    info["total_layers"] = int(line_upper.split(":")[1].strip())
                    total_layers_found = True
                except ValueError:
                    pass

            # Priority 4: Cura/SuperSlicer sometimes ";MAX_LAYER:N" (add 1 for 0-indexed)
            if not total_layers_found and line_upper.startswith(";MAX_LAYER:"):
                try:
                    info["total_layers"] = int(line_upper.split(":")[1].strip()) + 1
                    total_layers_found = True
                except ValueError:
                    pass

        # Try to parse bounding box information (NEW: Prioritize EXCLUDE_OBJECT_DEFINE)
        if not bbox_found:
            # Look for EXCLUDE_OBJECT_DEFINE POLYGON=[[min_x,min_y],[max_x,min_y],[max_x,max_y],[min_x,max_y],...]
            # Regex to capture the four coordinates of the polygon
            exclude_obj_match = re.search(r"POLYGON=\[\[([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)\],\[([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)\],\[([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)\],\[([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)\]", line, re.IGNORECASE)
            if exclude_obj_match:
                try:
                    # Extract coordinates and find min/max
                    coords = [float(exclude_obj_match.group(i)) for i in range(1, 9)]
                    xs = [coords[j] for j in [0, 2, 4, 6]]
                    ys = [coords[j] for j in [1, 3, 5, 7]]
                    info["min_x"] = min(xs)
                    info["max_x"] = max(xs)
                    info["min_y"] = min(ys)
                    info["max_y"] = max(ys)
                    bbox_found = True
                except ValueError:
                    pass # Continue to other bbox parsing methods

            # Fallback to "; bounding_box: X[0.0:100.0] Y[0.0:100.0] Z[0.0:50.0]" if EXCLUDE_OBJECT_DEFINE not found/parsed
            if not bbox_found: 
                bbox_match = re.search(r"X\[([-+]?\d*\.?\d+):([-+]?\d*\.?\d+)\]\s*Y\[([-+]?\d*\.?\d+):([-+]?\d*\.?\d+)\](?:\s*Z\[([-+]?\d*\.?\d+):([-+]?\d*\.?\d+)\])?", line, re.IGNORECASE)
                if bbox_match:
                    try:
                        info["min_x"] = float(bbox_match.group(1))
                        info["max_x"] = float(bbox_match.group(2))
                        info["min_y"] = float(bbox_match.group(3))
                        info["max_y"] = float(bbox_match.group(4)) 
                        if bbox_match.group(5) is not None and bbox_match.group(6) is not None:
                            info["max_z"] = float(bbox_match.group(6)) 
                        bbox_found = True
                    except ValueError:
                        pass # Continue if parsing fails for this line

            # Another common pattern for individual parameters (e.g., from PrusaSlicer)
            if not bbox_found: # Still try individual lines if other methods failed
                if "min_x" in line and info["min_x"] is None:
                    match = re.search(r"min_x\s*=\s*([-+]?\d*\.?\d+)", line)
                    if match: 
                        info["min_x"] = float(match.group(1))
                if "max_x" in line and info["max_x"] is None:
                    match = re.search(r"max_x\s*=\s*([-+]?\d*\.?\d+)", line)
                    if match: 
                        info["max_x"] = float(match.group(1))
                if "min_y" in line and info["min_y"] is None:
                    match = re.search(r"min_y\s*=\s*([-+]?\d*\.?\d+)", line)
                    if match: 
                        info["min_y"] = float(match.group(1))
                if "max_y" in line and info["max_y"] is None:
                    match = re.search(r"max_y\s*=\s*([-+]?\d*\.?\d+)", line)
                    if match: 
                        info["max_y"] = float(match.group(1))
                if "max_z" in line and info["max_z"] is None:
                    match = re.search(r"max_z\s*=\s*([-+]?\d*\.?\d+)", line)
                    if match: 
                        info["max_z"] = float(match.group(1))
                
                # Check if all bounding box components are now found from individual lines
                if all(info[k] is not None for k in ["min_x", "max_x", "min_y", "max_y"]):
                    bbox_found = True


        if total_layers_found and bbox_found:
            break
            
    return info


def run(settings, gcode_lines):
    """
    Processes G-code lines to insert corkscrew orbital moves and timelapse triggers.
    The camera position follows a spiral path around the print's estimated center.

    Args:
        settings (dict): A dictionary containing combined global and script-specific settings.
        gcode_lines (list): A list of strings, where each string is a line
                            from the input G-code file.

    Returns:
        tuple: A tuple containing:
            - list: A list of processed G-code lines with added camera commands.
            - list: A list of (x, y, z) tuples representing snapshot locations for the viewer.
    """
    new_gcode = []
    layer_count = 0 # Current layer number being processed
    snapshots_taken_count = 0
    # Store raw (x, y, z) tuples. The viewer will handle QPointF conversion if needed.
    snapshot_points_list = [] 

    # Flag to indicate if we've seen a LAYER_CHANGE and are waiting for a Z comment
    awaiting_z_for_layer_inference = False 

    # Track current absolute position. Assumes G90 (absolute positioning) by default.
    current_x = 0.0
    current_y = 0.0
    current_z = 0.0

    # Retrieve settings
    firmware = settings.get("firmware", "klipper")
    travel_speed = settings.get("travel_speed", 9000)
    dwell_time = settings.get("dwell_time", 500)
    retract_length = settings.get("retract_length", 0.5)
    retract_speed = settings.get("retract_speed", 40)
    z_hop_height = settings.get("z_hop_height", 0.2)

    # Script-specific settings (from SCRIPT_SETTINGS)
    num_orbits = int(settings.get("num_orbits", 3))
    snapshots_per_loop = int(settings.get("snapshots_per_loop", 20))
    z_offset_for_snapshots = float(settings.get("z_offset_for_snapshots", 0.0))
    first_snapshot_layer = int(settings.get("first_snapshot_layer", 1)) # Default changed to 1
    orbit_radius_xy = float(settings.get("orbit_radius_xy", 30.0))
    start_angle_deg = int(settings.get("start_angle", 0))
    orbit_height_mode = settings.get("orbit_height", "Current Layer Z")
    fixed_z_height = float(settings.get("fixed_z_height", 50.0))
    debug_mode = settings.get("debug_mode", False)

    # --- Get Model Info (Prioritize from main.py's parsing, fallback to local) ---
    total_layers_detected = settings.get("total_layers")
    min_x = settings.get("min_x")
    max_x = settings.get("max_x")
    min_y = settings.get("min_y")
    max_y = settings.get("max_y")
    max_z = settings.get("max_z")

    # If any info is missing from settings, try parsing locally
    if any(v is None for v in [total_layers_detected, min_x, max_x, min_y, max_y, max_z]):
        if debug_mode: print("DEBUG: Orbit Script: Some model info missing from settings. Attempting local parse.", file=sys.stdout)
        local_gcode_info = parse_gcode_info(gcode_lines)
        if total_layers_detected is None: total_layers_detected = local_gcode_info.get("total_layers")
        if min_x is None: min_x = local_gcode_info.get("min_x")
        if max_x is None: max_x = local_gcode_info.get("max_x")
        if min_y is None: min_y = local_gcode_info.get("min_y")
        if max_y is None: max_y = local_gcode_info.get("max_y")
        if max_z is None: max_z = local_gcode_info.get("max_z")
        
        if debug_mode: print(f"DEBUG: Orbit Script: Local parse result: {local_gcode_info}", file=sys.stdout)

    # Calculate average layer height if total_layers and max_z are known, for Z-based layer inference
    average_layer_height = None
    if total_layers_detected is not None and total_layers_detected > 0 and max_z is not None and max_z > 0:
        # Assuming first layer is at Z > 0, total_layers_detected includes layer 0 (if slicer starts at 0).
        # We try to calculate average height from the total print height.
        average_layer_height = max_z / max(1, total_layers_detected) 
        if debug_mode: print(f"DEBUG: Orbit Script: Inferred average layer height: {average_layer_height:.3f}mm (from total_layers={total_layers_detected}, max_z={max_z}).", file=sys.stdout)
    else:
        # Fallback to a common layer height if detection fails
        average_layer_height = 0.2 # Most common layer height
        if debug_mode: print(f"WARNING: Orbit Script: Could not reliably infer average layer height. Defaulting to {average_layer_height:.1f}mm.", file=sys.stdout)


    # --- Determine Model Center for Corkscrew ---
    if all(v is not None for v in [min_x, max_x, min_y, max_y]):
        model_center_x = (min_x + max_x) / 2.0
        model_center_y = (min_y + max_y) / 2.0
        if debug_mode: print(f"DEBUG: Orbit Script: Model center derived from bounding box: ({model_center_x:.2f}, {model_center_y:.2f}).", file=sys.stdout)
    else:
        # Fallback to center of a typical 220x220mm print bed if bounding box not found
        model_center_x = 220.0 / 2.0
        model_center_y = 220.0 / 2.0
        if debug_mode: print("WARNING: Orbit Script: Model bounding box not fully detected. Defaulting center to (110.0, 110.0).", file=sys.stdout)

    corkscrew_radius = max(orbit_radius_xy, 10.0) # Ensure a minimum sensible radius, but prioritize user input
    if debug_mode: print(f"DEBUG: Orbit Script: Corkscrew orbit radius set to {corkscrew_radius:.2f}mm.", file=sys.stdout)

    # --- Calculate Snapshot Interval and Angle Progression ---
    total_expected_snapshots = num_orbits * snapshots_per_loop
    
    # Calculate how many layers pass between each snapshot. This is ALWAYS derived.
    layer_interval_per_snapshot = 1 # Default to every layer if calculations can't provide a better interval
    
    if total_layers_detected is not None and total_layers_detected > 0 and total_expected_snapshots > 0:
        # Distribute snapshots evenly across relevant layers
        # The number of layers available for snapshots is from first_snapshot_layer up to total_layers_detected.
        # We need at least one layer to distribute over.
        layers_available_for_snapshots = max(1, total_layers_detected - first_snapshot_layer + 1) # +1 to include the end layer
        layer_interval_per_snapshot = max(1, round(layers_available_for_snapshots / total_expected_snapshots))
        
        if debug_mode: 
            print(f"DEBUG: Orbit Script: Total layers detected: {total_layers_detected}", file=sys.stdout)
            print(f"DEBUG: Orbit Script: First Snapshot Layer: {first_snapshot_layer}", file=sys.stdout)
            print(f"DEBUG: Orbit Script: Layers Available for Snapshots: {layers_available_for_snapshots}", file=sys.stdout)
            print(f"DEBUG: Orbit Script: Desired Orbits: {num_orbits}, Snapshots per Loop: {snapshots_per_loop}", file=sys.stdout)
            print(f"DEBUG: Orbit Script: Total snapshots calculated: {total_expected_snapshots}", file=sys.stdout)
            print(f"DEBUG: Orbit Script: Calculated snapshot interval (derived from total layers): {layer_interval_per_snapshot} layers.", file=sys.stdout)
    else:
        # Fallback if total layers unknown or if no snapshots are expected.
        # If total_expected_snapshots is 0, no snapshots will be taken anyway.
        # If total_layers_detected is None, we need a reasonable fallback interval.
        layer_interval_per_snapshot = 50 # Arbitrary reasonable default for unknown total layers (e.g., every 5mm layer at 0.1mm height)
        if debug_mode: print(f"WARNING: Orbit Script: Total layers not reliably detected or no snapshots expected. Falling back to default snapshot interval of {layer_interval_per_snapshot} layers.", file=sys.stdout)

    if total_expected_snapshots == 0:
        if debug_mode: print("WARNING: Orbit Script: Total expected snapshots is 0. No snapshots will be inserted.", file=sys.stdout)
        layer_interval_per_snapshot = -1 # This will cause `is_on_correct_interval` to be false if layer_interval_per_snapshot is 0 or negative


    total_angular_sweep_degrees = num_orbits * 360 # Total rotation for the entire print

    # Ensure max_z for snapshot moves doesn't go too low or too high relative to model
    # Use max_z from settings (or local parse fallback), and add safety margin
    calculated_max_snapshot_z = max_z + 5.0 if max_z is not None and max_z > 0 else 250.0
    calculated_max_snapshot_z = max(calculated_max_snapshot_z, z_hop_height + 1.0) # Ensure it's at least 1mm above z_hop for tiny prints


    new_gcode.append("; PrintPath by Zxodiacx - Corkscrew Orbit Mode\n")
    new_gcode.append(f"; Version: {settings.get('APP_VERSION', 'Unknown')}\n")
    new_gcode.append(f"; Firmware: {firmware.upper()}\n")
    new_gcode.append(f"; Orbit Radius: {orbit_radius_xy:.1f}mm\n")
    new_gcode.append(f"; Total 360-degree Orbits per Print: {num_orbits}\n")
    new_gcode.append(f"; Snapshots per 360-degree Loop: {snapshots_per_loop}\n")
    new_gcode.append(f"; Snapshot Z Offset: {z_offset_for_snapshots:.1f}mm\n")
    new_gcode.append(f"; First Snapshot Layer: {first_snapshot_layer}\n")
    new_gcode.append(f"; Calculated Snapshot Interval (Effective): Every {layer_interval_per_snapshot} layers\n")
    new_gcode.append(f"; Starting Angle: {start_angle_deg} degrees\n")
    new_gcode.append(f"; Orbit Height Mode: {orbit_height_mode}" + (f" ({fixed_z_height:.1f}mm)\n" if orbit_height_mode == "Fixed Z" else "\n"))
    new_gcode.append(f"; Travel Speed: {travel_speed}mm/min, Dwell Time: {dwell_time}ms\n")
    new_gcode.append(f"; Retract Length: {retract_length}mm at {retract_speed}mm/s, Z-Hop Height: {z_hop_height}mm\n")
    new_gcode.append(f"; Model BBox X:[{min_x:.1f}:{max_x:.1f}] Y:[{min_y:.1f}:{max_y:.1f}] Z:[0.0:{max_z:.1f}]\n")
    new_gcode.append(f"; Model Center (estimated): ({model_center_x:.2f}, {model_center_y:.2f})\n")
    if total_layers_detected is not None:
        new_gcode.append(f";   Total Layers Detected: {total_layers_detected}\n")
    else:
        new_gcode.append(f";   Total Layers: UNKNOWN (using fixed interval)\n")
    if average_layer_height is not None:
        new_gcode.append(f";   Inferred Average Layer Height: {average_layer_height:.3f}mm\n")
    new_gcode.append("G90 ; Ensure absolute positioning\n")
    new_gcode.append(f"M82 ; Ensure absolute extrusion (for safety, can be M83 for relative)\n")
    new_gcode.append("\n")

    coord_pattern = re.compile(r"([XYZFES])([-+]?\d*\.?\d+)")
    z_comment_pattern = re.compile(r";Z:(\d*\.?\d+)") # Pattern to capture Z from ;Z:X.X comment


    for line_index, line in enumerate(gcode_lines):
        # Update current X, Y, Z position by parsing G0/G1 commands
        if line.startswith("G0") or line.startswith("G1"):
            for match in coord_pattern.finditer(line):
                coord_type = match.group(1)
                value = float(match.group(2))
                if coord_type == 'X':
                    current_x = value
                elif coord_type == 'Y':
                    current_y = value
                elif coord_type == 'Z':
                    current_z = value

        # Flags for layer detection logic
        is_explicit_layer_change_comment = False
        explicit_layer_num_found_on_line = False

        # --- PRIMARY LAYER DETECTION ---
        # 1. Check for ";LAYER:N" (most reliable for direct layer number)
        if line.strip().startswith(";LAYER:"):
            try:
                part = line.strip().split(':', 1)[1].strip()
                if part.isdigit():
                    new_layer_num_from_comment = int(part)
                    if new_layer_num_from_comment > layer_count: # Only advance if it's a new, higher layer
                        layer_count = new_layer_num_from_comment
                        is_explicit_layer_change_comment = True
                        explicit_layer_num_found_on_line = True
                        awaiting_z_for_layer_inference = False # Reset if explicit layer found
                        if debug_mode: print(f"DEBUG: Orbit Script: Numerical ;LAYER:{layer_count} detected on line {line_index + 1}.", file=sys.stdout)
            except IndexError:
                pass
        # 2. Check for ";LAYER_CHANGE" (less specific, needs Z inference from next line)
        elif line.strip().startswith(";LAYER_CHANGE"):
            is_explicit_layer_change_comment = True
            awaiting_z_for_layer_inference = True # Set flag to look for Z in next lines
            if debug_mode: print(f"DEBUG: Orbit Script: Raw ;LAYER_CHANGE detected on line {line_index + 1}. Awaiting Z for inference.", file=sys.stdout)
        
        # --- LAYER INFERENCE FROM Z-COMMENT ---
        # This now handles Z comments on separate lines after LAYER_CHANGE
        if awaiting_z_for_layer_inference:
            z_match = z_comment_pattern.search(line)
            if z_match:
                z_value_from_comment = float(z_match.group(1))
                
                if average_layer_height is not None and average_layer_height > 0:
                    # Infer layer number: round(Z / average_layer_height)
                    # Add a small epsilon to handle floating point inaccuracies
                    inferred_layer = round(z_value_from_comment / average_layer_height + 1e-6) 
                    
                    if debug_mode: print(f"DEBUG: Orbit Script:   Attempting Z-based inference for layer_count. Current Z: {z_value_from_comment:.3f}, Avg Layer Ht: {average_layer_height:.3f}, Inferred Layer: {inferred_layer}.", file=sys.stdout)

                    # Only update layer_count if it's a new, higher layer number.
                    # This prevents resetting or going backwards due to imprecise Z values.
                    if inferred_layer > layer_count:
                        layer_count = inferred_layer
                        awaiting_z_for_layer_inference = False # Reset flag after successful inference
                        if debug_mode: print(f"DEBUG: Orbit Script:   Inferred layer {layer_count} from Z-comment {z_value_from_comment:.2f}mm (line {line_index + 1}). Layer_count updated.", file=sys.stdout)
                    elif debug_mode:
                        print(f"DEBUG: Orbit Script:   Inferred layer {inferred_layer} not higher than current layer {layer_count}. Skipping layer_count update.", file=sys.stdout)
                elif debug_mode:
                    print(f"DEBUG: Orbit Script: Cannot infer layer from Z-comment on line {line_index+1} because average_layer_height is unknown or zero ({average_layer_height}).", file=sys.stdout)
            elif debug_mode:
                # This message is important to debug why layer_count might not be increasing if expected
                # Only print this if we are actively awaiting Z and haven't found it on this line
                if not z_match and awaiting_z_for_layer_inference:
                    print(f"DEBUG: Orbit Script: No ;Z:X.X comment found on line {line_index+1} while awaiting Z. Continuing to look.", file=sys.stdout)


        # Now, proceed with snapshot logic using the potentially updated `layer_count`
        # Only evaluate snapshot conditions at explicit layer change markers OR if Z-based inference just happened
        # We use a boolean OR logic here because a successful Z-based inference IS a layer change event.
        if is_explicit_layer_change_comment or (not awaiting_z_for_layer_inference and layer_count > 0 and (line.strip().startswith(';Z:') or line.strip().startswith(';HEIGHT:'))): # Added ;HEIGHT: to trigger if Z isn't always present with LAYER_CHANGE
            if debug_mode: print(f"DEBUG: Orbit Script: Evaluating snapshot for current layer_count {layer_count} (line {line_index + 1}).", file=sys.stdout)
            
            # Check conditions for inserting a snapshot
            is_after_first_snapshot_layer = (layer_count >= first_snapshot_layer)
            is_on_correct_interval = False
            # Only check interval if first_snapshot_layer is met and interval is positive
            if is_after_first_snapshot_layer and layer_interval_per_snapshot > 0:
                 is_on_correct_interval = (layer_count - first_snapshot_layer) % layer_interval_per_snapshot == 0
            
            is_within_total_expected = (total_expected_snapshots > 0 and snapshots_taken_count < total_expected_snapshots)

            should_insert_snapshot = is_after_first_snapshot_layer and is_on_correct_interval and is_within_total_expected

            # --- EXPLICIT DEBUG CHECK ---
            if debug_mode: print(f"DEBUG: Orbit Script: Layer {layer_count}: should_insert_snapshot calculated as: {should_insert_snapshot}", file=sys.stdout)
            # --- END EXPLICIT DEBUG CHECK ---

            if debug_mode and not should_insert_snapshot:
                print(f"DEBUG: Orbit Script: Skipping snapshot for layer {layer_count} (line {line_index+1}) due to condition failure:", file=sys.stdout)
                print(f"  - Layer: {layer_count}", file=sys.stdout)
                print(f"  - First Snapshot Layer: {first_snapshot_layer}", file=sys.stdout)
                print(f"  - Snapshot Interval (calculated): {layer_interval_per_snapshot}", file=sys.stdout)
                print(f"  - Total Expected Snapshots: {total_expected_snapshots}", file=sys.stdout)
                print(f"  - Snapshots Taken So Far: {snapshots_taken_count}", file=sys.stdout)
                print(f"  - Condition Check:", file=sys.stdout)
                print(f"    - Layer >= First Snapshot Layer ({layer_count} >= {first_snapshot_layer}): {is_after_first_snapshot_layer}", file=sys.stdout)
                print(f"    - (Layer - First) % Interval == 0 (({layer_count} - {first_snapshot_layer}) % {layer_interval_per_snapshot} == 0): {is_on_correct_interval}", file=sys.stdout)
                print(f"    - Snapshots Taken < Total Expected ({snapshots_taken_count} < {total_expected_snapshots}): {is_within_total_expected}", file=sys.stdout)
                print(f"--- End Snapshot Skip Debug ---", file=sys.stdout)
            
            if should_insert_snapshot:
                if debug_mode: print(f"DEBUG: Orbit Script: Inserting snapshot sequence for layer {layer_count}", file=sys.stdout)
                
                original_pos_x = current_x
                original_pos_y = current_y
                original_pos_z = current_z

                new_gcode.append(f"; --- START PrintPath Corkscrew Snapshot for Layer {layer_count} ---\n")
                
                new_gcode.append(f"G90 ; Set to Absolute positioning\n")
                
                if retract_length > 0:
                    new_gcode.append(f"G91 ; Set to Relative positioning (for E move)\n")
                    new_gcode.append(f"G1 E-{retract_length:.3f} F{retract_speed * 60:.0f} ; Retract {retract_length}mm\n")
                
                target_snapshot_z = (fixed_z_height + z_offset_for_snapshots) if orbit_height_mode == "Fixed Z" else (original_pos_z + z_offset_for_snapshots)
                safe_z_for_snapshot = min(max(target_snapshot_z, z_hop_height), calculated_max_snapshot_z) 
                
                if orbit_height_mode == "Current Layer Z" and safe_z_for_snapshot < z_hop_height + 0.5:
                    safe_z_for_snapshot = z_hop_height + 0.5 

                new_gcode.append(f"G0 Z{original_pos_z + z_hop_height:.3f} F{travel_speed} ; Z-hop by {z_hop_height}mm\n")
                
                angle_deg = start_angle_deg
                if total_expected_snapshots > 1: # Calculate angle based on progress through expected snapshots
                    progress = (snapshots_taken_count) / max(1, total_expected_snapshots -1) 
                    progress = max(0.0, min(1.0, progress)) 
                    angle_deg = start_angle_deg + (progress * total_angular_sweep_degrees)
                else: # For 0 or 1 expected snapshot, no rotation
                    angle_deg = start_angle_deg 

                angle_rad = math.radians(angle_deg)
                
                target_x = model_center_x + corkscrew_radius * math.cos(angle_rad)
                target_y = model_center_y + corkscrew_radius * math.sin(angle_rad)

                new_gcode.append(f"G90 ; Set to Absolute positioning (again, for move)\n") 
                new_gcode.append(f"G0 X{target_x:.3f} Y{target_y:.3f} Z{safe_z_for_snapshot:.3f} F{travel_speed} ; Move to corkscrew snapshot position\n")
                
                if dwell_time > 0:
                    new_gcode.append(f"G4 P{dwell_time} ; Dwell for camera\n")
                new_gcode.append("TIMELAPSE_TAKE_FRAME\n")
                
                # Record the actual snapshot point for the viewer as raw (x, y, z) tuples
                snapshot_points_list.append((target_x, target_y, safe_z_for_snapshot)) 

                # Return to original position and unretract
                new_gcode.append(f"G0 X{original_pos_x:.3f} Y{original_pos_y:.3f} Z{original_pos_z:.3f} F{travel_speed} ; Return to original print position\n")
                
                if retract_length > 0:
                    new_gcode.append(f"G91 ; Set to Relative positioning (for unretraction)\n")
                    new_gcode.append(f"G1 E{retract_length:.3f} F{retract_speed * 60:.0f} ; Unretract {retract_length}mm\n")
                    new_gcode.append(f"G90 ; Set to Absolute positioning\n")
                
                new_gcode.append(f"; --- END PrintPath Corkscrew Snapshot for Layer {layer_count} ---\n")
                
                snapshots_taken_count += 1
                
        new_gcode.append(line) 

    return new_gcode, snapshot_points_list # Return both G-code and snapshot points
