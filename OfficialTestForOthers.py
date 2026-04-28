#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Software-side experimental controller for finger-specific vibrotactile testing.

What this program does:
- Stores pre-coded stimulus patterns
- Sends the corresponding finger commands to the apparatus API over serial
- Waits for matching participant keyboard input
- Scores perfect / imperfect replications
- Repeats a pattern until criterion is reached or 5 runs are exhausted
- Saves:
    1. Detailed trial-level log
    2. Pattern-level summary
    3. Test-level summary
    4. Overall participant summary

Test structure included:
- single_finger
- consistency
- sequence

Author: @haze1079
"""

import csv
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

import serial


# ============================================================
# CONFIGURATION
# ============================================================

PORT = "/dev/cu.usbmodem1101"
BAUD = 9600
SERIAL_TIMEOUT = 1.0

SERIAL_COMMAND_TEMPLATE = "SC{channel}"

PRE_TRIAL_DELAY_SEC = 1.0
INTER_STIMULUS_DELAY_SEC = 0.45
POST_STIMULUS_BEFORE_RESPONSE_SEC = 0.20
RESPONSE_TIMEOUT_SEC = 8.0
REST_BETWEEN_RUNS_SEC = 1.5
REST_BETWEEN_PATTERNS_SEC = 2.0
MAX_RUNS_PER_PATTERN = 5

OUTPUT_DIR = Path("experiment_output")
OUTPUT_DIR.mkdir(exist_ok=True)

CHANNEL_MAP = {
    "pinky": 0,
    "ring": 1,
    "middle": 2,
    "index": 3,
    "thumb": 4,
}

KEY_MAP = {
    "pinky": "a",
    "ring": "s",
    "middle": "d",
    "index": "f",
    "thumb": "space",
}

PRECODED_TESTS = {
    "single_finger": [
        ("single_trial_01", ["pinky"]),
        ("single_trial_02", ["ring"]),
        ("single_trial_03", ["middle"]),
        ("single_trial_04", ["index"]),
        ("single_trial_05", ["thumb"]),
    ],
    "consistency": [
        ("consistency_trial_01", ["pinky"]),
        ("consistency_trial_02", ["ring"]),
        ("consistency_trial_03", ["middle"]),
        ("consistency_trial_04", ["index"]),
        ("consistency_trial_05", ["thumb"]),
        ("consistency_trial_06", ["pinky"]),
        ("consistency_trial_07", ["ring"]),
        ("consistency_trial_08", ["middle"]),
        ("consistency_trial_09", ["index"]),
        ("consistency_trial_10", ["thumb"]),
    ],
    "sequence": [
        ("sequence_trial_01", ["pinky", "ring", "middle", "index", "thumb"]),
        ("sequence_trial_02", ["thumb", "index", "middle", "ring", "pinky"]),
        ("sequence_trial_03", ["middle", "index", "thumb", "ring", "pinky"]),
        ("sequence_trial_04", ["pinky", "thumb", "ring", "index", "middle"]),
        ("sequence_trial_05", ["pinky", "middle", "thumb", "ring", "index"]),
        ("sequence_trial_06", ["thumb", "middle", "pinky", "index", "ring"]),
        ("sequence_trial_07", ["pinky", "index", "ring", "thumb", "middle"]),
        ("sequence_trial_08", ["thumb", "ring", "index", "middle", "pinky"]),
    ],
}

SHUFFLE_PATTERNS = True
RUN_FAMILIARIZATION = True


# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class Pattern:
    test_type: str
    name: str
    fingers: list

    @property
    def expected_keys(self):
        return [KEY_MAP[f] for f in self.fingers]

    @property
    def expected_channels(self):
        return [CHANNEL_MAP[f] for f in self.fingers]

    @property
    def finger_sequence_str(self):
        return "-".join(self.fingers)

    @property
    def key_sequence_str(self):
        return "-".join(self.expected_keys)

    @property
    def channel_sequence_str(self):
        return "-".join(str(c) for c in self.expected_channels)


# ============================================================
# APPARATUS CONTROL
# ============================================================

class ApparatusController:
    def __init__(self, port, baud, timeout=1.0):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.serial_conn = None
        self.dry_run = False

        try:
            self.serial_conn = serial.Serial(self.port, self.baud, timeout=self.timeout)
            time.sleep(2.0)
            print(f"[INFO] Connected to apparatus on {self.port} @ {self.baud}")
        except Exception as e:
            self.dry_run = True
            print(f"[WARNING] Could not open serial port: {e}")
            print("[WARNING] Running in DRY RUN mode. No physical stimulus will be sent.")

    def send_finger_stimulus(self, finger_name):
        channel = CHANNEL_MAP[finger_name]
        cmd = SERIAL_COMMAND_TEMPLATE.format(channel=channel)

        if self.dry_run:
            print(f"[DRY RUN] Send stimulus -> finger={finger_name}, channel={channel}, cmd={cmd}")
            return cmd

        try:
            self.serial_conn.write((cmd + "\n").encode("utf-8"))
            self.serial_conn.flush()
            print(f"[SERIAL] {cmd}")
            return cmd
        except Exception as e:
            print(f"[ERROR] Failed to send serial command '{cmd}': {e}")
            return None

    def close(self):
        if self.serial_conn:
            try:
                self.serial_conn.close()
            except Exception:
                pass


# ============================================================
# UI / KEYBOARD CAPTURE
# ============================================================

class ExperimentUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Finger Stimulus Experiment")
        self.root.geometry("900x520")
        self.root.configure(bg="white")

        self.title_label = tk.Label(
            self.root,
            text="Finger Stimulus Experiment",
            font=("Helvetica", 20, "bold"),
            bg="white"
        )
        self.title_label.pack(pady=(20, 10))

        self.message_label = tk.Label(
            self.root,
            text="",
            font=("Helvetica", 14),
            bg="white",
            justify="left",
            wraplength=820
        )
        self.message_label.pack(pady=20)

        self.status_label = tk.Label(
            self.root,
            text="",
            font=("Helvetica", 12),
            fg="darkblue",
            bg="white"
        )
        self.status_label.pack(pady=10)

        self.keymap_label = tk.Label(
            self.root,
            text=self._build_keymap_text(),
            font=("Helvetica", 12),
            fg="black",
            bg="white",
            justify="left"
        )
        self.keymap_label.pack(pady=15)

        self.mode = None
        self.wait_var = tk.BooleanVar(value=False)
        self.capture_active = False
        self.capture_keys = []
        self.capture_start_time = None
        self.first_key_latency = None
        self.completion_latency = None
        self.expected_capture_len = 0
        self.capture_timeout_job = None

        self.root.bind("<Key>", self._on_key)
        self.root.focus_force()
        self.root.update()

    def _build_keymap_text(self):
        return (
            "Keyboard response mapping:\n"
            "  Response Key 1 -> a\n"
            "  Response Key 2 -> s\n"
            "  Response Key 3 -> d\n"
            "  Response Key 4 -> f\n"
            "  Response Key 5 -> space"
        )

    def _normalize_key(self, event):
        if event.keysym.lower() == "space":
            return "space"
        if event.keysym.lower() == "return":
            return "return"
        if event.char and event.char.isprintable():
            return event.char.lower()
        return event.keysym.lower()

    def _on_key(self, event):
        key = self._normalize_key(event)

        if self.mode == "continue":
            if key in ("space", "return"):
                self.wait_var.set(True)

        elif self.mode == "capture" and self.capture_active:
            allowed = set(KEY_MAP.values())
            if key not in allowed:
                return

            now = time.perf_counter()

            if self.first_key_latency is None:
                self.first_key_latency = now - self.capture_start_time

            self.capture_keys.append(key)
            self.status_label.config(
                text=f"Captured input: {' - '.join(self.capture_keys)}"
            )

            if len(self.capture_keys) >= self.expected_capture_len:
                self.completion_latency = now - self.capture_start_time
                self.capture_active = False
                if self.capture_timeout_job is not None:
                    self.root.after_cancel(self.capture_timeout_job)
                    self.capture_timeout_job = None
                self.wait_var.set(True)

    def show_message(self, text, status=""):
        self.message_label.config(text=text)
        self.status_label.config(text=status)
        self.root.update_idletasks()
        self.root.update()

    def wait_for_continue(self, text):
        self.mode = "continue"
        self.wait_var.set(False)
        self.show_message(text, status="Press SPACE or ENTER to continue.")
        self.root.wait_variable(self.wait_var)
        self.root.focus_force()

    def capture_response_sequence(self, expected_len, timeout_sec):
        self.mode = "capture"
        self.wait_var.set(False)
        self.capture_active = True
        self.capture_keys = []
        self.capture_start_time = time.perf_counter()
        self.first_key_latency = None
        self.completion_latency = None
        self.expected_capture_len = expected_len
        self.status_label.config(text="Waiting for participant response...")
        self.root.focus_force()

        def timeout_func():
            self.capture_active = False
            self.wait_var.set(True)

        self.capture_timeout_job = self.root.after(int(timeout_sec * 1000), timeout_func)
        self.root.wait_variable(self.wait_var)

        if self.capture_timeout_job is not None:
            try:
                self.root.after_cancel(self.capture_timeout_job)
            except Exception:
                pass
            self.capture_timeout_job = None

        self.mode = None
        self.root.focus_force()

        return self.capture_keys, self.first_key_latency, self.completion_latency

    def countdown(self, seconds, prefix="Starting in"):
        for remaining in range(seconds, 0, -1):
            self.status_label.config(text=f"{prefix} {remaining}...")
            self.root.update()
            time.sleep(1)
        self.status_label.config(text="")
        self.root.update()

    def close(self):
        self.root.destroy()


# ============================================================
# LOGGING / SUMMARIES
# ============================================================

def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_pattern_summaries(trial_rows):
    grouped = {}

    for row in trial_rows:
        key = (row["participant_id"], row["test_type"], row["pattern_name"])
        grouped.setdefault(key, []).append(row)

    summaries = []
    for (participant_id, test_type, pattern_name), rows in grouped.items():
        rows_sorted = sorted(rows, key=lambda r: int(r["run_number"]))
        runs_attempted = len(rows_sorted)
        successful_runs = sum(1 for r in rows_sorted if str(r["was_correct"]) == "True")
        reached_criterion = any(str(r["was_correct"]) == "True" for r in rows_sorted)

        criterion_run = ""
        if reached_criterion:
            for r in rows_sorted:
                if str(r["was_correct"]) == "True":
                    criterion_run = r["run_number"]
                    break
        else:
            criterion_run = str(MAX_RUNS_PER_PATTERN)

        last = rows_sorted[-1]

        summaries.append({
            "participant_id": participant_id,
            "test_type": test_type,
            "pattern_name": pattern_name,
            "expected_finger_sequence": last["expected_finger_sequence"],
            "expected_key_sequence": last["expected_key_sequence"],
            "expected_channel_sequence": last["expected_channel_sequence"],
            "runs_attempted": runs_attempted,
            "successful_runs": successful_runs,
            "reached_criterion": reached_criterion,
            "run_of_criterion_or_max": criterion_run,
            "final_response_sequence": last["response_key_sequence"],
        })

    return summaries


def build_test_summaries(pattern_summary_rows):
    grouped = {}

    for row in pattern_summary_rows:
        key = (row["participant_id"], row["test_type"])
        grouped.setdefault(key, []).append(row)

    summaries = []
    for (participant_id, test_type), rows in grouped.items():
        total_patterns = len(rows)
        criterion_count = sum(1 for r in rows if str(r["reached_criterion"]) == "True")
        failed_count = total_patterns - criterion_count
        total_runs = sum(int(r["runs_attempted"]) for r in rows)
        successful_runs = sum(int(r["successful_runs"]) for r in rows)
        avg_runs = total_runs / total_patterns if total_patterns else 0.0

        summaries.append({
            "participant_id": participant_id,
            "test_type": test_type,
            "total_patterns": total_patterns,
            "patterns_reaching_criterion": criterion_count,
            "patterns_not_reaching_criterion": failed_count,
            "successful_runs": successful_runs,
            "mean_runs_attempted": round(avg_runs, 3),
            "criterion_rate": round((criterion_count / total_patterns), 3) if total_patterns else 0.0,
        })

    return summaries


def build_overall_summary(pattern_summary_rows, trial_rows):
    if not pattern_summary_rows:
        return []

    participant_id = pattern_summary_rows[0]["participant_id"]
    total_patterns = len(pattern_summary_rows)
    criterion_count = sum(1 for r in pattern_summary_rows if str(r["reached_criterion"]) == "True")
    total_runs = sum(int(r["runs_attempted"]) for r in pattern_summary_rows)
    successful_runs = sum(int(r["successful_runs"]) for r in pattern_summary_rows)
    total_successful_replications = sum(1 for r in trial_rows if str(r["was_correct"]) == "True")
    total_attempted_replications = len(trial_rows)

    return [{
        "participant_id": participant_id,
        "total_patterns": total_patterns,
        "patterns_reaching_criterion": criterion_count,
        "patterns_not_reaching_criterion": total_patterns - criterion_count,
        "successful_runs": successful_runs,
        "total_runs_attempted": total_runs,
        "total_successful_replications": total_successful_replications,
        "total_attempted_replications": total_attempted_replications,
        "overall_accuracy": round(
            total_successful_replications / total_attempted_replications, 3
        ) if total_attempted_replications else 0.0,
        "criterion_rate": round(
            criterion_count / total_patterns, 3
        ) if total_patterns else 0.0,
    }]


# ============================================================
# EXPERIMENT FLOW
# ============================================================

def create_patterns():
    patterns = []
    for test_type, items in PRECODED_TESTS.items():
        for name, fingers in items:
            patterns.append(Pattern(test_type=test_type, name=name, fingers=fingers))
    return patterns


def run_familiarization(controller, ui):
    ui.wait_for_continue(
        "Familiarization phase.\n\n"
        "You will feel one stimulus at a time.\n"
        "During this phase, the program will tell you which finger is being cued.\n"
        "Keep your fingers resting on the assigned keys.\n\n"
        "Press SPACE or ENTER to begin."
    )

    for finger in ["pinky", "ring", "middle", "index", "thumb"]:
        key = KEY_MAP[finger]
        channel = CHANNEL_MAP[finger]

        ui.show_message(
            "Familiarization\n\n"
            f"Target finger: {finger.title()}\n"
            f"Keyboard key: {key}\n"
            f"Channel: SC{channel}\n\n"
            "A stimulus will be presented now.",
            status="Observe which finger receives the cue."
        )

        time.sleep(PRE_TRIAL_DELAY_SEC)
        controller.send_finger_stimulus(finger)
        time.sleep(INTER_STIMULUS_DELAY_SEC)

        ui.wait_for_continue("Press SPACE or ENTER for the next familiarization cue.")

    ui.show_message("Familiarization complete.")
    time.sleep(1.0)

def run_pattern_trial(participant_id, pattern, run_number, controller, ui):
    expected_keys = pattern.expected_keys

    ui.wait_for_continue(
        f"Test type: {pattern.test_type}\n"
        f"Pattern: {pattern.name}\n"
        f"Run: {run_number}/{MAX_RUNS_PER_PATTERN}\n\n"
        f"Expected sequence length: {len(pattern.fingers)}\n"
        f"Keep your fingers on the assigned keys.\n\n"
        "Press SPACE or ENTER to start this run."
    )

    ui.show_message(
        f"Pattern {pattern.name}\n\nStimulus incoming.",
        status="Prepare for stimulus."
    )
    time.sleep(PRE_TRIAL_DELAY_SEC)

    stimulus_start = time.perf_counter()
    sent_commands = []

    for finger in pattern.fingers:
        cmd = controller.send_finger_stimulus(finger)
        sent_commands.append(cmd if cmd is not None else "")
        ui.show_message(
            f"Pattern {pattern.name}\n\nStimulus sequence in progress.",
            status="Delivering stimulus cue..."
        )
        time.sleep(INTER_STIMULUS_DELAY_SEC)

    time.sleep(POST_STIMULUS_BEFORE_RESPONSE_SEC)

    ui.show_message(
        f"Pattern {pattern.name}\n\nEnter the response sequence now.",
        status=f"Expected number of keypresses: {len(expected_keys)}"
    )

    response_keys, first_key_latency, completion_latency = ui.capture_response_sequence(
        expected_len=len(expected_keys),
        timeout_sec=RESPONSE_TIMEOUT_SEC
    )

    was_correct = response_keys == expected_keys
    successful_run = was_correct

    if was_correct:
        ui.show_message(
            f"Pattern {pattern.name}\n\nCorrect replication.",
            status="Run successful."
        )
    else:
        ui.show_message(
            f"Pattern {pattern.name}\n\nIncorrect replication.",
            status="Run unsuccessful."
        )

    time.sleep(REST_BETWEEN_RUNS_SEC)

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "participant_id": participant_id,
        "test_type": pattern.test_type,
        "pattern_name": pattern.name,
        "run_number": run_number,
        "expected_finger_sequence": pattern.finger_sequence_str,
        "expected_key_sequence": pattern.key_sequence_str,
        "expected_channel_sequence": pattern.channel_sequence_str,
        "sent_command_sequence": "|".join(sent_commands),
        "response_key_sequence": "-".join(response_keys),
        "response_length": len(response_keys),
        "was_correct": was_correct,
        "successful_run": successful_run,
        "first_key_latency_sec": round(first_key_latency, 4) if first_key_latency is not None else "",
        "completion_latency_sec": round(completion_latency, 4) if completion_latency is not None else "",
        "stimulus_start_perf_counter": round(stimulus_start, 6),
    }


def run_experiment():
    participant_id = input("Enter participant ID: ").strip()
    if not participant_id:
        participant_id = "participant_unknown"

    apparatus = ApparatusController(PORT, BAUD, timeout=SERIAL_TIMEOUT)
    ui = ExperimentUI()

    trial_rows = []

    try:
        if RUN_FAMILIARIZATION:
            run_familiarization(apparatus, ui)

        ui.wait_for_continue(
            "Formal testing will now begin.\n\n"
            "The software will send pre-coded finger patterns to the apparatus.\n"
            "You must reproduce each pattern using the assigned keyboard keys.\n\n"
            f"A pattern will repeat until perfect replication or {MAX_RUNS_PER_PATTERN} runs maximum.\n\n"
            "Press SPACE or ENTER to begin."
        )

        for test_type, items in PRECODED_TESTS.items():
            patterns = [Pattern(test_type=test_type, name=name, fingers=fingers) for name, fingers in items]

            if SHUFFLE_PATTERNS:
                random.shuffle(patterns)

            ui.wait_for_continue(
                f"Starting test block: {test_type}\n\n"
                f"Number of patterns in this block: {len(patterns)}\n\n"
                "Press SPACE or ENTER to continue."
            )

            for pattern in patterns:
                criterion_met = False
                run_number = 1

                while run_number <= MAX_RUNS_PER_PATTERN and not criterion_met:
                    trial_row = run_pattern_trial(
                        participant_id=participant_id,
                        pattern=pattern,
                        run_number=run_number,
                        controller=apparatus,
                        ui=ui,
                    )
                    trial_rows.append(trial_row)

                    if trial_row["was_correct"]:
                        criterion_met = True
                    else:
                        run_number += 1

                ui.show_message(
                    f"Pattern complete: {pattern.name}",
                    status="Short rest before the next pattern."
                )
                time.sleep(REST_BETWEEN_PATTERNS_SEC)

        pattern_summary_rows = build_pattern_summaries(trial_rows)
        test_summary_rows = build_test_summaries(pattern_summary_rows)
        overall_summary_rows = build_overall_summary(pattern_summary_rows, trial_rows)

        timestamp_tag = datetime.now().strftime("%Y%m%d_%H%M%S")

        trial_log_path = OUTPUT_DIR / f"{participant_id}_trial_log_{timestamp_tag}.csv"
        pattern_summary_path = OUTPUT_DIR / f"{participant_id}_pattern_summary_{timestamp_tag}.csv"
        test_summary_path = OUTPUT_DIR / f"{participant_id}_test_summary_{timestamp_tag}.csv"
        overall_summary_path = OUTPUT_DIR / f"{participant_id}_overall_summary_{timestamp_tag}.csv"

        trial_fieldnames = [
            "timestamp",
            "participant_id",
            "test_type",
            "pattern_name",
            "run_number",
            "expected_finger_sequence",
            "expected_key_sequence",
            "expected_channel_sequence",
            "sent_command_sequence",
            "response_key_sequence",
            "response_length",
            "was_correct",
            "successful_run",
            "first_key_latency_sec",
            "completion_latency_sec",
            "stimulus_start_perf_counter",
        ]

        pattern_fieldnames = [
            "participant_id",
            "test_type",
            "pattern_name",
            "expected_finger_sequence",
            "expected_key_sequence",
            "expected_channel_sequence",
            "runs_attempted",
            "successful_runs",
            "reached_criterion",
            "run_of_criterion_or_max",
            "final_response_sequence",
        ]

        test_fieldnames = [
            "participant_id",
            "test_type",
            "total_patterns",
            "patterns_reaching_criterion",
            "patterns_not_reaching_criterion",
            "successful_runs",
            "mean_runs_attempted",
            "criterion_rate",
        ]

        overall_fieldnames = [
            "participant_id",
            "total_patterns",
            "patterns_reaching_criterion",
            "patterns_not_reaching_criterion",
            "successful_runs",
            "total_runs_attempted",
            "total_successful_replications",
            "total_attempted_replications",
            "overall_accuracy",
            "criterion_rate",
        ]

        write_csv(trial_log_path, trial_fieldnames, trial_rows)
        write_csv(pattern_summary_path, pattern_fieldnames, pattern_summary_rows)
        write_csv(test_summary_path, test_fieldnames, test_summary_rows)
        write_csv(overall_summary_path, overall_fieldnames, overall_summary_rows)

        summary_text = (
            f"Experiment complete.\n\n"
            f"Saved files:\n"
            f"- {trial_log_path}\n"
            f"- {pattern_summary_path}\n"
            f"- {test_summary_path}\n"
            f"- {overall_summary_path}\n"
        )

        print(summary_text)
        messagebox.showinfo("Experiment Complete", summary_text)
        ui.show_message(summary_text, status="You may now close the window.")
        ui.wait_for_continue("Press SPACE or ENTER to close the experiment window.")

    finally:
        apparatus.close()
        ui.close()


if __name__ == "__main__":
    run_experiment()