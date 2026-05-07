import ctypes
import difflib
import json
import logging
import os
import re
import sys
import threading
import tkinter as tk
from collections import defaultdict
from datetime import datetime
from tkinter import ttk
from requests import get
import cv2
import keyboard
import numpy as np
import pyautogui
import pydirectinput
import pygetwindow
import pytesseract
import webbrowser
import win32gui
import win32ui
from PIL import Image

nice_names = {
    31: "Increases max HP",
    32: "ATK+",
    33: "DEF+",
    34: "Increases SPD",
    35: "Increases critical rate",
    36: "Increases critical DMG",
    37: "Increases break effect",
    38: "Increases HP recovery amount",
    39: "Increases debuff hit rate",
    40: "Increases debuff RES",
}
NAME_PREFIXES = {"Increases", "ATK", "DEF", "Max"}
crys_options = defaultdict(list)
all_possible = set()

SETTINGS_FILE = "settings.json"
__version__ = "vDev"
possible_chars = set()


def resource_path(relative_path):
    """Get absolute path to resource."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


with open(resource_path("getSelectionAbilityMstList.json"), "r", encoding="utf-8") as f:
    crys = json.load(f)["payload"]["mstList"]
    for c in crys:
        if c["selectionAbilityType"] == 2:
            crys_options[nice_names[c["selectionAbilityEffectId"]]].append(
                c["description"]
            )
            all_possible.add(c["description"])
            possible_chars.update(c["description"])

possible_chars.remove(" ")
TARGET_WINDOW = "MadokaExedra"
TESS_CONFIG = (
    "--oem 3 --psm 6 " + "-c tessedit_char_whitelist=" + "".join(sorted(possible_chars))
)
_normalised_to_canonical: dict[str, str] = {
    re.sub(r"\s+", "", s).lower(): s for s in all_possible
}
_normalised_to_canonical["increaseshprecoveryamountby."] = (
    "Increases HP recovery amount by 8%."
)

pydirectinput.FAILSAFE = False
keyboard.add_hotkey("ctrl+shift+q", lambda: os._exit(0))

log_formatter = logging.Formatter("%(asctime)s - %(message)s", "%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("crys_reroller")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


def get_game_window():
    wins = pygetwindow.getWindowsWithTitle(TARGET_WINDOW)
    if not wins:
        raise RuntimeError("Game window not found")
    return wins[0]


def _capture_window(hwnd: int) -> Image.Image:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w = right - left
    h = bottom - top

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)

    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0x2)

    bmp_info = bmp.GetInfo()
    raw = bmp.GetBitmapBits(True)
    img = Image.frombuffer(
        "RGB",
        (bmp_info["bmWidth"], bmp_info["bmHeight"]),
        raw,
        "raw",
        "BGRX",
        0,
        1,
    )

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    return img


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _strip_noise_prefix(text: str) -> str:
    tokens = text.split()
    for i, token in enumerate(tokens):
        if any(token.startswith(p) for p in NAME_PREFIXES):
            return " ".join(tokens[i:])
    return text


def _ocr_full_window(img_colour: Image.Image, debug_log: bool) -> list[str]:
    arr = np.array(img_colour)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    results = []
    for variant_img, name in ((arr, "colour"), (gray, "gray"), (bw, "bw")):
        os.makedirs("debug", exist_ok=True)
        if debug_log:
            Image.fromarray(variant_img).save(f"debug/{name}.png")
        try:
            data = pytesseract.image_to_data(
                variant_img,
                output_type=pytesseract.Output.DICT,
                config=TESS_CONFIG,
            )
            raw = re.sub(r" +", " ", " ".join(data["text"])).strip()
            results.append(raw)
        except pytesseract.TesseractNotFoundError as e:
            input(
                "Tesseract is not in path! Download it and restart your PC and try again..."
            )
            raise e
    return results


def _find_abilities_in_text(ocr_text: str) -> list[str | None]:
    norm_text = _normalize(ocr_text)
    found: list[str] = []

    for norm_ability, canonical in _normalised_to_canonical.items():
        if norm_ability in norm_text and canonical not in found:
            found.append(canonical)
        if len(found) >= 3:
            return found[:3]

    for line in ocr_text.splitlines():
        logger.debug("Checking line for matches: '%s'", line)
        line = _strip_noise_prefix(line.strip())
        if not line:
            continue
        matches = difflib.get_close_matches(line, all_possible, n=1, cutoff=0.65)
        if matches and matches[0] not in found:
            found.append(matches[0])
        if len(found) >= 3:
            break

    while len(found) < 3:
        found.append(None)
    return found[:3]


def fetch_current_crys_values(win, debug_log: bool) -> list[str | None]:
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    if not hwnd:
        logger.error("Game window handle not found")
        return [None, None, None]

    img = _capture_window(hwnd)
    crop = img.crop(
        (
            0.25 * win.width,
            0.28 * win.height,
            0.48 * win.width,
            0.5 * win.height,
        )
    )
    variants = _ocr_full_window(crop, debug_log)
    variant_names = ("colour", "gray", "bw")

    best_result = [None, None, None]
    best_count = -1

    for name, text in zip(variant_names, variants):
        logger.debug("[%s] OCR text:\n%s", name, text)
        result = _find_abilities_in_text(text)
        hits = sum(1 for r in result if r is not None)
        logger.debug("[%s] parsed → %s (%d hits)", name, result, hits)
        if hits > best_count:
            best_count = hits
            best_result = result

    return best_result


def click(x: float | int, y: float | int):
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    if not hwnd:
        return
    prev_hwnd = win32gui.GetForegroundWindow()
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    pyautogui.sleep(0.05)
    curr = pyautogui.position()
    pydirectinput.click(int(x), int(y))
    pyautogui.moveTo(curr)
    pyautogui.sleep(0.05)
    ctypes.windll.user32.SetForegroundWindow(prev_hwnd)


def click_reroll_button(win) -> None:
    click(win.left + 0.6 * win.width, win.top + 0.85 * win.height)


def reroll(
    win,
    targets: list[str],
    match_mode: str,
    required_count: int,
    stop_flag: threading.Event,
    roll_log_path: str | None,
    debug_log: bool = False,
):
    target_set = set(targets)
    roll_number = 0

    logger.info("Starting reroll | mode=%s | targets: %s", match_mode, list(target_set))
    logger.info("Press Ctrl+Shift+Q to force-quit at any time.")

    while not stop_flag.is_set():
        pyautogui.sleep(0.3)

        if stop_flag.is_set():
            break

        current_values = fetch_current_crys_values(win, debug_log)

        if None in current_values:
            if current_values.count(None) >= 2:
                # Not on the right screen
                click_reroll_button(win)
            logger.warning(
                "Roll #%d — could not read all substats (%s), retrying…",
                roll_number,
                current_values,
            )
            continue

        found_targets = [v for v in current_values if v in target_set]

        if match_mode == "AND":
            success = len(found_targets) >= required_count
        else:
            success = len(found_targets) >= 1

        logger.info(
            "Roll #%d: %s | %s",
            roll_number,
            current_values,
            "HIT" if success else "miss",
        )

        if roll_log_path:
            entry = {
                "roll": roll_number,
                "time": datetime.now().isoformat(timespec="seconds"),
                "s1": current_values[0],
                "s2": current_values[1],
                "s3": current_values[2],
            }
            with open(roll_log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")

        if success:
            logger.info("Target reached after %d rolls — stopping.", roll_number)
            return

        roll_number += 1
        logger.info("Roll #%d — clicking reroll…", roll_number)
        click_reroll_button(win)

    logger.info("Reroll stopped by user after %d rolls.", roll_number)


def check_git_version_match():
    try:
        git_version = get(
            "https://api.github.com/repos/thefrozenfishy/exedra-crys-reroller/releases/latest",
            timeout=10,
        )
        if git_version.status_code == 200:
            data = git_version.json()
            version = data["tag_name"].lstrip("version-")
            if f"v{version}" != __version__:
                return version
                logger.warning(
                    "New version available: v%s, you are on %s", version, __version__
                )
    except Exception as e:
        logger.error("Failed to get git version")
    return False


def main():
    win = get_game_window()
    try:
        win.activate()
    except Exception as e:
        logger.warning(
            "Could not activate window — make sure Exedra is visible. (%s)", e
        )

    stop_flag = threading.Event()
    reroll_thread: threading.Thread | None = None

    settings = load_settings()

    root = tk.Tk()
    root.title("Exedra Auto Reroller")
    root.geometry("340x500+50+50")
    root.resizable(False, False)

    dropdown_options = [""] + list(crys_options.keys())  # "" = clear/empty option
    dropdown_vars = []
    min_level_vars = []
    min_level_boxes = []
    check_vars = []

    saved_targets = settings.get("targets", [{}, {}, {}])

    def persist_settings(*_):
        """Write current GUI state to settings.json."""
        data = {
            "match_mode": match_mode_var.get(),
            "should_log": should_log_var.get(),
            "debug_log": debug_log_var.get(),
            "targets": [
                {
                    "enabled": check_vars[i].get(),
                    "category": dropdown_vars[i].get(),
                    "min_value": min_level_vars[i].get(),
                }
                for i in range(3)
            ],
        }
        save_settings(data)

    def update_min_level(index, *_):
        main_value = dropdown_vars[index].get()
        if main_value and main_value in crys_options:
            new_values = crys_options[main_value]
            min_level_boxes[index]["values"] = [""] + new_values
            min_level_boxes[index].set(new_values[-1])
        else:
            min_level_boxes[index]["values"] = [""]
            min_level_boxes[index].set("")
        persist_settings()

    for i in range(3):
        row_frame = ttk.Frame(root)
        row_frame.pack(fill="x", padx=10, pady=(6, 0))

        saved = saved_targets[i] if i < len(saved_targets) else {}
        check_var = tk.BooleanVar(value=saved.get("enabled", True))
        check_vars.append(check_var)
        ttk.Checkbutton(row_frame, variable=check_var, command=persist_settings).pack(
            side="left"
        )

        ttk.Label(row_frame, text=f"Target {i+1}").pack(side="left", padx=(2, 6))

        var = tk.StringVar(value="")
        dropdown_vars.append(var)
        box = ttk.Combobox(
            row_frame,
            textvariable=var,
            values=dropdown_options,
            state="readonly",
            width=28,
        )
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda e, idx=i: update_min_level(idx))

        ttk.Label(root, text="  Minimum value").pack(anchor="w", padx=30)
        min_var = tk.StringVar(value="")
        min_level_vars.append(min_var)
        min_box = ttk.Combobox(
            root,
            textvariable=min_var,
            values=[""],
            state="readonly",
            width=32,
        )
        min_box.pack(anchor="w", padx=(42, 10), pady=(0, 2))
        min_box.bind("<<ComboboxSelected>>", persist_settings)
        min_level_boxes.append(min_box)

    # Restore saved selections now that all widgets exist
    for i in range(3):
        saved = saved_targets[i] if i < len(saved_targets) else {}
        category = saved.get("category", "")
        min_value = saved.get("min_value", "")
        if category and category in crys_options:
            dropdown_vars[i].set(category)
            options = crys_options[category]
            min_level_boxes[i]["values"] = [""] + options
            min_level_vars[i].set(min_value if min_value in options else "")

    ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10, pady=8)

    match_frame = ttk.Frame(root)
    match_frame.pack(anchor="w", padx=14)
    ttk.Label(match_frame, text="Match mode:").pack(side="left", padx=(0, 8))
    match_mode_var = tk.StringVar(value=settings.get("match_mode", "OR"))
    ttk.Radiobutton(
        match_frame,
        text="OR (any target)",
        variable=match_mode_var,
        value="OR",
        command=persist_settings,
    ).pack(side="left", padx=4)
    ttk.Radiobutton(
        match_frame,
        text="AND (all targets)",
        variable=match_mode_var,
        value="AND",
        command=persist_settings,
    ).pack(side="left", padx=4)

    ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10, pady=8)

    should_log_var = tk.BooleanVar(value=settings.get("should_log", True))
    ttk.Checkbutton(
        root,
        text="Save roll log (JSONL)",
        variable=should_log_var,
        command=persist_settings,
    ).pack(anchor="w", padx=14)

    debug_log_var = tk.BooleanVar(value=settings.get("debug_log", False))
    ttk.Checkbutton(
        root,
        text="Verbose debug logging",
        variable=debug_log_var,
        command=persist_settings,
    ).pack(anchor="w", padx=14)

    ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10, pady=8)

    def start_reroll():
        nonlocal reroll_thread
        if reroll_thread and reroll_thread.is_alive():
            logger.warning("Already running — press Stop first.")
            return

        persist_settings()
        logger.setLevel(logging.DEBUG if debug_log_var.get() else logging.INFO)

        roll_log_path: str | None = None
        if should_log_var.get():
            os.makedirs("reroll_logs", exist_ok=True)
            roll_log_path = (
                f"reroll_logs/{datetime.today().strftime('%Y-%m-%dT%H-%M-%S')}.jsonl"
            )
            fh = logging.FileHandler(
                roll_log_path.replace(".jsonl", "_verbose.txt"), encoding="utf-8"
            )
            fh.setFormatter(log_formatter)
            logger.addHandler(fh)
            logger.info("Logging rolls to %s", roll_log_path)

        targets: list[str] = []
        for i in range(3):
            if not check_vars[i].get():
                continue
            category = dropdown_vars[i].get()
            min_val = min_level_vars[i].get()
            if not category or not min_val:
                continue
            options = crys_options.get(category, [])
            try:
                idx = options.index(min_val)
                targets += options[idx:]
            except ValueError:
                pass

        if not targets:
            logger.warning("No valid targets selected — nothing to reroll for.")
            return

        match_mode = "AND" if match_mode_var.get() == "AND" else "OR"
        required_count = len(targets)

        stop_flag.clear()
        reroll_thread = threading.Thread(
            target=reroll,
            args=(
                win,
                targets,
                match_mode,
                required_count,
                stop_flag,
                roll_log_path,
                debug_log_var.get(),
            ),
            daemon=True,
        )
        reroll_thread.start()

    def stop_reroll():
        stop_flag.set()
        logger.info("Stop requested — will halt after current roll.")

    btn_frame = ttk.Frame(root)
    btn_frame.pack(pady=4)
    ttk.Button(btn_frame, text="Start Reroll", command=start_reroll).pack(
        side="left", padx=6
    )
    ttk.Button(btn_frame, text="Stop", command=stop_reroll).pack(side="left", padx=6)

    keyboard.add_hotkey("ctrl+shift+e", stop_reroll)

    ttk.Label(
        root,
        text="Ctrl+Shift+Q = force quit at any time",
        foreground="gray",
        font=("TkDefaultFont", 8),
    ).pack(pady=(8, 0))
    ttk.Label(
        root,
        text="Ctrl+Shift+E = stop reroll",
        foreground="gray",
        font=("TkDefaultFont", 8),
    ).pack(pady=(2, 0))
    ttk.Label(
        root,
        text=f"Current version: {__version__}",
        foreground="black",
        font=("TkDefaultFont", 10),
    ).pack(pady=(8, 0))
    if new_version := check_git_version_match():
        ttk.Label(
            root,
            text=f"Version {new_version} available",
            foreground="black",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(pady=(4, 0))
        button = ttk.Button(
            root,
            text="Download Latest Version",
            command=lambda: webbrowser.open(
                "https://github.com/thefrozenfishy/exedra-crys-reroller/releases"
            ),
        )
        button.pack()

    root.mainloop()


if __name__ == "__main__":
    main()
