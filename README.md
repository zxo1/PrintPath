PrintPath: G-code Post-Processor for Timelapse Camera PathsPrintPath is a desktop application designed to post-process G-code files, enabling the insertion of custom camera movements for creating stunning timelapses of your 3D prints. It features a graphical user interface (GUI) for easy configuration, dynamic script loading for flexible camera paths, and a built-in G-code previewer.✨ FeaturesGUI Interface: User-friendly desktop application built with PyQt5.Dynamic Script Loading: Easily add and switch between different post-processing scripts.G-code Previewer: Visualize your print's toolpath and the planned camera snapshot points in 2D (Top/Front views).Customizable Settings: Adjust global settings (travel speed, dwell time, retraction) and script-specific parameters through the GUI.Firmware Agnostic: Supports both Klipper and Marlin firmware (configurable per script).Automatic Saving: Processed G-code files are automatically saved with a descriptive filename.Extensible: Create your own Python scripts to define unique camera movements.🚀 InstallationPrintPath requires Python 3.x and PyQt5.Clone the repository:git clone https://github.com/YourGitHubUser/PrintPath.git
cd PrintPath
Create a virtual environment (recommended):python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
Install dependencies:pip install PyQt5
Create the scripts directory:Ensure there's a folder named scripts in the root directory of the application. This is where your G-code post-processing scripts will reside.mkdir scripts
Place example scripts:Copy the provided example scripts (e.g., orbit.py, arc.py) into the scripts directory.🏃‍♀️ UsageRun the application:# Ensure your virtual environment is active
python main.py
Open a G-code file:Go to File > Open G-code... and select your .gcode file. The viewer will parse and display a preview of your print.Select a Script:Choose a post-processing script from the "Select Script" dropdown (e.g., "orbit", "arc"). The available settings on the left panel will dynamically update based on the selected script.Adjust Settings:Modify the global settings (Travel Speed, Dwell Time, Retract Length, Z-Hop Height) and any script-specific settings (e.g., Number of Snapshots, Orbit Radius) to customize your camera path.Process G-code:Click the "Go!" button. The application will process your G-code, inserting the camera movement commands defined by the selected script. A progress bar will indicate activity.Review and Save:After processing, the processed snapshot points will appear in the previewer. The "Go!" button will change to "Open your_file_name_mode.gcode", allowing you to open the processed file directly with your system's default application. You can also manually save the processed G-code via File > Save Processed G-code or File > Save Processed G-code As....⚙️ Custom ScriptsPrintPath is designed to be highly extensible through custom Python scripts.Each script should be a .py file placed in the scripts directory and must contain:# SCRIPT_SETTINGS: comment: A JSON string on the first line defining the script's configurable parameters. This allows the GUI to dynamically generate input widgets.Example format:# SCRIPT_SETTINGS: {"setting_key": {"type": "spinbox/doublespinbox/combobox", "label": "Display Name", "range": [min, max], "default": value, "step": value, "decimals": int, "items": ["Item1", "Item2"], "tooltip": "Help text"}}
"type": spinbox (integer), doublespinbox (float), combobox (dropdown)."label": Text displayed in the GUI."range": [min, max] for spinboxes."default": Default value."step": Increment for spinboxes."decimals": Number of decimal places for doublespinbox."items": List of strings for combobox."tooltip": Hover-over help text.run(settings, gcode_lines) function: This is the main entry point for your script.settings (dict): A dictionary containing all global settings (e.g., travel_speed, dwell_time, firmware) and your script-specific settings as defined in # SCRIPT_SETTINGS:. It also includes parsed G-code information like min_x, max_x, min_y, max_y, max_z, total_layers, and bed_dimensions.gcode_lines (list of str): The original G-code content, line by line.Return Value: The function must return a tuple:A list of strings representing the modified G-code lines.A list of (x, y, z) tuples (floats) representing the calculated snapshot locations. These points will be displayed in the GCodeViewer.Script Example (my_custom_script.py)# SCRIPT_SETTINGS: {"my_custom_value": {"type": "spinbox", "label": "My Custom Setting", "range": [1, 10], "default": 5, "tooltip": "A custom setting."}}

import re
import math
import sys

