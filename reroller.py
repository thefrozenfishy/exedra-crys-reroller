import ctypes
import difflib
import json
import logging
import os
import re
import sys
import threading
import tkinter as tk
import webbrowser
from collections import defaultdict
from datetime import datetime
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

import cv2
import keyboard
import numpy as np
import pyautogui
import pydirectinput
import pygetwindow
import pytesseract
import win32gui
import win32ui
from PIL import Image, ImageDraw
from requests import get

nice_names = {
    35: ("Increases critical rate", 0),
    36: ("Increases critical DMG", 0),
    37: ("Increases break effect", 1),
    34: ("Increases SPD", 2),
    31: ("Increases max HP", 3),
    40: ("Increases debuff RES", 3),
    39: ("Increases debuff hit rate", 3),
    33: ("DEF+ (fyi 45 DEF ≈ 100 HP)", 4),
    32: ("ATK+ (fyi 60 ATK ≈ 2% Crit DMG)", 4),
    38: ("Increases HP recovery amount", 9),
}
PERMALOCK_PRIORITY = {v[0]: v[1] for v in nice_names.values()}
PERMALOCK_EXCLUDED = {"Increases HP recovery amount"}
NAME_PREFIXES = {"Increases", "ATK", "DEF", "Max"}
crys_options = defaultdict(list)
all_possible = set()

SETTINGS_FILE = "settings.json"
__version__ = "vDEV"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def resource_path(relative_path):
    """Get absolute path to resource."""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


with open(resource_path("getSelectionAbilityMstList.json"), "r", encoding="utf-8") as f:
    crys = json.load(f)["payload"]["mstList"]
    for c in crys:
        if c["selectionAbilityType"] == 2:
            crys_options[nice_names[c["selectionAbilityEffectId"]][0]].append(
                c["description"]
            )
            all_possible.add(c["description"])

_normalised_to_canonical = {_normalize(s): s for s in all_possible}
_normalised_to_canonical["increaseshprecoveryamountby."] = (
    "Increases HP recovery amount by 8%."
)
_normalised_to_canonical["???"] = "???"
crys_options_reverse = {
    desc: cat for cat, descs in crys_options.items() for desc in descs
}
possible_chars = set("".join(_normalised_to_canonical.values()))
possible_chars.remove(" ")

TARGET_WINDOW = "MadokaExedra"
TESS_CONFIG = (
    "--oem 3 --psm 6 " + "-c tessedit_char_whitelist=" + "".join(sorted(possible_chars))
)
REROLL_SCREEN_TEXT = _normalize(
    "Use Paint Drop to roll the boost effects for the following Crystalis ability."
)
REMOVE_PERMALOCK_TEXT = _normalize("Remove Permalock Confirmation")
pydirectinput.FAILSAFE = False
keyboard.add_hotkey("ctrl+shift+q", lambda: os._exit(0))

log_formatter = logging.Formatter()
logger = logging.getLogger("crys_reroller")
logger.setLevel(logging.INFO)


class _GUILogHandler(logging.Handler):
    def __init__(self, widget: ScrolledText):
        super().__init__()
        self.widget = widget

    def emit(self, record: logging.LogRecord):
        msg = self.format(record) + "\n"

        def _append():
            self.widget.configure(state="normal")
            self.widget.insert(tk.END, msg)
            self.widget.see(tk.END)
            self.widget.configure(state="disabled")

        try:
            self.widget.after(0, _append)
        except RuntimeError:
            pass  # widget destroyed


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


def _ocr_raw(img: Image.Image, debug_log: bool, idx: int) -> str:
    variant_img, name = _prepare_variants(img)[0]

    return _ocr_image(
        variant_img,
        debug_log,
        idx,
        name,
    )


