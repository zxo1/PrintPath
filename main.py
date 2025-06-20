import sys
import os
import time
import json
import re

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QTextEdit, QLabel,
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QFrame, QAction, QMenuBar,
    QComboBox, QProgressBar, QGroupBox, QFormLayout, QDoubleSpinBox, QSpinBox,
    QPushButton, QCheckBox, QMessageBox, QSizePolicy
)
from PyQt5.QtGui import QIcon, QTextCharFormat, QColor, QFont, QTextCursor
from PyQt5.QtCore import Qt, QRegExp, QThread, pyqtSignal, QObject, QPointF 

from config import load_settings, save_settings, DEFAULT_SETTINGS 
from gcode_viewer import GCodeViewer 

# Store original stdout and stderr before any potential redirection
ORIGINAL_STDOUT = sys.stdout
ORIGINAL_STDERR = sys.stderr

# Constants for directory and default processing mode
SCRIPTS_DIR = "scripts"
DEFAULT_MODE = "orbit"
SETTINGS_FILE = "settings.json"
MAX_TITLE_FILENAME_LENGTH = 40
APP_VERSION = "1.0.0"

# Constants for bed dimension sanity checks
MIN_BED_DIMENSION = 50.0 # Minimum reasonable bed dimension in mm
DEFAULT_BED_X = 220.0
DEFAULT_BED_Y = 220.0


def load_script(mode):
    """
    Dynamically loads and returns the 'run' function from a script
    located in the SCRIPTS_DIR.
    """
    script_path = os.path.join(SCRIPTS_DIR, f"{mode}.py")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script '{mode}' not found at {script_path}.")
    scope = {}
    with open(script_path, "r") as f:
        exec(f.read(), scope)
    if "run" not in scope:
        raise AttributeError(f"Script '{mode}' does not define a 'run' function.")
    return scope["run"]

# Helper class to redirect stdout to a signal
class StreamRedirect(QObject):
    """Redirects stdout to a Qt signal and also prints to the original console."""
    message_signal = pyqtSignal(str, str) # message, type (e.g., 'info', 'debug')

    def __init__(self, log_signal, default_log_type="info", original_stdout=None, fallback_stderr=None):
        super().__init__()
        self._log_signal = log_signal
        self._default_log_type = default_log_type
        # Use the provided original_stdout or the globally captured one
        self._original_stdout = original_stdout if original_stdout is not None else ORIGINAL_STDOUT
        # Use the provided fallback_stderr or the globally captured one
        self._fallback_stderr = fallback_stderr if fallback_stderr is not None else ORIGINAL_STDERR 

    def write(self, text):
        if text.strip(): # Only emit if there's actual text
            # Determine log type based on message content
            msg_type = self._default_log_type
            if text.strip().upper().startswith("WARNING:"):
                msg_type = "warning"
            elif text.strip().upper().startswith("ERROR:"):
                msg_type = "error"
            elif text.strip().upper().startswith("DEBUG:"):
                msg_type = "debug"
            
            # Emit signal to update GUI log console
            self._log_signal.emit(text.strip(), msg_type)
            
            # Write to the original stdout to ensure console output
            if self._original_stdout:
                try:
                    self._original_stdout.write(f"[{msg_type.upper()}] {text}\n") # Added newline for clarity in console
                    self._original_stdout.flush()
                except Exception as e:
                    # Fallback print using built-in print() to captured stderr
                    print(f"Error writing to original_stdout: {e} - Message: {text}", file=self._fallback_stderr)
                    self._fallback_stderr.flush()

    def flush(self):
        if self._original_stdout:
            self._original_stdout.flush()


# --- Worker Thread for G-code Processing (for post-processing scripts) ---
class GCodeProcessorThread(QThread):
    """
    A QThread subclass to run the G-code processing in a separate thread,
    preventing the GUI from freezing.
    """
    # Modified signal: now emits processed_content ONLY
    finished = pyqtSignal(str, str, str) # Signals: original_filepath, processed_content, mode
    error = pyqtSignal(str) # Signal for error messages
    log_signal = pyqtSignal(str, str) # Signal for logging messages: (message, type)

    def __init__(self, filepath, mode, settings):
        super().__init__()
        self.filepath = filepath
        self.mode = mode
        self.settings = settings 
        self.old_stdout = None # To store the original stdout

    def run(self):
        """
        Executes the G-code processing logic in this thread.
        """
        processed_content = ""
        try:
            # Store the original stdout and redirect
            self.old_stdout = sys.stdout
            # Pass sys.__stderr__ explicitly to StreamRedirect
            sys.stdout = StreamRedirect(self.log_signal, "debug" if self.settings.get("debug_mode", False) else "info", self.old_stdout, sys.__stderr__)
            
            with open(self.filepath, "r") as f:
                gcode_lines = f.readlines()

            run_func = load_script(self.mode)
            
            # Expect run_func to return only processed_gcode_lines (list of strings)
            new_lines = run_func(self.settings, gcode_lines)
            processed_content = "".join(new_lines)

            self.finished.emit(self.filepath, processed_content, self.mode)

        except FileNotFoundError as e:
            self.error.emit(f"Error: {e}")
            self.log_signal.emit(f"Error: {e}", "error")
        except AttributeError as e:
            self.error.emit(f"Script Error: {e}")
            self.log_signal.emit(f"Script Error: {e}", "error")
        except Exception as e:
            self.error.emit(f"An unexpected error occurred during processing: {e}")
            self.log_signal.emit(f"An unexpected error occurred during processing: {e}", "error")
        finally:
            if self.old_stdout: # Ensure stdout is restored
                sys.stdout = self.old_stdout