def run(settings, gcode_lines):
    modified_lines = []
    snapshot_points = []

    # Access global settings
    travel_speed = settings.get("travel_speed", 9000)
    dwell_time = settings.get("dwell_time", 500)
    firmware = settings.get("firmware", "klipper")

    # Access script-specific setting
    my_custom_value = settings.get("my_custom_value", 5)

    # Access print dimensions
    center_x = (settings.get("min_x",0) + settings.get("max_x",0)) / 2.0
    center_y = (settings.get("min_y",0) + settings.get("max_y",0)) / 2.0
    
    # --- Example Logic: Insert a snapshot at every 10th layer ---
    layer_count = 0
    current_x, current_y, current_z = 0.0, 0.0, 0.0
    
    for line in gcode_lines:
        # Update current position (simplified for example)
        # In a real script, you'd parse G0/G1 for x, y, z
        if "X" in line and "Y" in line and "Z" in line:
            try:
                current_x = float(re.search(r"X([-\d.]+)", line).group(1))
                current_y = float(re.search(r"Y([-\d.]+)", line).group(1))
                current_z = float(re.search(r"Z([-\d.]+)", line).group(1))
            except AttributeError: # Regex might not find groups if malformed
                pass

        # Detect layer changes
        if ";LAYER:" in line:
            try:
                layer_count = int(line.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
            
            if layer_count > 0 and layer_count % 10 == 0: # Snapshot every 10 layers
                # Example: Move camera slightly offset from center
                snap_x = center_x + 50.0
                snap_y = center_y + 50.0
                snap_z = current_z + settings.get("z_hop_height", 0.2) + 5.0 # Z-hop + 5mm offset

                modified_lines.append(f"; --- Custom Snapshot at Layer {layer_count} --- \n")
                modified_lines.append("G90 ; Absolute positioning\n")
                modified_lines.append(f"G0 Z{snap_z:.3f} F{travel_speed}\n")
                modified_lines.append(f"G0 X{snap_x:.3f} Y{snap_y:.3f} F{travel_speed}\n")
                modified_lines.append(f"G4 P{dwell_time} ; Dwell\n")
                modified_lines.append("TIMELAPSE_TAKE_FRAME\n")
                modified_lines.append(f"G0 X{current_x:.3f} Y{current_y:.3f} Z{current_z:.3f} F{travel_speed} ; Return\n")
                modified_lines.append(f"; --- End Custom Snapshot --- \n")
                
                snapshot_points.append((snap_x, snap_y, snap_z)) # Add to visualization list

        modified_lines.append(line)
    
    return modified_lines, snapshot_points
🗺️ G-code ViewerThe integrated G-code viewer provides a real-time visualization of your print.Toolpath (Cyan): Shows the nozzle's movement during printing.Layer Start Points (Yellow): Indicates the beginning of each new layer.Processed Snapshot Points (Magenta): Shows the locations where the active script will trigger a timelapse snapshot.Views: Switch between "Top View (XY)" and "Front View (XZ)" to inspect your G-code and camera paths from different perspectives.Navigation:Pan: Click and drag with the left mouse button.Zoom: Use the mouse wheel (scroll up to zoom in, down to zoom out).Reset View: Press R or right-click and select "Reset View" to return to the default zoom and pan.🤝 ContributingPrintPath is currently a personal project. Contributions are welcome under the CC BY-NC-SA 4.0 International license. Feel free to explore the code, create issues for bugs, or suggest features. Pull requests are appreciated, especially for new post-processing scripts or general improvements that align with the project's non-commercial goals.📧 ContactFor any inquiries or feedback, please contact Zxodiacx at [your_email@example.com] (replace with your actual email).⚖️ LicensePrintPath is licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International Public License (CC BY-NC-SA 4.0).You are free to:Share — copy and redistribute the material in any medium or format.Adapt — remix, transform, and build upon the material.Under the following terms:Attribution — You must give appropriate credit, provide a link to the license, and indicate if changes were made. You may do so in any reasonable manner, but not in any way that suggests the licensor endorses you or your use.NonCommercial — You may not use the material for commercial purposes.ShareAlike — If you remix, transform, or build upon the material, you must distribute your contributions under the same license as the original.For the full legal text of the license, please refer to the LICENSE.txt file in this repository or visit Creative Commons website.
