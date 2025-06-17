import sys
import os
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QTextEdit, QLabel, QListWidget,
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QFrame
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt

SCRIPTS_DIR = "scripts"
DEFAULT_MODE = "orbit"

def load_script(mode):
    script_path = os.path.join(SCRIPTS_DIR, f"{mode}.py")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"Script '{mode}' not found.")
    scope = {}
    with open(script_path, "r") as f:
        exec(f.read(), scope)
    return scope["run"]

def process_gcode_file(filepath, mode=DEFAULT_MODE):
    with open(filepath, "r") as f:
        gcode_lines = f.readlines()
    run_func = load_script(mode)
    new_lines = run_func({}, gcode_lines)
    base, ext = os.path.splitext(filepath)
    outpath = f"{base}_{mode}{ext}"
    with open(outpath, "w") as f:
        f.writelines(new_lines)
    print(f"Processed: {filepath} -> {outpath}")

class PrintPathApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PrintPath")
        self.setWindowIcon(QIcon("icon.png"))
        self.setGeometry(100, 100, 1000, 700)

        self.script_list = QListWidget()
        self.metadata_display = QLabel("Select a script to view metadata.")
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)

        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("Available Scripts:"))
        left_panel.addWidget(self.script_list)

        right_panel = QVBoxLayout()
        right_panel.addWidget(QLabel("Script Metadata:"))
        right_panel.addWidget(self.metadata_display)
        right_panel.addWidget(QLabel("Log Console:"))
        right_panel.addWidget(self.log_console)

        left_frame = QFrame()
        left_frame.setLayout(left_panel)
        left_frame.setFrameShape(QFrame.StyledPanel)

        right_frame = QFrame()
        right_frame.setLayout(right_panel)
        right_frame.setFrameShape(QFrame.StyledPanel)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_frame)
        splitter.addWidget(right_frame)

        container = QWidget()
        layout = QVBoxLayout()
        layout.addWidget(splitter)
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.load_scripts()
        self.script_list.currentItemChanged.connect(self.update_metadata)

    def load_scripts(self):
        if not os.path.exists(SCRIPTS_DIR):
            os.makedirs(SCRIPTS_DIR)
        for filename in os.listdir(SCRIPTS_DIR):
            if filename.endswith(".py"):
                self.script_list.addItem(filename[:-3])

    def update_metadata(self):
        selected = self.script_list.currentItem()
        if not selected:
            self.metadata_display.setText("No script selected.")
            return
        path = os.path.join(SCRIPTS_DIR, selected.text() + ".py")
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            metadata = [line.strip() for line in lines if line.startswith("#")]
            self.metadata_display.setText("\n".join(metadata))
            self.log_console.append(f"Loaded metadata for {selected.text()}")
        except Exception as e:
            self.log_console.append(f"Error loading metadata: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        if not os.path.exists(filepath) or not filepath.lower().endswith(".gcode"):
            print("Error: Please provide a valid .gcode file.")
            sys.exit(1)
        try:
            process_gcode_file(filepath)
        except Exception as e:
            print(f"Failed to process file: {e}")
        sys.exit(0)
    else:
        app = QApplication(sys.argv)
        win = PrintPathApp()
        win.show()
        sys.exit(app.exec_())