# --- Worker Thread for G-code File Parsing (for preview) ---
class GCodeParseThread(QThread):
    """
    A QThread subclass for parsing G-code file content to extract
    bed dimensions and toolpath data for the previewer.
    """
    # Modified signal: now emits a list of (QPointF(x,y), z_value) tuples
    finished = pyqtSignal(dict, list, list) # Signals: gcode_info, toolpath_data, layer_start_points
    error = pyqtSignal(str) # Signal for error messages
    log_signal = pyqtSignal(str, str) # Signal for logging messages: (message, type)

    def __init__(self, filepath):
        super().__init__()
        self.filepath = filepath
        self.old_stdout = None # To store the original stdout
        self.is_relative_positioning = False # Internal state for parsing

    def run(self):
        """
        Executes the G-code parsing logic in this thread.
        """
        try:
            # Redirect stdout for this thread's logs
            self.old_stdout = sys.stdout
            # Pass sys.__stderr__ explicitly to StreamRedirect
            sys.stdout = StreamRedirect(self.log_signal, "debug", self.old_stdout, sys.__stderr__) # Always debug for parsing thread

            self.log_signal.emit(f"Parsing G-code file: {os.path.basename(self.filepath)}", "info")

            with open(self.filepath, "r") as f:
                gcode_lines = f.readlines()
            
            gcode_info = self._parse_gcode_info_main_app(gcode_lines)
            toolpath_data, layer_start_points = self._parse_gcode_toolpath(gcode_lines) # Now returns toolpath and layer points

            self.finished.emit(gcode_info, toolpath_data, layer_start_points)

        except FileNotFoundError:
            self.error.emit(f"Error: File not found at {self.filepath}")
            self.log_signal.emit(f"Error: File not found at {self.filepath}", "error")
        except Exception as e:
            self.error.emit(f"An unexpected error occurred during G-code parsing: {e}")
            self.log_signal.emit(f"An unexpected error occurred during G-code parsing: {e}", "error")
        finally:
            if self.old_stdout: # Ensure stdout is restored
                sys.stdout = self.old_stdout

    def _parse_gcode_info_main_app(self, lines):
        """
        Parses G-code lines to extract various information for the main app UI.
        This is a more comprehensive parser than the one in scripts.
        """
        info = {
            "gcode_flavor": None,
            "total_layers": None,
            "min_x": None, "max_x": None,
            "min_y": None, "max_y": None,
            "max_z": None,
            "bed_dimensions": None 
        }

        self.log_signal.emit("Starting G-code info parsing...", "debug")
        for line_num, line in enumerate(lines):
            line_upper = line.strip().upper()

            # G-code flavor
            if info["gcode_flavor"] is None:
                match = re.search(r";\s*gcode_flavor\s*=\s*(\w+)", line, re.IGNORECASE)
                if match:
                    flavor = match.group(1).lower()
                    if flavor in ["klipper", "marlin"]:
                        info["gcode_flavor"] = flavor
                        self.log_signal.emit(f"Line {line_num + 1}: Detected G-code flavor: {flavor}", "debug")
            
            # Total layers
            if info["total_layers"] is None:
                match = re.search(r";\s*total layer number:\s*(\d+)", line, re.IGNORECASE)
                if match: info["total_layers"] = int(match.group(1))
                if info["total_layers"] is None:
                    match = re.search(r"LAYERS:\s*(\d+)", line_upper)
                    if match: info["total_layers"] = int(match.group(1))
                if info["total_layers"] is None and line_upper.startswith(";TOTAL_LAYERS:"):
                    try: info["total_layers"] = int(line_upper.split(":")[1].strip())
                    except ValueError: pass
                if info["total_layers"] is None and line_upper.startswith(";MAX_LAYER:"):
                    try: info["total_layers"] = int(line_upper.split(":")[1].strip()) + 1
                    except ValueError: pass
                if info["total_layers"] is not None:
                    self.log_signal.emit(f"Line {line_num + 1}: Detected total layers: {info['total_layers']}", "debug")


            # Object Bounding Box
            if info["min_x"] is None:
                # Try POLYGON format
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
                        self.log_signal.emit(f"Line {line_num + 1}: Detected object bbox via POLYGON: X[{info['min_x']}:{info['max_x']}] Y[{info['min_y']}:{info['max_y']}]", "debug")
                    except ValueError:
                        self.log_signal.emit(f"Line {line_num + 1}: Error parsing POLYGON coordinates.", "debug")
                        pass

            if info["min_x"] is None: 
                # Try generic BBOX format
                bbox_match = re.search(r"X\[([-+]?\d*\.?\d+):([-+]?\d*\.?\d+)\]\s*Y\[([-+]?\d*\.?\d+):([-+]?\d*\.?\d+)\](?:\s*Z\[([-+]?\d*\.?\d+):([-+]?\d*\.?\d+)\])?", line, re.IGNORECASE)
                if bbox_match:
                    try:
                        info["min_x"] = float(bbox_match.group(1))
                        info["max_x"] = float(bbox_match.group(2))
                        info["min_y"] = float(bbox_match.group(3))
                        info["max_y"] = float(bbox_match.group(4)) 
                        if bbox_match.group(5) and bbox_match.group(6):
                            info["max_z"] = float(bbox_match.group(6)) 
                        self.log_signal.emit(f"Line {line_num + 1}: Detected object bbox: X[{info['min_x']}:{info['max_x']}] Y[{info['min_y']}:{info['max_y']}] Z[{info.get('min_z', 'N/A')}:{info.get('max_z', 'N/A')}]", "debug")
                    except ValueError: 
                        self.log_signal.emit(f"Line {line_num + 1}: Error parsing bbox coordinates.", "debug")
                        pass
            
            # Max Z height
            if info["max_z"] is None: 
                max_z_match = re.search(r"(?:max_z_height|max_z)\s*[=:]\s*([-+]?\d*\.?\d+)", line, re.IGNORECASE) 
                if max_z_match: 
                    try:
                        info["max_z"] = float(max_z_match.group(1))
                        self.log_signal.emit(f"Line {line_num + 1}: Detected max_z: {info['max_z']}", "debug")
                    except ValueError:
                        self.log_signal.emit(f"Line {line_num + 1}: Error parsing max_z value.", "debug")
                        pass


            # --- Bed Dimension Parsing and Immediate Validation ---
            if info["bed_dimensions"] is None:
                x_dim, y_dim = None, None
                
                # Combined regex for common bed dimension comments
                bed_dim_match = re.search(r"(?:bed_size|print_bed_size|bed_shape)\s*[=:]\s*(\S+?)(?:x|,\s*)(\S+)", line, re.IGNORECASE)
                
                if bed_dim_match:
                    try:
                        x_str = bed_dim_match.group(1).replace(",", "") # Remove comma if present
                        y_str = bed_dim_match.group(2).replace(",", "")
                        
                        x_dim = float(x_str)
                        y_dim = float(y_str)

                        # Immediate sanity check
                        if x_dim < MIN_BED_DIMENSION or y_dim < MIN_BED_DIMENSION:
                            self.log_signal.emit(f"WARNING: Line {line_num + 1}: Detected bed dimensions X:{x_dim:.1f}, Y:{y_dim:.1f} which are too small. Defaulting to {DEFAULT_BED_X}x{DEFAULT_BED_Y}mm.", "warning")
                            x_dim = DEFAULT_BED_X
                            y_dim = DEFAULT_BED_Y
                        
                        info["bed_dimensions"] = {"x": x_dim, "y": y_dim}
                        self.log_signal.emit(f"Line {line_num + 1}: Final bed dimensions set to: {info['bed_dimensions']['x']}x{info['bed_dimensions']['y']}", "debug")

                    except ValueError:
                        self.log_signal.emit(f"Line {line_num + 1}: Error parsing bed dimensions from '{line.strip()}'.", "debug")
                        pass
                
                # NEW: Parse 'printable_area' comment format
                if info["bed_dimensions"] is None:
                    # Regex to capture the maxX and maxY from the pattern like '0x0,220x0,220x220,0x220'
                    printable_area_match = re.search(r";\s*printable_area\s*=\s*[-\d.]+x[-\d.]+,\s*[-\d.]+x[-\d.]+,\s*([-\d.]+)x([-\d.]+),", line, re.IGNORECASE)
                    if printable_area_match:
                        try:
                            # Group 1 is maxX, Group 2 is maxY from 'maxX x maxY,' part
                            x_dim = float(printable_area_match.group(1))
                            y_dim = float(printable_area_match.group(2))

                            # Immediate sanity check
                            if x_dim < MIN_BED_DIMENSION or y_dim < MIN_BED_DIMENSION:
                                self.log_signal.emit(f"WARNING: Line {line_num + 1}: Detected printable_area dimensions X:{x_dim:.1f}, Y:{y_dim:.1f} which are too small. Defaulting to {DEFAULT_BED_X}x{DEFAULT_BED_Y}mm.", "warning")
                                x_dim = DEFAULT_BED_X
                                y_dim = DEFAULT_BED_Y
                            
                            info["bed_dimensions"] = {"x": x_dim, "y": y_dim}
                            self.log_signal.emit(f"Line {line_num + 1}: Final bed dimensions set from printable_area: {info['bed_dimensions']['x']}x{info['bed_dimensions']['y']}", "debug")
                        except ValueError:
                            self.log_signal.emit(f"Line {line_num + 1}: Error parsing printable_area dimensions from '{line.strip()}'.", "debug")
                            pass
            
            # --- Early Exit Optimization ---
            # If all crucial info (including valid bed dimensions) is found, break early.
            if all(info[k] is not None for k in ["total_layers", "min_x", "max_x", "min_y", "max_y", "max_z", "gcode_flavor"]):
                if info["bed_dimensions"] is not None and \
                   info["bed_dimensions"]["x"] >= MIN_BED_DIMENSION and info["bed_dimensions"]["y"] >= MIN_BED_DIMENSION:
                    self.log_signal.emit(f"Line {line_num + 1}: All primary info (layers, bbox, flavor, valid bed) found. Stopping parsing early.", "debug")
                    break 
                
        # Final fallback for bed dimensions if not found or still invalid after loop
        if info["bed_dimensions"] is None or \
           info["bed_dimensions"]["x"] < MIN_BED_DIMENSION or info["bed_dimensions"]["y"] < MIN_BED_DIMENSION:
            
            old_x = info["bed_dimensions"].get("x") if info["bed_dimensions"] else "N/A"
            old_y = info["bed_dimensions"].get("y") if info["bed_dimensions"] else "N/A"
            
            info["bed_dimensions"] = {"x": DEFAULT_BED_X, "y": DEFAULT_BED_Y} 
            self.log_signal.emit(f"WARNING: Bed dimensions were not reliably detected from G-code (found X:{old_x}, Y:{old_y}). Defaulting to {DEFAULT_BED_X}x{DEFAULT_BED_Y}mm for robustness.", "warning")


        self.log_signal.emit(f"Finished G-code info parsing. Final detected info: {info}", "debug")
        return info

    def _parse_gcode_toolpath(self, lines):
        """
        Parses G-code lines to extract a list of (x, y, z) coordinates
        representing the toolpath.
        Returns a list of tuples: [(QPointF(x, y), z_value), ...].
        Handles G90 (absolute) and G91 (relative) positioning.
        Additionally, identifies potential layer start points for snapshots.
        """
        toolpath_points = [] # Stores (QPointF(x,y), z) tuples
        layer_start_points = [] # Stores (QPointF(x,y), z) tuples for layer starts
        
        current_x, current_y, current_z = 0.0, 0.0, 0.0 
        is_relative = False # Start assuming absolute unless G91 is encountered
        current_layer = -1 # Track the current layer
        layer_change_detected = False # Flag to mark if a new layer comment was just seen

        # Regex to find G0/G1 commands and extract X, Y, Z values
        gcode_move_pattern = re.compile(r"^(G0|G1)\s*(?:X([-\d.]+))?\s*(?:Y([-\d.]+))?\s*(?:Z([-\d.]+))?\s*(?:E([-\d.]+))?.*$")

        self.log_signal.emit("Starting G-code toolpath parsing...", "debug")
        
        for line_num, line in enumerate(lines):
            line = line.strip() 
            line_upper = line.upper()

            # Check for G90/G91 directly
            if line_upper.startswith("G90"):
                is_relative = False
                continue 
            elif line_upper.startswith("G91"):
                is_relative = True
                continue 
            
            # Check for layer change comments (e.g., from PrusaSlicer, Cura)
            layer_match = re.search(r";LAYER:(\d+)", line_upper)
            if layer_match:
                new_layer = int(layer_match.group(1))
                if new_layer > current_layer:
                    current_layer = new_layer
                    layer_change_detected = True
                    self.log_signal.emit(f"Line {line_num + 1}: Detected new layer comment: {current_layer}", "debug")
                continue # Process next line, expecting a move command soon

            # Check for move commands (G0 or G1)
            match = gcode_move_pattern.match(line)
            if match:
                x_str, y_str, z_str, e_str = match.group(2), match.group(3), match.group(4), match.group(5)
                
                prev_x, prev_y, prev_z = current_x, current_y, current_z

                if x_str is not None:
                    x_val = float(x_str)
                    current_x = prev_x + x_val if is_relative else x_val
                
                if y_str is not None:
                    y_val = float(y_str)
                    current_y = prev_y + y_val if is_relative else y_val
                
                if z_str is not None:
                    z_val = float(z_str)
                    current_z = prev_z + z_val if is_relative else z_val
                
                # Add the point if it's a new unique (X,Y,Z) position
                if not toolpath_points or (QPointF(current_x, current_y), current_z) != toolpath_points[-1]:
                    toolpath_points.append((QPointF(current_x, current_y), current_z))

                    # If a layer change was just detected AND this is a printing move (E present)
                    # or it's the first move after the layer comment, add it as a snapshot point.
                    # We also add a point if current Z is significantly higher than previous Z
                    # AND it's not a retract/unretract (i.e. E-value is not present or 0)
                    if layer_change_detected and (e_str is not None or current_z > prev_z + 0.05): # Use a small tolerance for Z change
                        layer_start_points.append((QPointF(current_x, current_y), current_z))
                        self.log_signal.emit(f"Line {line_num + 1}: Added layer start point: ({current_x:.1f}, {current_y:.1f}, Z={current_z:.1f}) for layer {current_layer}", "debug")
                        layer_change_detected = False # Reset flag after adding the point

        self.log_signal.emit(f"Finished G-code toolpath parsing. Parsed {len(toolpath_points)} toolpath points for preview, including Z coordinates.", "debug")
        self.log_signal.emit(f"Detected {len(layer_start_points)} potential layer start points for snapshots.", "debug")

        if not toolpath_points:
            self.log_signal.emit("Warning: No X/Y movement commands found or parsed in the G-code for the preview.", "warning")
        return toolpath_points, layer_start_points


class PrintPathApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PrintPath")
        if os.path.exists("icon.png"):
            self.setWindowIcon(QIcon("icon.png"))
        else:
            self._log_message("Warning: icon.png not found. Using default icon.", "warning")
        self.setGeometry(100, 100, 1000, 700)

        self.processed_gcode_content = None
        self.original_gcode_filepath = None 
        self.gcode_bed_dimensions = None 
        self.gcode_toolpath_data = None # Now stores list of (QPointF, float) tuples
        self.gcode_layer_start_points = [] # New: Stores (QPointF, float) tuples for layer start points (pre-processing)
        self.processed_snapshot_points = [] # Moved to GCodeViewer, but reset here for clarity.
        self.gcode_info_full_data = {} # New: to store all parsed info including total_layers

        self.script_global_settings_map = {} 
        self.script_custom_settings_defs_map = {} 

        self.global_default_settings = DEFAULT_SETTINGS 
        self.current_settings = load_settings() 

        self.last_used_directory = self.current_settings.get("last_used_directory", "")
        if not self.last_used_directory or not os.path.isdir(self.last_used_directory):
            try:
                self.last_used_directory = os.path.expanduser("~/Documents")
                if not os.path.isdir(self.last_used_directory):
                    self.last_used_directory = os.path.expanduser("~") 
            except Exception:
                self.last_used_directory = os.getcwd() 

        # --- Left Panel Widgets ---
        self.script_combo = QComboBox() 
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(0)
        self.progress_bar.hide()

        # Settings Group Box
        self.settings_group_box = QGroupBox("Script Settings")
        self.settings_form_layout = QFormLayout()
        
        self.global_setting_widgets = {} 
        self.script_specific_setting_widgets = {}
        self.dynamic_setting_rows = [] 

        # --- GLOBAL Settings UI Elements (created once) ---
        self.firmware_label = QLabel("Firmware:")
        self.firmware_input = QComboBox()
        self.firmware_input.addItem("klipper")
        self.firmware_input.addItem("marlin")
        self.firmware_input.setCurrentText(self.current_settings.get("firmware", self.global_default_settings.get("firmware", "klipper")))
        self.firmware_input.currentTextChanged.connect(lambda text: self._update_setting("firmware", text))
        self.firmware_input.setToolTip("Select the firmware your 3D printer uses (e.g., Klipper, Marlin).")
        self.settings_form_layout.addRow(self.firmware_label, self.firmware_input)
        self.global_setting_widgets["firmware"] = (self.firmware_label, self.firmware_input)

        self.travel_speed_label = QLabel("Travel Speed (mm/min):") 
        self.travel_speed_input = QSpinBox()
        self.travel_speed_input.setRange(1000, 30000)
        self.travel_speed_input.setSingleStep(100)
        self.travel_speed_input.setValue(self.current_settings.get("travel_speed", self.global_default_settings.get("travel_speed", 2500)))
        self.travel_speed_input.valueChanged.connect(lambda value: self._update_setting("travel_speed", value))
        self.travel_speed_input.setToolTip("The speed at which the nozzle travels during non-printing moves for snapshots.")
        self.settings_form_layout.addRow(self.travel_speed_label, self.travel_speed_input)
        self.global_setting_widgets["travel_speed"] = (self.travel_speed_label, self.travel_speed_input) 

        self.dwell_time_label = QLabel("Dwell Time (ms):")
        self.dwell_time_input = QSpinBox()
        self.dwell_time_input.setRange(0, 5000)
        self.dwell_time_input.setSingleStep(50)
        self.dwell_time_input.setValue(self.current_settings.get("dwell_time", self.global_default_settings.get("dwell_time", 500)))
        self.dwell_time_input.valueChanged.connect(lambda value: self._update_setting("dwell_time", value))
        self.dwell_time_input.setToolTip("The duration (in milliseconds) the printer waits at the snapshot position.")
        self.settings_form_layout.addRow(self.dwell_time_label, self.dwell_time_input)
        self.global_setting_widgets["dwell_time"] = (self.dwell_time_label, self.dwell_time_input)

        self.retract_length_label = QLabel("Retract Length (mm):")
        self.retract_length_input = QDoubleSpinBox()
        self.retract_length_input.setRange(0.0, 10.0)
        self.retract_length_input.setSingleStep(0.1)
        self.retract_length_input.setValue(self.current_settings.get("retract_length", self.global_default_settings.get("retract_length", 0.5)))
        self.retract_length_input.valueChanged.connect(lambda value: self._update_setting("retract_length", value))
        self.retract_length_input.setToolTip("The amount of filament to retract before a travel move to prevent oozing.")
        self.settings_form_layout.addRow(self.retract_length_label, self.retract_length_input)
        self.global_setting_widgets["retract_length"] = (self.retract_length_label, self.retract_length_input)

        self.retract_speed_label = QLabel("Retract Speed (mm/s):")
        self.retract_speed_input = QSpinBox()
        self.retract_speed_input.setRange(1, 200)
        self.retract_speed_input.setSingleStep(5)
        self.retract_speed_input.setValue(self.current_settings.get("retract_speed", self.global_default_settings.get("retract_speed", 40)))
        self.retract_speed_input.valueChanged.connect(lambda value: self._update_setting("retract_speed", value))
        self.retract_speed_input.setToolTip("The speed at which filament is retracted and unretracted.")
        self.settings_form_layout.addRow(self.retract_speed_label, self.retract_speed_input)
        self.global_setting_widgets["retract_speed"] = (self.retract_speed_label, self.retract_speed_input)

        self.z_hop_height_label = QLabel("Z-Hop Height (mm):")
        self.z_hop_height_input = QDoubleSpinBox()
        self.z_hop_height_input.setRange(0.0, 5.0)
        self.z_hop_height_input.setSingleStep(0.05)
        self.z_hop_height_input.setValue(self.current_settings.get("z_hop_height", self.global_default_settings.get("z_hop_height", 0.5)))
        self.z_hop_height_input.valueChanged.connect(lambda value: self._update_setting("z_hop_height", value))
        self.z_hop_height_input.setToolTip("The vertical distance the nozzle lifts during travel moves for snapshots.")
        self.settings_form_layout.addRow(self.z_hop_height_label, self.z_hop_height_input)
        self.global_setting_widgets["z_hop_height"] = (self.z_hop_height_label, self.z_hop_height_input)
        
        self.settings_group_box.setLayout(self.settings_form_layout)
        self.settings_group_box.setFlat(True)

        self.go_button = QPushButton("Go!")
        self.go_button.clicked.connect(self._go_button_clicked) 
        self.go_button.setEnabled(False) 
        
        # --- Right Panel Widgets ---
        self.gcode_viewer = GCodeViewer()
        self.gcode_viewer.setMinimumSize(400, 300)
        # Set size policy to expanding for both horizontal and vertical directions
        self.gcode_viewer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Initialize GCodeViewer with default bed dimensions and an empty toolpath
        # This ensures the viewer always has a valid drawing context.
        self.gcode_viewer.set_bed_dimensions(DEFAULT_BED_X, DEFAULT_BED_Y, 250.0) # Assume 250 max Z
        self.gcode_viewer.set_gcode_data([]) # Start with empty toolpath
        self.gcode_viewer.set_layer_start_points([]) # Initialize empty layer start points
        self.gcode_viewer.set_processed_snapshot_points([]) # Initialize empty processed snapshot points


        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_formats = {
            "info": QTextCharFormat(),
            "warning": QTextCharFormat(),
            "error": QTextCharFormat(),
            "debug": QTextCharFormat()
        }
        self.log_formats["info"].setForeground(QColor("#CCCCCC"))    
        self.log_formats["warning"].setForeground(QColor("#FFA500")) 
        self.log_formats["error"].setForeground(QColor("#FF0000"))   
        self.log_formats["debug"].setForeground(QColor("#808080"))   
        
        # New: Clear Log button
        self.clear_log_button = QPushButton("Clear Log")
        self.clear_log_button.clicked.connect(self._clear_log_console)

        # --- View Mode Selector ---
        self.view_mode_label = QLabel("Preview View:")
        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItem("Top View (XY)")
        self.view_mode_combo.addItem("Front View (XZ)")
        self.view_mode_combo.setCurrentText(self.current_settings.get("preview_view_mode", "Top View (XY)"))
        self.view_mode_combo.currentTextChanged.connect(self._update_view_mode)
        self.view_mode_combo.setToolTip("Select the perspective for the G-code preview.")
        
        # Add view mode selector to a horizontal layout below the viewer
        view_mode_layout = QHBoxLayout()
        view_mode_layout.addStretch(1) # Push to center
        view_mode_layout.addWidget(self.view_mode_label)
        view_mode_layout.addWidget(self.view_mode_combo)
        view_mode_layout.addStretch(1)

        # --- Left Panel Layout ---
        left_panel_layout = QVBoxLayout()
        left_panel_layout.addWidget(QLabel("Select Script:"))
        left_panel_layout.addWidget(self.script_combo)
        left_panel_layout.addWidget(self.progress_bar)
        left_panel_layout.addWidget(self.settings_group_box)
        left_panel_layout.addWidget(self.go_button) 
        left_panel_layout.addStretch(1)


        # --- Right Panel Layout ---
        right_panel_layout = QVBoxLayout()
        right_panel_layout.addWidget(QLabel("Print Preview:"))
        right_panel_layout.addWidget(self.gcode_viewer) 
        right_panel_layout.addLayout(view_mode_layout) # Add the view mode selector
        right_panel_layout.addWidget(QLabel("Log Console:"))
        right_panel_layout.addWidget(self.log_console)
        right_panel_layout.addWidget(self.clear_log_button)

        right_panel_layout.setStretch(1, 1)
        right_panel_layout.setStretch(3, 1)


        # --- Frames for styling and structure ---
        left_frame = QFrame()
        left_frame.setLayout(left_panel_layout)
        left_frame.setFrameShape(QFrame.StyledPanel)
        left_frame.setMinimumWidth(300)

        right_frame = QFrame()
        right_frame.setLayout(right_panel_layout)
        right_frame.setFrameShape(QFrame.StyledPanel)


        # --- Splitter for resizable panels ---
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        # --- Main Layout for Central Widget ---
        main_layout = QVBoxLayout()
        main_layout.addWidget(splitter)
        
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        # --- Menu Bar ---
        self._create_menu_bar()

        # --- Initial Data Load and Connections ---
        self.load_scripts()
        # Connect script_combo change to the method that handles button reset
        self.script_combo.currentIndexChanged.connect(self._on_settings_or_file_changed)
        self.script_combo.currentIndexChanged.connect(self._update_settings_panel_visibility)


        last_used_script = self.current_settings.get("last_used_script")
        if last_used_script and last_used_script in [self.script_combo.itemText(i) for i in range(self.script_combo.count())]:
            self.script_combo.setCurrentText(last_used_script)
            self._update_settings_panel_visibility() 
        elif self.script_combo.count() > 0:
            self._update_settings_panel_visibility()

        self._log_message("Welcome to PrintPath!", "info")
        self._log_message("Load a G-code file via 'File > Open G-code...' to begin processing.", "info")
        self._log_message("Adjust script settings above, and click 'Go!' to process.", "info")
        self._log_message("On some operating systems (e.g., macOS, some Linux), the menu bar may appear at the very top of your screen.", "info")


    def _log_message(self, message, msg_type="info"):
        """
        Logs messages to the console with color-coding, conditionally showing debug messages.
        This function now ONLY updates the GUI QTextEdit, as console output is handled by StreamRedirect.
        """
        if msg_type == "debug" and not self.current_settings.get("debug_mode", False):
            # Only return if it's a debug message AND debug mode is off
            return 
        
        cursor = self.log_console.textCursor()
        cursor.movePosition(QTextCursor.End)
        # Ensure message has a newline if it doesn't already, for proper display
        if not message.endswith('\n'):
            message += '\n'
        cursor.insertBlock() 
        cursor.insertText(message, self.log_formats.get(msg_type, self.log_formats["info"]))
        self.log_console.setTextCursor(cursor)
        self.log_console.ensureCursorVisible()

    def _clear_log_console(self):
        """
        Clears the text in the log console.
        """
        self.log_console.clear()
        self._log_message("Log cleared.", "info")


    def _create_menu_bar(self):
        """
        Creates the application's menu bar with File and Help options.
        """
        menu_bar = self.menuBar()
        if menu_bar is None:
            self._log_message("ERROR: self.menuBar() returned None. Menu bar cannot be created.", "error")
            return
        
        self._log_message("Menu bar object created successfully.", "debug")
        self._log_message("Menu bar setup initiated.", "debug")

        file_menu = menu_bar.addMenu("&File")

        open_action = QAction("&Open G-code...", self)
        open_action.setShortcut("Ctrl+O")
    
        open_action.setStatusTip("Open a G-code file for processing")
        open_action.triggered.connect(self.open_gcode_file)
        file_menu.addAction(open_action)

        self.save_action = QAction("&Save Processed G-code", self)
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.setStatusTip("Save the processed G-code to the original file's directory")
        self.save_action.triggered.connect(self.save_processed_gcode)
        self.save_action.setEnabled(False)
        file_menu.addAction(self.save_action)

        self.save_as_action = QAction("Save Processed G-code &As...", self)
        self.save_as_action.setShortcut("Ctrl+Shift+S")
        self.save_as_action.setStatusTip("Save the processed G-code to a specified file location")
        self.save_as_action.triggered.connect(self.save_processed_gcode_as)
        self.save_as_action.setEnabled(False)
        file_menu.addAction(self.save_as_action)

        file_menu.addSeparator()

        self.debug_mode_action = QAction("Debug Mode", self)
        self.debug_mode_action.setCheckable(True)
        self.debug_mode_action.setChecked(self.current_settings.get("debug_mode", False))
        self.debug_mode_action.setStatusTip("Toggle debug messages in the log console.")
        self.debug_mode_action.triggered.connect(self._toggle_debug_mode)
        file_menu.addAction(self.debug_mode_action)

        file_menu.addSeparator()

        exit_action = QAction("&Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.setStatusTip("Exit the application")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menu_bar.addMenu("&Help")
        about_action = QAction("&About PrintPath", self)
        about_action.setStatusTip("Show information about PrintPath")
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    def _show_about_dialog(self):
        """
        Displays the About PrintPath dialog.
        """
        about_text = (
            f"PrintPath v{APP_VERSION}\n\n"
            "A G-code post-processor for creating custom timelapse camera paths.\n\n"
            "Developed by Zxodiacx\n"
            "© 2024 Zxodiacx. All rights reserved."
        )
        QMessageBox.about(self, "About PrintPath", about_text)

    def _toggle_debug_mode(self):
        """
        Toggles the debug_mode setting and saves it.
        """
        self.current_settings["debug_mode"] = self.debug_mode_action.isChecked()
        save_settings(self.current_settings)
        # These messages should now consistently appear in both GUI and system console
        self._log_message(f"Debug Mode setting updated to: {self.current_settings['debug_mode']}", "info")
        self._log_message(f"Debug Mode {'ENABLED' if self.current_settings['debug_mode'] else 'DISABLED'}.", "info")


    def load_scripts(self):
        """
        Populates the script combo box with .py files found in SCRIPTS_DIR.
        Also parses global and script-specific settings defined in each script.
        """
        print(f"DEBUG: Entering load_scripts(). Scanning directory: {SCRIPTS_DIR}", file=sys.__stdout__)
        if not os.path.exists(SCRIPTS_DIR):
            os.makedirs(SCRIPTS_DIR)
            self._log_message(f"Created scripts directory: {SCRIPTS_DIR}", "info")
        
        self.script_combo.clear()
        all_global_setting_keys = list(self.global_default_settings.keys())
        if 'firmware' not in all_global_setting_keys:
            all_global_setting_keys.append('firmware')


        self.script_custom_settings_defs_map = {}

        found_scripts = False
        
        script_files = [f for f in os.listdir(SCRIPTS_DIR) if f.endswith(".py")]
        print(f"DEBUG: Found script files: {script_files}", file=sys.__stdout__)

        for filename in script_files:
            script_name = filename[:-3]
            self.script_combo.addItem(script_name)
            found_scripts = True
            print(f"DEBUG: Adding script '{script_name}' to combo box.", file=sys.__stdout__)

            script_path = os.path.join(SCRIPTS_DIR, filename)
            current_script_custom_defs = {}
            try:
                with open(script_path, "r") as f:
                    for line_num, line in enumerate(f): # Iterate through lines
                        stripped_line = line.strip()
                        if stripped_line.startswith("# SCRIPT_SETTINGS:"):
                            json_str = stripped_line[len("# SCRIPT_SETTINGS:"):].strip()
                            try:
                                current_script_custom_defs = json.loads(json_str)
                                self._log_message(f"Parsed SCRIPT_SETTINGS for '{script_name}': {current_script_custom_defs}", "debug")
                                print(f"DEBUG: Parsed SCRIPT_SETTINGS for '{script_name}': {current_script_custom_defs}", file=sys.__stdout__)
                            except json.JSONDecodeError as e:
                                self._log_message(f"Error parsing SCRIPT_SETTINGS JSON for '{script_name}' on line {line_num + 1}: {e}", "error")
                                print(f"ERROR: Error parsing SCRIPT_SETTINGS JSON for '{script_name}' on line {line_num + 1}: {e}", file=sys.__stdout__)
                                current_script_custom_defs = {} 
                            break # Found settings, stop scanning this file
                
                self.script_global_settings_map[script_name] = all_global_setting_keys
                self.script_custom_settings_defs_map[script_name] = current_script_custom_defs
                print(f"DEBUG: Populated script_custom_settings_defs_map for '{script_name}': {self.script_custom_settings_defs_map.get(script_name)}", file=sys.__stdout__)

                if script_name not in self.current_settings:
                    self.current_settings[script_name] = {}
                for setting_key, defs in current_script_custom_defs.items():
                    if setting_key not in self.current_settings[script_name]:
                        default_value = None
                        if defs.get("type") == "spinbox":
                            default_value = defs.get("default", defs.get("range", [0])[0]) 
                        elif defs.get("type") == "doublespinbox":
                            default_value = defs.get("default", defs.get("range", [0.0])[0])
                        elif defs.get("type") == "combobox":
                            default_value = defs.get("default", defs.get("items", [""])[0])
                        
                        self.current_settings[script_name][setting_key] = default_value
                        print(f"DEBUG: Initialized default value for '{script_name}.{setting_key}': {default_value}", file=sys.__stdout__)


            except Exception as e:
                self._log_message(f"Error loading script settings for '{script_name}': {e}", "error")
                print(f"ERROR: Error loading script settings for '{script_name}': {e}", file=sys.__stdout__)
                self.script_global_settings_map[script_name] = all_global_setting_keys
                self.script_custom_settings_defs_map[script_name] = {}


        if not found_scripts:
            self._log_message(f"No scripts found in '{SCRIPTS_DIR}'. Please add .py files.", "warning")
            print(f"WARNING: No scripts found in '{SCRIPTS_DIR}'.", file=sys.__stdout__)
        
        print(f"DEBUG: Exiting load_scripts(). Final script_custom_settings_defs_map: {self.script_custom_settings_defs_map}", file=sys.__stdout__)


    def _update_settings_panel_visibility(self):
        """
        Updates the visibility of global settings widgets and dynamically creates/destroys
        script-specific setting widgets based on the currently selected script.
        This also updates the range of 'num_snapshots' (or similar) if 'total_layers' is available.
        Crucially, it clamps the current value of QSpinBox widgets to their new valid range.
        """
        selected_script_name = self.script_combo.currentText()
        print(f"DEBUG: Entering _update_settings_panel_visibility() for script: '{selected_script_name}'", file=sys.__stdout__)

        if not selected_script_name:
            print("DEBUG: No script selected. Hiding all settings.", file=sys.__stdout__)
            for setting_key, (label_widget, input_widget) in self.global_setting_widgets.items():
                label_widget.setVisible(False)
                input_widget.setVisible(False)
            self._clear_dynamic_setting_widgets()
            return

        expected_global_settings = self.script_global_settings_map.get(selected_script_name, [])
        print(f"DEBUG: Expected global settings for '{selected_script_name}': {expected_global_settings}", file=sys.__stdout__)


        for setting_key, (label_widget, input_widget) in self.global_setting_widgets.items():
            is_visible = setting_key in expected_global_settings 
            label_widget.setVisible(is_visible)
            input_widget.setVisible(is_visible)
            input_widget.setEnabled(not self.progress_bar.isVisible())
            print(f"DEBUG: Global setting '{setting_key}': visible={is_visible}, enabled={input_widget.isEnabled()}", file=sys.__stdout__)


        self._clear_dynamic_setting_widgets()
        print("DEBUG: Cleared previous dynamic settings.", file=sys.__stdout__)

        self.script_specific_setting_widgets = {} 
        custom_setting_defs = self.script_custom_settings_defs_map.get(selected_script_name, {})
        print(f"DEBUG: Custom setting definitions for '{selected_script_name}': {custom_setting_defs}", file=sys.__stdout__)
        
        if selected_script_name not in self.current_settings:
            self.current_settings[selected_script_name] = {}
        script_current_settings = self.current_settings[selected_script_name]

        for setting_key, defs in custom_setting_defs.items():
            label_text = defs.get("label", setting_key.replace('_', ' ').title() + ":")
            label_widget = QLabel(label_text)
            input_widget = None
            
            setting_type = defs.get("type")
            value_from_settings = script_current_settings.get(setting_key)
            default_from_defs = defs.get("default")
            
            print(f"DEBUG: Processing dynamic setting '{setting_key}' (type: {setting_type})", file=sys.__stdout__)


            if setting_type == "spinbox":
                input_widget = QSpinBox()
                min_val, max_val_def = defs.get("range", [0, 100]) 
                
                # Special handling for settings that might depend on total_layers (like num_snapshots or num_orbits)
                if setting_key in ["num_snapshots", "num_orbits"] and self.gcode_info_full_data.get("total_layers") is not None:
                    max_val_actual = max(1, self.gcode_info_full_data["total_layers"])
                    max_val = min(max_val_actual, max(1, max_val_def)) 
                    self._log_message(f"Setting max for '{setting_key}' to {max_val} (derived from total layers {self.gcode_info_full_data['total_layers']}).", "debug")
                    print(f"DEBUG: Setting max for '{setting_key}' to {max_val} (derived from total layers {self.gcode_info_full_data['total_layers']}).", file=sys.__stdout__)
                else:
                    max_val = max_val_def 
                
                input_widget.setRange(min_val, max_val)
                
                effective_value = value_from_settings if value_from_settings is not None else (default_from_defs if default_from_defs is not None else min_val)
                clamped_value = min(max(min_val, effective_value), max_val)
                input_widget.setValue(clamped_value)

                input_widget.valueChanged.connect(lambda value, key=setting_key: self._update_script_specific_setting(selected_script_name, key, value))
                print(f"DEBUG: Spinbox '{setting_key}' set to value: {clamped_value} (Range: {min_val}-{max_val})", file=sys.__stdout__)

            elif setting_type == "doublespinbox":
                input_widget = QDoubleSpinBox()
                min_val, max_val = defs.get("range", [0.0, 10.0])
                input_widget.setRange(min_val, max_val)
                input_widget.setSingleStep(defs.get("step", 0.1))
                input_widget.setDecimals(defs.get("decimals", 2))
                input_widget.setValue(value_from_settings if value_from_settings is not None else (default_from_defs if default_from_defs is not None else min_val))
                input_widget.valueChanged.connect(lambda value, key=setting_key: self._update_script_specific_setting(selected_script_name, key, value))
                print(f"DEBUG: DoubleSpinbox '{setting_key}' set to value: {input_widget.value()} (Range: {min_val}-{max_val})", file=sys.__stdout__)

            elif setting_type == "combobox":
                input_widget = QComboBox()
                items = defs.get("items", [])
                input_widget.addItems(items)
                input_widget.setCurrentText(value_from_settings if value_from_settings is not None else (default_from_defs if default_from_defs is not None else (items[0] if items else "")))
                input_widget.currentTextChanged.connect(lambda text, key=setting_key: self._update_script_specific_setting(selected_script_name, key, text))
                print(f"DEBUG: Combobox '{setting_key}' set to text: '{input_widget.currentText()}' (Items: {items})", file=sys.__stdout__)

            if input_widget:
                input_widget.setToolTip(defs.get("tooltip", label_text.replace(":", "") + ".")) 
                self.settings_form_layout.addRow(label_widget, input_widget)
                self.script_specific_setting_widgets[setting_key] = (label_widget, input_widget)
                input_widget.setEnabled(not self.progress_bar.isVisible())
                print(f"DEBUG: Added dynamic widget for '{setting_key}'. Enabled: {input_widget.isEnabled()}", file=sys.__stdout__)


        self._log_message(f"Updated settings panel for script: '{selected_script_name}'.", "debug")
        self._log_message(f"Current Settings Object: {self.current_settings}", "debug")
        print(f"DEBUG: Exiting _update_settings_panel_visibility(). Current Settings: {self.current_settings}", file=sys.__stdout__)

        self.current_settings["last_used_script"] = selected_script_name
        save_settings(self.current_settings)


    def _clear_dynamic_setting_widgets(self):
        """
        Clears dynamic script-specific setting widgets from the settings form layout.
        This method is designed to avoid the "wrapped C/C++ object of type QLabel has been deleted"
        warning by properly removing items from the layout.
        """
        print(f"DEBUG: Clearing dynamic setting widgets for current script.", file=sys.__stdout__)

        # Iterate in reverse to safely remove items from the layout
        # QFormLayout has a specific way to remove rows by index or by widget.
        # We need to remove the rows and then explicitly delete the widgets.
        while self.settings_form_layout.count() > len(self.global_setting_widgets):
            # The dynamic widgets are added after the global ones.
            # Get the last row's item and remove it.
            # QFormLayout.takeAt(index) removes and returns the layout item at index.
            # QFormLayout.removeRow(index) removes the row but doesn't return the item.
            # We want to remove the specific widgets we added dynamically.
            
            found_and_removed = False
            for setting_key, (label_widget, input_widget) in list(self.script_specific_setting_widgets.items()):
                # Find the index of the row containing these widgets
                row_index = -1
                for i in range(self.settings_form_layout.rowCount()):
                    # Check if the label or field item in the row matches our widgets
                    if self.settings_form_layout.itemAt(i, QFormLayout.LabelRole) and self.settings_form_layout.itemAt(i, QFormLayout.LabelRole).widget() is label_widget:
                        row_index = i
                        break
                    if self.settings_form_layout.itemAt(i, QFormLayout.FieldRole) and self.settings_form_layout.itemAt(i, QFormLayout.FieldRole).widget() is input_widget:
                        row_index = i
                        break
                
                if row_index != -1:
                    # Removing the row also removes and implicitly deletes the widgets in that row.
                    # This avoids the "wrapped C/C++ object deleted" warning.
                    self.settings_form_layout.removeRow(row_index)
                    # No need to explicitly deleteLater() as removeRow handles it.
                    del self.script_specific_setting_widgets[setting_key]
                    found_and_removed = True
                    print(f"DEBUG: Removed dynamic widget row for '{setting_key}'.", file=sys.__stdout__)
                    break # Break and re-iterate the list(self.script_specific_setting_widgets.items())
                          # because the dictionary size changes during iteration.
            
            if not found_and_removed:
                # If we couldn't find a dynamic widget to remove, something is wrong,
                # or all dynamic widgets are already gone. Break to prevent infinite loop.
                print("DEBUG: No more dynamic widgets found to clear or an issue occurred during removal.", file=sys.__stdout__)
                break

        # After removing from layout, clear the Python dictionary of references
        self.script_specific_setting_widgets.clear()
        print("DEBUG: script_specific_setting_widgets cleared.", file=sys.__stdout__)
        # Ensure all widgets that were removed from the layout are indeed deleted later
        # Qt's memory management usually handles this automatically once the widgets are parentless
        # but to be absolutely sure, if they were *not* implicitly deleted by removeRow,
        # they would be memory leaked. The above change for removeRow should prevent the warning.


    def _go_button_clicked(self):
        """
        Handles the Go! button click. If in "Open Processed File" mode, opens the file.
        Otherwise, initiates G-code processing.
        """
        print(f"DEBUG: Go! button clicked. Current text: '{self.go_button.text()}'", file=sys.__stdout__)
        if self.go_button.text().startswith("Open "): 
            self._open_processed_file_with_default_app()
        else:
            # If the button says "Go!", it means we need to process the current G-code
            # with the current settings.
            self._process_current_gcode()


    def _process_current_gcode(self):
        """
        Processes the currently loaded G-code file with the current script and settings.
        This function is called by open_gcode_file and when the script selection changes.
        """
        if not self.original_gcode_filepath:
            self._log_message("No G-code file loaded to process. Please open a file first.", "warning")
            print("WARNING: No G-code file loaded to process.", file=sys.__stdout__)
            return

        mode = self.script_combo.currentText() if self.script_combo.currentText() else DEFAULT_MODE
        
        self._set_ui_processing_state(True)
        self._log_message(f"Processing '{os.path.basename(self.original_gcode_filepath)}' with '{mode}' script...", "info")
        print(f"DEBUG: Initiating G-code processing for '{os.path.basename(self.original_gcode_filepath)}' with '{mode}'.", file=sys.__stdout__)


        save_settings(self.current_settings)
        self._log_message("Current settings saved automatically.", "debug")

        combined_settings = {}
        for key in self.global_default_settings.keys():
            combined_settings[key] = self.current_settings.get(key, self.global_default_settings[key])

        script_specific_settings_for_mode = self.current_settings.get(mode, {})
        combined_settings.update(script_specific_settings_for_mode)
        
        combined_settings["debug_mode"] = self.current_settings.get("debug_mode", False)
        # Pass full gcode_info_full_data to scripts for comprehensive access
        combined_settings.update(self.gcode_info_full_data)


        self._log_message(f"Combined settings passed to GCodeProcessorThread for '{mode}': {combined_settings}", "debug")
        print(f"DEBUG: Combined settings sent to thread: {combined_settings}", file=sys.__stdout__)


        self.processor_thread = GCodeProcessorThread(self.original_gcode_filepath, mode, combined_settings)
        self.processor_thread.finished.connect(self._processing_finished)
        self.processor_thread.error.connect(self._processing_error)
        self.processor_thread.log_signal.connect(self._log_message)
        self.processor_thread.start()


    def open_gcode_file(self):
        """
        Opens a file dialog for the user to select a G-code file.
        If a file is selected, it sets the file path, detects G-code flavor,
        and starts a separate thread for parsing.
        """
        print("DEBUG: Open G-code file dialog initiated.", file=sys.__stdout__)
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open G-code File", self.last_used_directory, "G-code Files (*.gcode);;All Files (*)"
        )
        if filepath:
            print(f"DEBUG: File selected: {filepath}", file=sys.__stdout__)
            # Check if this file is already loaded
            if self.original_gcode_filepath == filepath:
                self._log_message(f"File '{os.path.basename(filepath)}' is already loaded. No re-parsing needed.", "info")
                print(f"INFO: File '{os.path.basename(filepath)}' already loaded. Refreshing viewer state.", file=sys.__stdout__)
                # Just ensure the preview is up-to-date in case it was cleared or something
                if self.gcode_bed_dimensions:
                    self.gcode_viewer.set_bed_dimensions(self.gcode_bed_dimensions['x'], self.gcode_bed_dimensions['y'])
                # Re-apply current view mode in case it changed
                self._update_view_mode(self.view_mode_combo.currentText()) 
                # Re-set data to trigger repaint with current view mode
                self.gcode_viewer.set_gcode_data(self.gcode_toolpath_data)
                self.gcode_viewer.set_layer_start_points(self.gcode_layer_start_points) # Pass layer start points
                self.gcode_viewer.set_processed_snapshot_points([]) # Clear processed snapshots if same file re-opened without re-processing
                self.go_button.setEnabled(True)
                return


            self.original_gcode_filepath = filepath
            
            self.last_used_directory = os.path.dirname(filepath)
            self.current_settings["last_used_directory"] = self.last_used_directory
            save_settings(self.current_settings) 

            filename_only = os.path.basename(filepath)
            if len(filename_only) > MAX_TITLE_FILENAME_LENGTH:
                filename_only = "..." + filename_only[-(MAX_TITLE_FILENAME_LENGTH - 3):]
            self.setWindowTitle(f"PrintPath - {filename_only}")
            print(f"DEBUG: Window title set to: PrintPath - {filename_only}", file=sys.__stdout__)

            # Clear processed snapshot points when a new file is opened
            self.gcode_viewer.set_processed_snapshot_points([])


            # Start parsing in a new thread
            self._set_ui_for_parsing_state(True) # Show progress bar, disable UI
            self.parse_thread = GCodeParseThread(self.original_gcode_filepath)
            self.parse_thread.finished.connect(self._parsing_finished)
            self.parse_thread.error.connect(self._parsing_error)
            self.parse_thread.log_signal.connect(self._log_message)
            self.parse_thread.start()

            self._log_message(f"Loading '{filename_only}' for preview...", "info")
            print(f"INFO: Starting parsing thread for '{filename_only}'.", file=sys.__stdout__)


    def _parsing_finished(self, gcode_info, toolpath_data, layer_start_points):
        """
        Slot connected to GCodeParseThread's finished signal.
        Updates UI with parsed G-code info and toolpath, and layer start points.
        """
        self._set_ui_for_parsing_state(False) # Hide progress bar, enable UI
        print("DEBUG: G-code parsing finished. Updating UI.", file=sys.__stdout__)

        self.gcode_info_full_data = gcode_info # Store full info
        
        # --- Update bed dimensions and max_z ---
        self.gcode_bed_dimensions = gcode_info.get("bed_dimensions")
        detected_max_z = gcode_info.get("max_z", 250.0) # Default to 250 if not detected
        
        if self.gcode_bed_dimensions:
            self._log_message(f"Detected bed dimensions: {self.gcode_bed_dimensions['x']:.1f}x{self.gcode_bed_dimensions['y']:.1f}mm, Max Z: {detected_max_z:.1f}mm", "debug")
            print(f"DEBUG: Detected bed dimensions: {self.gcode_bed_dimensions['x']:.1f}x{self.gcode_bed_dimensions['y']:.1f}mm, Max Z: {detected_max_z:.1f}mm", file=sys.__stdout__)
            # Ensure bed dimensions are valid numbers before passing to viewer
            bed_x = max(1.0, self.gcode_bed_dimensions.get('x', 220.0))
            bed_y = max(1.0, self.gcode_bed_dimensions.get('y', 220.0))
            self.gcode_viewer.set_bed_dimensions(bed_x, bed_y, detected_max_z)
        else:
            self._log_message("Bed dimensions not detected from file. Viewer will use default 220x220mm.", "warning")
            print("WARNING: Bed dimensions not detected from file. Using default 220x220mm.", file=sys.__stdout__)
            self.gcode_bed_dimensions = {"x": DEFAULT_BED_X, "y": DEFAULT_BED_Y} # Set fallback in main app as well
            self.gcode_viewer.set_bed_dimensions(DEFAULT_BED_X, DEFAULT_BED_Y, detected_max_z)

        # --- Update firmware flavor ---
        detected_flavor = gcode_info.get("gcode_flavor")
        if detected_flavor:
            self._log_message(f"Detected G-code flavor: {detected_flavor}", "debug")
            print(f"DEBUG: Detected G-code flavor: {detected_flavor}", file=sys.__stdout__)
            self.firmware_input.blockSignals(True)
            self.firmware_input.setCurrentText(detected_flavor)
            self.firmware_input.blockSignals(False)
            self._update_setting("firmware", detected_flavor) 
        else:
            self._log_message("G-code flavor not detected from file. Using current firmware setting.", "debug")
            print("DEBUG: G-code flavor not detected from file. Using current firmware setting.", file=sys.__stdout__)

        # --- Update toolpath data ---
        self.gcode_toolpath_data = toolpath_data # Now list of (QPointF(x,y), z_value)
        print(f"DEBUG (main.py): About to pass {len(self.gcode_toolpath_data)} points to GCodeViewer.set_gcode_data().", file=sys.__stdout__)
        if self.gcode_toolpath_data and len(self.gcode_toolpath_data) > 0:
            print(f"DEBUG (main.py): First point to viewer: ({self.gcode_toolpath_data[0][0].x():.1f}, {self.gcode_toolpath_data[0][0].y():.1f}, Z={self.gcode_toolpath_data[0][1]:.1f})", file=sys.__stdout__)
        self._log_message(f"Passing {len(self.gcode_toolpath_data)} points to GCodeViewer.set_gcode_data().", "debug")
        self.gcode_viewer.set_gcode_data(self.gcode_toolpath_data) # Pass the new (XY, Z) tuples
        
        # --- Update layer start points (for pre-processing preview) ---
        self.gcode_layer_start_points = layer_start_points
        self._log_message(f"Passing {len(self.gcode_layer_start_points)} layer start points to GCodeViewer.set_layer_start_points().", "debug")
        self.gcode_viewer.set_layer_start_points(self.gcode_layer_start_points)

        # Clear any processed snapshot points that might be lingering from a previous process
        self.gcode_viewer.set_processed_snapshot_points([])


        # Apply the currently selected view mode
        self._update_view_mode(self.view_mode_combo.currentText())

        # Update script-specific settings visibility and ranges (especially for num_snapshots)
        # This is important to call AFTER gcode_info_full_data is set
        self._update_settings_panel_visibility() 
        
        self.go_button.setEnabled(True) 
        self._log_message(f"File '{os.path.basename(self.original_gcode_filepath)}' loaded for preview. Click 'Go!' to process.", "info")
        print(f"INFO: File '{os.path.basename(self.original_gcode_filepath)}' loaded successfully for preview.", file=sys.__stdout__)


    def _parsing_error(self, message):
        """
        Slot connected to GCodeParseThread's error signal.
        Logs the error and re-enables UI.
        """
        self._set_ui_for_parsing_state(False)
        self.original_gcode_filepath = None
        self.gcode_toolpath_data = [] # Set to empty list on error
        self.gcode_layer_start_points = [] # Clear layer start points on error
        self.gcode_viewer.set_processed_snapshot_points([]) # Clear processed snapshot points on error
        self.gcode_info_full_data = {} # Clear info on error
        self.go_button.setEnabled(False)
        self._log_message(f"G-code parsing failed: {message}", "error")
        print(f"ERROR: G-code parsing failed: {message}", file=sys.__stdout__)
        self.setWindowTitle("PrintPath")
        self.gcode_viewer.set_gcode_data([]) # Clear previous toolpath and redraw
        self.gcode_viewer.set_layer_start_points([]) # Clear layer start points in viewer
        self.gcode_viewer.set_processed_snapshot_points([]) # Clear processed snapshot points in viewer
        self.gcode_viewer.set_bed_dimensions(DEFAULT_BED_X, DEFAULT_BED_Y, 250.0) # Reset bed dimensions to default

    def _set_ui_for_parsing_state(self, is_parsing):
        """
        Manages UI state specifically during G-code file parsing for preview.
        """
        print(f"DEBUG: Setting UI for parsing state: is_parsing={is_parsing}", file=sys.__stdout__)
        self.script_combo.setEnabled(not is_parsing)
        self.settings_group_box.setEnabled(not is_parsing)
        self.go_button.setEnabled(not is_parsing)
        self.clear_log_button.setEnabled(not is_parsing) 
        self.view_mode_combo.setEnabled(not is_parsing) # Disable view mode during parsing
        
        # Disable menu bar actions while parsing
        for action in self.menuBar().actions():
            if hasattr(action, 'menu') and action.menu(): 
                action.menu().setEnabled(not is_parsing)
            else: 
                if action != self.debug_mode_action: 
                    action.setEnabled(not is_parsing)

        if is_parsing:
            self.progress_bar.show()
        else:
            self.progress_bar.hide()
            # Ensure debug mode action is re-enabled
            if self.debug_mode_action:
                self.debug_mode_action.setEnabled(True)


    def _processing_finished(self, original_filepath, processed_content, mode):
        """
        Slot connected to the GCodeProcessorThread's finished signal.
        Updates the GUI with the processed content, saves it, and changes button behavior.
        Also triggers parsing of the processed content to update snapshot points in the viewer.
        """
        self._set_ui_processing_state(False)
        print(f"DEBUG: G-code processing finished for '{os.path.basename(original_filepath)}'.", file=sys.__stdout__)

        if processed_content:
            self.processed_gcode_content = processed_content
            self.save_action.setEnabled(True)
            self.save_as_action.setEnabled(True)
            self._log_message(f"G-code processing complete for '{os.path.basename(original_filepath)}'.", "info")
            
            snapshot_count = processed_content.count("TIMELAPSE_TAKE_FRAME")
            self._log_message(f"Detected {snapshot_count} TIMELAPSE_TAKE_FRAME commands in the processed G-code.", "info")
            print(f"INFO: Detected {snapshot_count} TIMELAPSE_TAKE_FRAME commands.", file=sys.__stdout__)

            # Auto-save the processed content
            self.output_filepath = self._auto_save_processed_gcode(original_filepath, mode)
            
            # After saving, parse the *saved* content to get snapshot points for the viewer
            if self.output_filepath:
                try:
                    with open(self.output_filepath, "r") as f:
                        processed_lines = f.readlines()
                    self.gcode_viewer.parse_and_set_processed_snapshot_points(processed_lines, self.current_settings.get("debug_mode", False))
                    self._log_message(f"Viewer updated with {len(self.gcode_viewer.processed_snapshot_points)} processed snapshot points.", "debug")
                except Exception as e:
                    self._log_message(f"Error parsing processed file for snapshots: {e}", "error")
                    print(f"ERROR: Error parsing processed file for snapshots: {e}", file=sys.__stdout__)


                self.go_button.setText(f"Open '{os.path.basename(self.output_filepath)}'")
                self.go_button.setEnabled(True)
                print(f"DEBUG: Auto-save successful. Go button text updated to 'Open {os.path.basename(self.output_filepath)}'.", file=sys.__stdout__)
            else:
                self._log_message("Automatic save failed, 'Open Processed File' button will not be available.", "warning")
                print("WARNING: Automatic save failed. Open button disabled.", file=sys.__stdout__)
                self.go_button.setText("Go!") 
                self.go_button.setEnabled(True) 
        else:
            self.processed_gcode_content = None
            self.save_action.setEnabled(False)
            self.save_as_action.setEnabled(False)
            self.gcode_viewer.set_processed_snapshot_points([]) # Clear on no content
            self._log_message(f"Processing of '{os.path.basename(original_filepath)}' completed with no content.", "warning")
            print(f"WARNING: Processing of '{os.path.basename(original_filepath)}' completed with no content.", file=sys.__stdout__)
            self.setWindowTitle("PrintPath")
            self.go_button.setText("Go!") 
            self.go_button.setEnabled(True) 


    def _processing_error(self, message):
        """
        Slot connected to the GCodeProcessorThread's error signal.
        Logs the error and re-enables UI.
        """
        self._set_ui_processing_state(False)
        self.processed_gcode_content = None
        self.save_action.setEnabled(False)
        self.save_as_action.setEnabled(False)
        self.gcode_viewer.set_processed_snapshot_points([]) # Clear on error
        self._log_message(f"Processing failed: {message}", "error")
        print(f"ERROR: Processing failed: {message}", file=sys.__stdout__)
        self.setWindowTitle("PrintPath")
        self.go_button.setText("Go!") 
        self.go_button.setEnabled(True) 


    def _set_ui_processing_state(self, is_processing):
        """
        Enables/disables relevant UI elements and shows/hides the progress bar
        based on whether processing is ongoing (for script processing).
        """
        print(f"DEBUG: Setting UI for processing state: is_processing={is_processing}", file=sys.__stdout__)
        # This function should only affect script processing state, not file loading/parsing.
        # It's called after _parsing_finished, so parsing UI elements are already managed.

        self.script_combo.setEnabled(not is_processing)
        self.settings_group_box.setEnabled(not is_processing)
        self.clear_log_button.setEnabled(not is_processing)
        self.view_mode_combo.setEnabled(not is_processing) # Enable/disable view mode during processing


        # Re-enable/disable based on overall processing state
        for action in self.menuBar().actions():
            if hasattr(action, 'menu') and action.menu(): 
                action.menu().setEnabled(not is_processing)
            else: 
                if action != self.debug_mode_action:
                    action.setEnabled(not is_processing) 

        if not is_processing and self.processed_gcode_content:
            self.save_action.setEnabled(True)
            self.save_as_action.setEnabled(True)
        else:
            self.save_action.setEnabled(False)
            self.save_as_action.setEnabled(False)


        # Specific widgets related to settings
        for setting_key, (label_widget, input_widget) in self.global_setting_widgets.items():
            is_visible_by_script = label_widget.isVisible() 
            input_widget.setEnabled(not is_processing and is_visible_by_script)
            print(f"DEBUG: Global setting widget '{setting_key}' enabled: {input_widget.isEnabled()} (visible by script: {is_visible_by_script})", file=sys.__stdout__)


        for setting_key, (label_widget, input_widget) in self.script_specific_setting_widgets.items():
            input_widget.setEnabled(not is_processing)
            print(f"DEBUG: Script specific setting widget '{setting_key}' enabled: {input_widget.isEnabled()}", file=sys.__stdout__)


        # Go button state logic is complex:
        # It's enabled if a file is loaded AND no processing is ongoing.
        # Its text changes after processing finishes successfully.
        if is_processing:
            self.go_button.setEnabled(False)
            self.progress_bar.show()
            print("DEBUG: Processing active. Go button disabled, progress bar shown.", file=sys.__stdout__)
        else:
            self.progress_bar.hide()
            # If a file is loaded, enable the go button
            self.go_button.setEnabled(self.original_gcode_filepath is not None)
            if self.debug_mode_action:
                self.debug_mode_action.setEnabled(True)
            print(f"DEBUG: Processing inactive. Go button enabled: {self.go_button.isEnabled()} (File loaded: {self.original_gcode_filepath is not None}). Progress bar hidden.", file=sys.__stdout__)

    
    def _on_settings_or_file_changed(self):
        """
        This method is now primarily for updating UI elements based on settings changes,
        and *not* for triggering G-code re-parsing.
        It primarily updates the "Go!" button text to indicate changes need to be applied.
        """
        print("DEBUG: _on_settings_or_file_changed triggered.", file=sys.__stdout__)
        if self.go_button.text().startswith("Open "):
            self._log_message("Settings or script changed. Resetting Go! button to 'Go!' (processing needed).", "debug")
            print("DEBUG: Go button text changed from 'Open...' to 'Go!'.", file=sys.__stdout__)
            self.go_button.setText("Go!")
            
        # Ensure the Go button is enabled if a file is loaded
        self.go_button.setEnabled(self.original_gcode_filepath is not None)
        print(f"DEBUG: Go button enabled: {self.go_button.isEnabled()} (File loaded: {self.original_gcode_filepath is not None}).", file=sys.__stdout__)
        
        # The GCodeViewer is *not* automatically redrawn here. It will redraw
        # when a new file is loaded, or when the "Go!" button is clicked.

    def _update_view_mode(self, selected_text):
        """
        Updates the GCodeViewer's view mode based on the selected text in the combo box.
        """
        print(f"DEBUG: View mode changed to: '{selected_text}'.", file=sys.__stdout__)
        mode = 'top' # Default
        if "Front View" in selected_text:
            mode = 'front'
        
        self.gcode_viewer.set_view_mode(mode)
        self.current_settings["preview_view_mode"] = selected_text
        save_settings(self.current_settings)
        self._log_message(f"Preview view mode set to: '{mode}'.", "debug")

        # Also re-draw the viewer with the current data in the new view mode
        if self.original_gcode_filepath and self.gcode_toolpath_data:
            print("DEBUG: Re-drawing GCodeViewer with current data due to view mode change.", file=sys.__stdout__)
            # Pass bed dimensions and max_z again to ensure viewer uses them for scaling
            detected_max_z = self.gcode_info_full_data.get("max_z", 250.0)
            self.gcode_viewer.set_bed_dimensions(self.gcode_bed_dimensions['x'], self.gcode_bed_dimensions['y'], detected_max_z)
            self.gcode_viewer.set_gcode_data(self.gcode_toolpath_data)
            self.gcode_viewer.set_layer_start_points(self.gcode_layer_start_points) # Pass layer start points
            # No longer passing processed_snapshot_points directly from here, it's parsed by viewer
        else:
            print("DEBUG: No G-code data to re-draw for viewer after view mode change.", file=sys.__stdout__)


    def _auto_save_processed_gcode(self, original_filepath, mode):
        """
        Automatically saves the currently processed G-code content to a derived filename
        in the same directory as the original file, without a file dialog.
        Returns the path of the saved file or None on failure.
        """
        print(f"DEBUG: Attempting auto-save for processed G-code. Original: {original_filepath}, Mode: {mode}", file=sys.__stdout__)
        if self.processed_gcode_content is None:
            self._log_message("No G-code has been processed yet to save automatically.", "debug")
            return None
        if not original_filepath:
            self._log_message("Cannot auto-save: Original G-code file path is unknown.", "debug")
            return None

        base_dir = os.path.dirname(original_filepath)
        base_name, ext = os.path.splitext(os.path.basename(original_filepath))
        mode_suffix = mode if mode else DEFAULT_MODE
        
        output_filename = f"{base_name}_{mode_suffix}{ext}"
        filepath = os.path.join(base_dir, output_filename)

        try:
            with open(filepath, "w") as f:
                f.write(self.processed_gcode_content)
            self._log_message(f"Processed G-code automatically saved to: {filepath}", "info")
            print(f"INFO: Processed G-code automatically saved to: {filepath}", file=sys.__stdout__)
            return filepath
        except Exception as e:
            self._log_message(f"Error auto-saving G-code to {filepath}: {e}", "error")
            print(f"ERROR: Error auto-saving G-code to {filepath}: {e}", file=sys.__stdout__)
            return None
        
    def save_processed_gcode(self):
        """
        Automatically saves the currently processed G-code content to a derived filename
        in the same directory as the original file, without a file dialog.
        """
        print("DEBUG: Save processed G-code triggered (auto-save variant).", file=sys.__stdout__)
        if self.processed_gcode_content is None:
            self._log_message("No G-code has been processed yet to save.", "warning")
            return
        if not self.original_gcode_filepath:
            self._log_message("Cannot save: Original G-code file path is unknown.", "warning")
            return

        saved_path = self._auto_save_processed_gcode(self.original_gcode_filepath, self.script_combo.currentText())
        if saved_path:
            self.save_action.setEnabled(False)
            self.save_as_action.setEnabled(False)
        
    def save_processed_gcode_as(self):
        """
        Saves the currently processed G-code content to a user-specified file location,
        suggesting a default filename based on the original file and current mode.
        """
        print("DEBUG: Save processed G-code As... dialog initiated.", file=sys.__stdout__)
        if self.processed_gcode_content is None:
            self._log_message("No G-code has been processed yet to save.", "warning")
            return

        default_filename = "processed_output.gcode"
        if self.original_gcode_filepath:
            base, ext = os.path.splitext(os.path.basename(self.original_gcode_filepath))
            mode_suffix = self.script_combo.currentText() if self.script_combo.currentText() else DEFAULT_MODE
            default_filename = f"{base}_{mode_suffix}{ext}"

        filepath, _ = QFileDialog.getSaveFileName(
            self, "Save Processed G-code As", self.last_used_directory, "G-code Files (*.gcode);;All Files (*)"
        )

        if filepath:
            try:
                with open(filepath, "w") as f:
                    f.write(self.processed_gcode_content)
                self._log_message(f"Processed G-code saved to: {filepath}", "info")
                print(f"INFO: Processed G-code saved to: {filepath}", file=sys.__stdout__)
                self.save_action.setEnabled(False)
                self.save_as_action.setEnabled(False)
            except Exception as e:
                self._log_message(f"Error saving G-code: {e}", "error")
                print(f"ERROR: Error saving G-code: {e}", file=sys.__stdout__)
        else:
            self._log_message("Save As operation cancelled.", "info")
            print("INFO: Save As operation cancelled.", file=sys.__stdout__)

    def _update_setting(self, key, value):
        """
        Updates a specific GLOBAL setting in the self.current_settings dictionary.
        Also saves settings automatically.
        """
        print(f"DEBUG: Updating global setting '{key}' to: {value}", file=sys.__stdout__)
        self.current_settings[key] = value
        self._log_message(f"Global Setting '{key}' updated to: {value}", "debug")
        save_settings(self.current_settings) 
        self._on_settings_or_file_changed() 


    def _update_script_specific_setting(self, script_name, key, value):
        """
        Updates a specific SCRIPT-SPECIFIC setting in the nested self.current_settings dictionary.
        Also saves settings automatically.
        """
        print(f"DEBUG: Updating script-specific setting '{script_name}.{key}' to: {value}", file=sys.__stdout__)
        if script_name not in self.current_settings:
            self.current_settings[script_name] = {}
        self.current_settings[script_name][key] = value
        self._log_message(f"Script '{script_name}' setting '{key}' updated to: {value}", "debug")
        save_settings(self.current_settings) 
        self._on_settings_or_file_changed() 

    def _open_processed_file_with_default_app(self):
        """
        Attempts to open the processed G-code file with the system's default application.
        """
        print(f"DEBUG: Attempting to open processed file: {self.output_filepath}", file=sys.__stdout__)
        if self.output_filepath and os.path.exists(self.output_filepath):
            try:
                if sys.platform.startswith('darwin'):    
                    os.system(f'open "{self.output_filepath}"')
                elif sys.platform.startswith('win'):     
                    os.startfile(self.output_filepath)
                else:                                    
                    os.system(f'xdg-open "{self.output_filepath}"')
                self._log_message(f"Opened '{os.path.basename(self.output_filepath)}' with default application.", "info")
                print(f"INFO: Opened '{os.path.basename(self.output_filepath)}' with default application.", file=sys.__stdout__)
            except Exception as e:
                self._log_message(f"Error opening processed file with default app: {e}", "error")
                print(f"ERROR: Error opening processed file with default app: {e}", file=sys.__stdout__)
        else:
            self._log_message("No processed file available to open.", "debug")
            print("DEBUG: No processed file available to open.", file=sys.__stdout__)


