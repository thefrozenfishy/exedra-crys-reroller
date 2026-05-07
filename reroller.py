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

import cv2
import keyboard
import numpy as np
import pyautogui
import pydirectinput
import pygetwindow
import pytesseract
import win32gui
import win32ui
from PIL import Image
import ctypes

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


def resource_path(relative_path):
    """Get absolute path to resource."""
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller temp folder
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

_normalised_to_canonical: dict[str, str] = {
    re.sub(r"\s+", "", s).lower(): s for s in all_possible
}

pydirectinput.FAILSAFE = False
keyboard.add_hotkey("ctrl+shift+q", lambda: os._exit(0))

log_formatter = logging.Formatter("%(asctime)s - %(message)s", "%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("crys_reroller")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

TARGET_WINDOW = "MadokaExedra"

SLEEP_DUR = 2


def get_game_window():
    wins = pygetwindow.getWindowsWithTitle(TARGET_WINDOW)
    if not wins:
        raise RuntimeError("Game window not found")
    return wins[0]


def _capture_window(hwnd: int) -> Image.Image:
    """
    Capture a window using PrintWindow with PW_RENDERFULLCONTENT (0x2).
    This flag is what makes it work for DirectX / GPU-rendered games where
    ImageGrab.grab() returns a black rectangle.
    """
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w = right - left
    h = bottom - top

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)

    # PW_RENDERFULLCONTENT = 0x2  →  captures GPU-rendered content
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


def _ocr_full_window(img_colour: Image.Image) -> list[str]:
    """
    Run Tesseract on colour, grayscale, and Otsu-binarised variants of the
    full captured window image.
    """
    arr = np.array(img_colour)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    results = []
    for variant_img in (arr, gray, bw):
        try:
            data = pytesseract.image_to_data(
                variant_img, output_type=pytesseract.Output.DICT
            )
            raw = re.sub(r" +", " ", " ".join(data["text"])).strip()
            results.append(raw)
        except pytesseract.TesseractNotFoundError as e:
            input(
                "Tesseract is not in path! "
                "Download it and restart your PC and try again..."
            )
            raise e
    return results


def _find_abilities_in_text(ocr_text: str) -> list[str | None]:
    """
    Search a full-window OCR blob for up to 3 canonical ability strings.
    Pass 1 — exact: normalise the whole blob and check each known ability.
    Pass 2 — fuzzy: line-by-line difflib fallback.
    """
    norm_text = _normalize(ocr_text)
    found: list[str] = []

    # Exact pass
    for norm_ability, canonical in _normalised_to_canonical.items():
        if norm_ability in norm_text and canonical not in found:
            found.append(canonical)
        if len(found) >= 3:
            return found[:3]

    # Fuzzy pass on individual lines
    for line in ocr_text.splitlines():
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


def fetch_current_crys_values(win) -> list[str | None]:
    """
    Capture the full game window via PrintWindow (GPU-safe), OCR it across
    three image variants, and return the best-matched set of 3 substats.
    """
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    if not hwnd:
        logger.error("Game window handle not found")
        return [None, None, None]

    img = _capture_window(hwnd)

    variants = _ocr_full_window(img)
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
    pyautogui.sleep(0.05)  # let it actually activate
    curr = pyautogui.position()
    pydirectinput.click(int(x), int(y))
    pyautogui.moveTo(curr)
    pyautogui.sleep(0.05)
    ctypes.windll.user32.SetForegroundWindow(prev_hwnd)


def click_reroll_button(win) -> None:
    click(win.left + 0.6 * win.width, win.top + 0.85 * win.height)