def is_on_reroll_screen(win, debug_log) -> bool:
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    img = _capture_client(hwnd)
    w, h = img.size
    coords = (
        0.2 * w,
        0.05 * h,
        0.8 * w,
        0.15 * h,
    )
    ocr_text = _ocr_raw(img.crop(coords), debug_log, 99)

    if debug_log:
        draw = ImageDraw.Draw(img)
        draw.rectangle(coords, outline="green", width=2)
        img.save("debug/reroll_screen.png")
    if not ocr_text:
        logger.debug("No reroll text")
        return False

    score = difflib.SequenceMatcher(
        None,
        REROLL_SCREEN_TEXT,
        _normalize(ocr_text),
    ).ratio()

    logger.debug("reroll screen text was %s, with a score of %f", ocr_text, score)
    return score >= 0.75


def _capture_client(hwnd: int) -> Image.Image:
    win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(hwnd)
    w = win_right - win_left
    h = win_bottom - win_top

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0x2)

    bmp_info = bmp.GetInfo()
    raw = bmp.GetBitmapBits(True)
    full_img = Image.frombuffer(
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

    client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
    client_rect = win32gui.GetClientRect(hwnd)
    cx = client_left - win_left
    cy = client_top - win_top
    cw = client_rect[2]
    ch = client_rect[3]
    return full_img.crop((cx, cy, cx + cw, cy + ch))


def _strip_noise_prefix(text: str) -> str:
    tokens = text.split()
    for i, token in enumerate(tokens):
        if any(token.startswith(p) for p in NAME_PREFIXES):
            return " ".join(tokens[i:])
    return text


def _prepare_variants(img_colour: Image.Image):
    arr = np.array(img_colour)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return [(bw, "bw"), (gray, "gray"), (arr, "colour")]


def _ocr_image(variant_img, debug_log: bool, idx: int, name: str) -> str:
    os.makedirs("debug", exist_ok=True)

    if debug_log:
        Image.fromarray(variant_img).save(f"debug/{name}_{idx}.png")

    try:
        raw = pytesseract.image_to_string(
            variant_img,
            config=TESS_CONFIG,
        )
        return re.sub(r"\s+", " ", raw).strip()
    except pytesseract.TesseractNotFoundError as e:
        input(
            "Tesseract is not in path! Download it, put it in math, restart your PC, and try again!"
        )
        raise e


def _find_ability_in_text(ocr_text: str) -> tuple[str | None, float]:
    norm_text = _normalize(ocr_text)

    for norm_ability, canonical in _normalised_to_canonical.items():
        if norm_ability in norm_text:
            return canonical, 1.0

    best_match = None
    best_score = 0.0

    for line in ocr_text.splitlines():
        line = _strip_noise_prefix(line.strip())

        if not line:
            continue

        logger.debug("Checking line for matches: '%s'", line)

        match = difflib.get_close_matches(line, all_possible, n=1, cutoff=0.65)

        if not match:
            continue

        candidate = match[0]
        score = difflib.SequenceMatcher(None, line, candidate).ratio()

        if score > best_score:
            best_match = candidate
            best_score = score

    if best_score >= 0.65:
        return best_match, best_score

    return None, 0.0


def fetch_current_crys_values(
    win, debug_log: bool, check_locked: bool
) -> list[str | None]:
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    if not hwnd:
        logger.error("Game window handle not found")
        return [None, None, None]

    img = _capture_client(hwnd)
    w, h = img.size
    current_values = []
    for i in range(3):
        coords = (
            (0.51 if check_locked else 0.24) * w,
            (0.08 * i + 0.24) * h,
            (0.73 if check_locked else 0.47) * w,
            (0.08 * i + 0.32) * h,
        )
        if debug_log:
            draw = ImageDraw.Draw(img)
            draw.rectangle(coords, outline="green", width=2)
        crop = img.crop(coords)
        current_values.append(_ocr_slot(crop, debug_log, 10 + i if check_locked else i))
    if debug_log:
        img.save(f"debug/crys_vals_{'' if check_locked else 'not_'}locked.png")
    return current_values


def _ocr_slot(img: Image.Image, debug_log: bool, idx: int) -> str | None:
    best_result = None
    best_score = 0.0

    for variant_img, name in _prepare_variants(img):

        text = _ocr_image(variant_img, debug_log, idx, name)

        logger.debug("[%s] OCR text:\n%s", name, text)

        result, score = _find_ability_in_text(text)

        logger.debug(
            "[%s] parsed → %s (score=%.3f)",
            name,
            result,
            score,
        )

        if score > best_score:
            best_result = result
            best_score = score

        if score >= 0.95:
            return result

    return best_result


def click(x: float | int, y: float | int):
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    if not hwnd:
        return
    prev_hwnd = win32gui.GetForegroundWindow()
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    pyautogui.sleep(0.02)
    curr = pyautogui.position()
    pydirectinput.click(int(x), int(y))
    pyautogui.moveTo(curr)
    pyautogui.sleep(0.02)
    ctypes.windll.user32.SetForegroundWindow(prev_hwnd)


def is_on_remove_permalock_screen(win, debug_log) -> bool:
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    img = _capture_client(hwnd)
    w, h = img.size
    coords = (0.3 * w, 0.05 * h, 0.7 * w, 0.12 * h)
    ocr_text = _ocr_raw(img.crop(coords), debug_log, 79)
    if debug_log:
        draw = ImageDraw.Draw(img)
        draw.rectangle(coords, outline="green", width=2)
        img.save("debug/permalock_remove.png")
    if not ocr_text:
        logger.debug("No permalock text")
        return False

    score = difflib.SequenceMatcher(
        None,
        REMOVE_PERMALOCK_TEXT,
        _normalize(ocr_text),
    ).ratio()

    logger.debug("permalock text was %s, with a score of %f", ocr_text, score)
    return score >= 0.75


def click_reroll_button(win, debug_log) -> None:
    key = "enter"
    if is_on_remove_permalock_screen(win, debug_log):
        key = "esc"
    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
    if not hwnd:
        return
    prev_hwnd = win32gui.GetForegroundWindow()
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    pyautogui.sleep(0.02)
    pydirectinput.press(key)
    pyautogui.sleep(0.02)
    ctypes.windll.user32.SetForegroundWindow(prev_hwnd)


def _reroll_wrapper(*args, **kwargs):
    try:
        reroll(*args, **kwargs)
    except Exception as e:
        logger.exception("Reroll thread crashed", exc_info=e)


def reroll(
    win,
    targets: list[str],
    match_mode: str,
    should_permalock: bool,
    target_categories: set[str],
    stop_flag: threading.Event,
    roll_log_path: str | None,
    debug_log: bool = False,
):
    target_set = set(targets)
    roll_number = 0

    logger.debug(
        "Starting reroll | mode=%s | targets: %s", match_mode, list(target_set)
    )
    already_locked_targets = []
    while not stop_flag.is_set():
        pyautogui.sleep(0.2)
        if not is_on_reroll_screen(win, debug_log):
            logger.debug("Not on reroll screen")
            if stop_flag.is_set():
                break
            click_reroll_button(win, debug_log)
            continue

        current_values = fetch_current_crys_values(win, debug_log, False)

        if None in current_values:
            logger.debug(
                "Roll #%d — could not read all substats (%s), retrying…",
                roll_number,
                current_values,
            )
            continue

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

        found_targets = [v for v in current_values if v in target_set]

        logger.info(
            "Roll #%d %s",
            roll_number,
            ", ".join(["✔" if v in target_set else "❌" for v in current_values]),
        )
        logger.debug(
            "current values: %s | found targets: %s",
            current_values,
            found_targets,
        )

        if found_targets:
            if match_mode == "OR":
                logger.info("Found one target in OR mode, stopping")
                return
            if match_mode == "AND":
                found_categories = {crys_options_reverse.get(v) for v in found_targets}
                if target_categories.issubset(found_categories):
                    logger.info("Found all target categories in AND mode, stopping")
                    return

                if should_permalock:
                    if len(already_locked_targets) < 3:
                        already_locked_targets = fetch_current_crys_values(
                            win, debug_log, True
                        )

                    locked_categories = {
                        crys_options_reverse.get(v)
                        for v in already_locked_targets
                        if v is not None
                    }

                    still_needed = target_categories - locked_categories

                    present_cats = {
                        crys_options_reverse.get(v)
                        for v in current_values
                        if v in target_set
                    }
                    lockable_now = present_cats - locked_categories - PERMALOCK_EXCLUDED

                    # Categories we still need but are NOT on this roll at all
                    missing_cats = still_needed - present_cats - PERMALOCK_EXCLUDED
                    logger.debug(
                        "Stuff is %s, %s, %s, %s, %s, %s, %s",
                        found_categories,
                        already_locked_targets,
                        locked_categories,
                        still_needed,
                        present_cats,
                        lockable_now,
                        missing_cats,
                    )
                    if lockable_now:
                        best_lockable_priority = min(
                            PERMALOCK_PRIORITY.get(cat, 999) for cat in lockable_now
                        )
                        best_missing_priority = min(
                            (PERMALOCK_PRIORITY.get(cat, 999) for cat in missing_cats),
                            default=999,
                        )
                        logger.debug(
                            "prios: %d vs %d",
                            best_lockable_priority,
                            best_missing_priority,
                        )

                        if best_missing_priority >= best_lockable_priority:
                            for i, v in enumerate(current_values):
                                cat = crys_options_reverse.get(v)
                                logger.debug("%s with %s", v in target_set, cat)
                                if (
                                    v in target_set
                                    and cat not in locked_categories
                                    and cat not in PERMALOCK_EXCLUDED
                                    and PERMALOCK_PRIORITY.get(cat, 999)
                                    == best_lockable_priority
                                ):
                                    logger.info(
                                        "Permalock priority %d — locking %s at index %d",
                                        best_lockable_priority,
                                        v,
                                        i,
                                    )
                                    hwnd = win32gui.FindWindow(None, TARGET_WINDOW)
                                    cl, ct = win32gui.ClientToScreen(hwnd, (0, 0))
                                    cr = win32gui.GetClientRect(hwnd)
                                    cw, ch = cr[2], cr[3]

                                    click(
                                        cl + 0.85 * cw,
                                        ct + (0.08 * i + 0.3) * ch,
                                    )
                                    click_reroll_button(win, debug_log)
                                    click(
                                        cl + 0.6 * cw,
                                        ct + 0.75 * ch,
                                    )
                                    pyautogui.sleep(3)
                                    already_locked_targets = []
                        else:
                            logger.info(
                                "Skipping permalock — missing rarer category (priority %d) not on this roll",
                                best_missing_priority,
                            )

        if stop_flag.is_set():
            break
        roll_number += 1
        click_reroll_button(win, debug_log)
        pyautogui.sleep(0.75)

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
    except Exception:
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
    try:
        root.iconbitmap(resource_path("icon.ico"))
    except Exception:
        pass

    if new_version := check_git_version_match():
        height = 720
    else:
        height = 660
    root.geometry(f"340x{height}+50+50")
    root.resizable(False, False)

    dropdown_options = [""] + list(crys_options.keys())  # "" = clear/empty option
    dropdown_vars = []
    min_level_vars = []
    min_level_boxes = []

    saved_targets = settings.get("targets", [{}, {}, {}])

    def persist_settings(*_):
        """Write current GUI state to settings.json."""
        data = {
            "match_mode": match_mode_var.get(),
            "should_log": should_log_var.get(),
            "debug_log": debug_log_var.get(),
            "permalock_once_reached": permalock_var.get(),
            "targets": [
                {
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
            min_level_boxes[index]["values"] = new_values
            min_level_boxes[index].set(new_values[-1])
        else:
            min_level_boxes[index]["values"] = [""]
            min_level_boxes[index].set("")
        persist_settings()

    for i in range(3):
        row_frame = ttk.Frame(root)
        row_frame.pack(fill="x", padx=10, pady=(6, 0))

        saved = saved_targets[i] if i < len(saved_targets) else {}
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
            min_level_boxes[i]["values"] = options
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
    permalock_var = tk.BooleanVar(value=settings.get("permalock_once_reached", True))
    permalock_check = ttk.Checkbutton(
        root,
        text="Permalock options underways",
        variable=permalock_var,
        command=persist_settings,
    )
    permalock_check.pack(anchor="w", padx=28, pady=(2, 0))
    ttk.Label(
        root,
        text="Locks in rarest options first to minimize total rolls.\nMeaning you might see it roll past spd4 or HP420\nthis is by design.",
        foreground="gray",
        font=("TkDefaultFont", 8),
    ).pack(anchor="w", padx=44)

    def update_permalock_visibility(*_):
        if match_mode_var.get() == "AND":
            permalock_check.state(["!disabled"])
        else:
            permalock_check.state(["disabled"])

    match_mode_var.trace_add("write", update_permalock_visibility)
    update_permalock_visibility()

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

        run_id = datetime.today().strftime("%Y-%m-%dT%H-%M-%S")
        roll_log_path: str | None = None
        if should_log_var.get():
            os.makedirs("reroll_logs", exist_ok=True)
            roll_log_path = f"reroll_logs/{run_id}.jsonl"
            logger.info("Logging rolls for %s", run_id)
        if debug_log_var.get():
            os.makedirs("debug/logs", exist_ok=True)
            fh = logging.FileHandler(f"debug/logs/{run_id}.txt", encoding="utf-8")
            fh.setFormatter(log_formatter)
            logger.addHandler(fh)
            logger.info("Debugging for %s", run_id)

        target_categories = set()
        match_mode = "AND" if match_mode_var.get() == "AND" else "OR"
        for i in range(3):
            category = dropdown_vars[i].get()
            min_val = min_level_vars[i].get()
            if not category or not min_val:
                continue
            target_categories.add(category)
        lowest_prio_cat = None
        if len(target_categories) == 3:
            lowest_prio_cat = max(
                (PERMALOCK_PRIORITY.get(t, 999), t) for t in target_categories
            )[1]

        targets: list[str] = []
        for i in range(3):
            category = dropdown_vars[i].get()
            min_val = min_level_vars[i].get()
            if not category or not min_val:
                continue
            options = crys_options.get(category, [])
            idx = options.index(min_val)
            if (
                match_mode == "AND"
                and permalock_var.get()
                and category != lowest_prio_cat
                and idx != len(options) - 1
            ):
                logger.info(
                    "Permalocking mode, forcing '%s' to be '%s'",
                    options[idx],
                    options[-1],
                )
                idx = -1
            targets += options[idx:]

        if not targets:
            logger.warning("No valid targets selected — nothing to reroll for.")
            return

        stop_flag.clear()
        reroll_thread = threading.Thread(
            target=_reroll_wrapper,
            args=(
                win,
                targets,
                match_mode,
                permalock_var.get(),
                target_categories,
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
    if new_version:
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
                f"https://github.com/thefrozenfishy/exedra-crys-reroller/releases/tag/version-{new_version}"
            ),
        )
        button.pack()

    ttk.Separator(root, orient="horizontal").pack(fill="x", padx=10, pady=(10, 4))
    ttk.Label(root, text="Log", font=("TkDefaultFont", 8), foreground="gray").pack(
        anchor="w", padx=14
    )
    log_box = ScrolledText(
        root,
        state="disabled",
        height=8,
        font=("Courier", 8),
        wrap="word",
    )
    log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    gui_handler = _GUILogHandler(log_box)
    gui_handler.setFormatter(log_formatter)
    logger.addHandler(gui_handler)

    logger.info("Running version %s", __version__)

    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("A critical error occurred", exc_info=e)
        input("Press Enter to exit...")