# --- Main Application Entry Point ---
if __name__ == "__main__":
    print("DEBUG: Application starting. Checking CLI arguments...", file=sys.__stdout__)

    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        if not os.path.exists(filepath) or not filepath.lower().endswith(".gcode"):
            print("Error: Please provide a valid .gcode file.")
            sys.exit(1)
        
        cli_settings = load_settings() 
        
        try:
            run_func = load_script(DEFAULT_MODE)
            with open(filepath, "r") as f:
                gcode_lines = f.readlines()
            
            # Use the globally captured ORIGINAL_STDOUT and ORIGINAL_STDERR for CLI redirection
            sys.stdout = StreamRedirect(lambda msg, type: ORIGINAL_STDOUT.write(f"[{type.upper()}] {msg}\n"), "debug", ORIGINAL_STDOUT, ORIGINAL_STDERR)

            # In CLI mode, we still need to run parsing logic to get info/toolpath
            # Since no GUI, we can call directly.
            temp_parse_thread = GCodeParseThread(filepath)
            # To get results from a QThread without a QApp event loop, we can just call run directly
            # This is not ideal for GUI apps, but acceptable for a CLI context when simulating.
            dummy_gcode_info = temp_parse_thread._parse_gcode_info_main_app(gcode_lines)
            dummy_toolpath_data, dummy_layer_start_points = temp_parse_thread._parse_gcode_toolpath(gcode_lines) # Now tuples

            # Pass the full dummy_gcode_info to cli_settings
            cli_settings.update(dummy_gcode_info)
            cli_settings["toolpath_data"] = dummy_toolpath_data # This one isn't used by scripts, just viewer
            cli_settings["layer_start_points"] = dummy_layer_start_points # Pass layer start points

            # CLI mode will not have a viewer to display processed snapshot points,
            # so we still call run_func expecting it to return (lines)
            new_lines = run_func(cli_settings, gcode_lines)
            
            base, ext = os.path.splitext(filepath)
            outpath = f"{base}_{DEFAULT_MODE}{ext}"
            with open(outpath, "w") as f:
                f.writelines(new_lines)
            print(f"Processed and saved: {filepath} -> {outpath}")
        except Exception as e:
            # Use ORIGINAL_STDERR for error messages in CLI mode
            ORIGINAL_STDERR.write(f"Failed to process file in CLI mode: {e}\n")
        finally:
            sys.stdout = ORIGINAL_STDOUT # Restore stdout
        sys.exit(0)
    else:
        app = QApplication(sys.argv)
        win = PrintPathApp()
        win.show()
        sys.exit(app.exec_())