def reroll(
    win,
    targets: list[str],  # flat list of canonical ability strings to look for
    match_mode: str,  # "AND" or "OR"
    required_count: int,  # for AND: all enabled targets; for OR: at least 1
    stop_flag: threading.Event,
    roll_log_path: str | None,
):
    """
    AND mode — every enabled target must appear in the three substat slots.
    OR  mode — at least one enabled target must appear.

    Each roll result is appended to roll_log_path (one JSON line per roll).
    """
    target_set = set(targets)
    roll_number = 0

    logger.info(
        "Starting reroll | mode=%s | targets: %s",
        match_mode,
        list(target_set),
    )
    logger.info("Press Ctrl+Shift+Q to force-quit at any time.")

    while not stop_flag.is_set():
        roll_number += 1
        logger.info("Roll #%d — clicking reroll…", roll_number)
        click_reroll_button(win)
        pyautogui.sleep(SLEEP_DUR)

        if stop_flag.is_set():
            break

        current_values = fetch_current_crys_values(win)

        if None in current_values:
            logger.warning(
                "Roll #%d — could not read all substats (%s), retrying…",
                roll_number,
                current_values,
            )
            pyautogui.sleep(SLEEP_DUR)
            continue

        found_targets = [v for v in current_values if v in target_set]

        if match_mode == "AND":
            success = len(found_targets) >= required_count
        else:  # OR
            success = len(found_targets) >= 1

        logger.info(
            "Roll #%d: %s | %s",
            roll_number,
            current_values,
            "HIT" if success else "miss",
        )

        # --- Append to roll log file ---
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
            logger.info("✓ Target reached after %d rolls — stopping.", roll_number)
            return

        pyautogui.sleep(SLEEP_DUR)

    logger.info("Reroll stopped by user after %d rolls.", roll_number)


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

    def start_reroll():
        nonlocal reroll_thread
        if reroll_thread and reroll_thread.is_alive():
            logger.warning("Already running — press Stop first.")
            return

        # Logging level
        logger.setLevel(logging.DEBUG if debug_log_var.get() else logging.INFO)

        # Build roll-log file path (one file per session)
        roll_log_path: str | None = None
        if should_log_var.get():
            os.makedirs("reroll_logs", exist_ok=True)
            roll_log_path = (
                f"reroll_logs/{datetime.today().strftime('%Y-%m-%dT%H-%M-%S')}.jsonl"
            )
            # Also attach a text handler for the verbose logger
            fh = logging.FileHandler(
                roll_log_path.replace(".jsonl", "_verbose.txt"), encoding="utf-8"
            )
            fh.setFormatter(log_formatter)
            logger.addHandler(fh)
            logger.info("Logging rolls to %s", roll_log_path)

        # Collect targets from the three dropdowns + checkboxes
        targets: list[str] = []
        for i in range(3):
            if not check_vars[i].get():  # checkbox unchecked → skip
                continue
            category = dropdown_vars[i].get()
            min_val = min_level_vars[i].get()
            if not category or not min_val:
                continue
            options = crys_options.get(category, [])
            try:
                idx = options.index(min_val)
                targets += options[idx:]  # min_val and anything better
            except ValueError:
                pass

        if not targets:
            logger.warning("No valid targets selected — nothing to reroll for.")
            return

        match_mode = "AND" if match_mode_var.get() == "AND" else "OR"
        required_count = len(targets)  # for AND: all must match

        stop_flag.clear()
        reroll_thread = threading.Thread(
            target=reroll,
            args=(win, targets, match_mode, required_count, stop_flag, roll_log_path),
            daemon=True,
        )
        reroll_thread.start()

    def stop_reroll():
        stop_flag.set()
        logger.info("Stop requested — will halt after current roll.")

    # ---- build GUI ----

    root = tk.Tk()
    root.title("Exedra Auto Reroller")
    root.geometry("340x580+50+50")
    root.resizable(False, False)

    dropdown_options = list(crys_options.keys())
    dropdown_vars = []
    min_level_vars = []
    min_level_boxes = []
    check_vars = []

    def update_min_level(index):
        main_value = dropdown_vars[index].get()
        if main_value:
            new_values = crys_options[main_value]
            min_level_boxes[index]["values"] = new_values
            min_level_boxes[index].set(new_values[-1])

    # --- Three target rows ---
    for i in range(3):
        row_frame = ttk.Frame(root)
        row_frame.pack(fill="x", padx=10, pady=(6, 0))

        check_var = tk.BooleanVar(value=True)
        check_vars.append(check_var)
        ttk.Checkbutton(row_frame, variable=check_var).pack(side="left")

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
        box.bind("<<ComboboxSelected>>", lambda e, i=i: update_min_level(i))

        ttk.Label(root, text="  Minimum value").pack(anchor="w", padx=30)
        min_var = tk.StringVar(value="")
        min_level_vars.append(min_var)
        min_box = ttk.Combobox(
            root,
            textvariable=min_var,
            values=[],
            state="readonly",
            width=32,
        )
        min_box.pack(anchor="w", padx=(42, 10), pady=(0, 2))
        min_level_boxes.append(min_box)

    # --- Match mode ---
    ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10, pady=8)

    match_frame = ttk.Frame(root)
    match_frame.pack(anchor="w", padx=14)
    ttk.Label(match_frame, text="Match mode:").pack(side="left", padx=(0, 8))
    match_mode_var = tk.StringVar(value="OR")
    ttk.Radiobutton(
        match_frame, text="OR (any target)", variable=match_mode_var, value="OR"
    ).pack(side="left", padx=4)
    ttk.Radiobutton(
        match_frame, text="AND (all targets)", variable=match_mode_var, value="AND"
    ).pack(side="left", padx=4)

    # --- Options ---
    ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10, pady=8)

    should_log_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(root, text="Save roll log (JSONL)", variable=should_log_var).pack(
        anchor="w", padx=14
    )
    debug_log_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(root, text="Verbose debug logging", variable=debug_log_var).pack(
        anchor="w", padx=14
    )

    # --- Buttons ---
    ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10, pady=8)

    btn_frame = ttk.Frame(root)
    btn_frame.pack(pady=4)
    ttk.Button(btn_frame, text="Start Reroll", command=start_reroll).pack(
        side="left", padx=6
    )
    ttk.Button(btn_frame, text="Stop", command=stop_reroll).pack(side="left", padx=6)
    keyboard.add_hotkey("ctrl+shift+e", lambda: stop_reroll)

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
    ).pack(pady=(8, 0))
    ttk.Label(
        root,
        text="Current version: 1.0.0",
        foreground="black",
        font=("TkDefaultFont", 10),
    ).pack(pady=(8, 0))

    root.mainloop()


if __name__ == "__main__":
    main()
