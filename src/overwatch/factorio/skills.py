from __future__ import annotations

"""
Named menu / UI skills as key sequences (Factorio defaults; user rebinding breaks this).

Values are lists of keys understood by :mod:`pyautogui` if installed (e.g. ``escape``, ``e``, ``t``).
See https://pyautogui.readthedocs.io/en/latest/keyboard.html#keyboard-keys
"""

# skill_name -> sequence of pyautogui key strings
SKILL_KEY_SEQUENCES: dict[str, list[str]] = {
    "close_menu": ["escape"],
    "toggle_pause": ["space"],
    "open_research": ["t"],
    "open_production_stats": ["p"],
    "open_blueprint_menu": ["b"],
    "open_map": ["m"],
    "rotate": ["r"],
    "pipette": ["q"],
}


def list_skills() -> list[str]:
    return sorted(SKILL_KEY_SEQUENCES.keys())
