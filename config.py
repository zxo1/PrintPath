import json
import os

SETTINGS_FILE = "settings.json"

# Define default global settings
DEFAULT_SETTINGS = {
    "firmware": "klipper",
    "travel_speed": 9000, # Renamed from orbit_speed
    "dwell_time": 500,
    "retract_length": 0.5,
    "retract_speed": 40,
    "z_hop_height": 0.2
}

def load_settings():
    """
    Loads settings from settings.json. If the file doesn't exist,
    returns default settings. Merges new default settings if available.
    """
    settings = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                settings = json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Could not decode {SETTINGS_FILE}. Using default settings.")
            settings = {} # Reset to empty dict if file is corrupt
    
    # Merge with default settings to ensure all keys are present
    # This also handles new default settings being added in future versions
    # and ensures that only global settings are picked from DEFAULT_SETTINGS
    
    # Start with global defaults
    merged_settings = DEFAULT_SETTINGS.copy()
    
    # Overwrite with saved settings for global keys
    for key, value in settings.items():
        if key in DEFAULT_SETTINGS:
            merged_settings[key] = value
        else: # Keep script-specific settings as nested dicts
            merged_settings[key] = value # This will carry over the nested script settings

    # Ensure nested script-specific settings are also merged, not just overwritten
    # Iterate through potentially saved script settings and merge them
    for script_name, script_settings in settings.items():
        if script_name not in DEFAULT_SETTINGS and isinstance(script_settings, dict):
            if script_name not in merged_settings:
                merged_settings[script_name] = {}
            # Merge individual script settings (don't overwrite the whole dict)
            for setting_key, setting_value in script_settings.items():
                merged_settings[script_name][setting_key] = setting_value

    return merged_settings


def save_settings(settings):
    """
    Saves the current settings to settings.json.
    """
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=4)
    except Exception as e:
        print(f"Error saving settings to {SETTINGS_FILE}: {e}")

if __name__ == "__main__":
    # Example usage:
    # On first run or if settings.json is missing/corrupt, it will load defaults.
    # Subsequent runs will load saved settings.
    current_app_settings = load_settings()
    print("Loaded settings:", current_app_settings)

    # Example of updating a global setting
    current_app_settings["travel_speed"] = 10000
    print("Updated travel_speed to:", current_app_settings["travel_speed"])

    # Example of updating a script-specific setting (assuming 'arc' script exists)
    # If 'arc' doesn't exist yet in current_app_settings, it will be created.
    if "arc" not in current_app_settings:
        current_app_settings["arc"] = {}
    current_app_settings["arc"]["num_snapshots"] = 15
    current_app_settings["arc"]["arc_start_layer"] = 5
    print("Updated arc script settings:", current_app_settings.get("arc"))


    save_settings(current_app_settings)
    print("Settings saved.")

    # Load again to verify
    reloaded_settings = load_settings()
    print("Reloaded settings:", reloaded_settings)
