#!/usr/bin/env python3
"""
VSPA:PE Fall 2026
Single-Finger + Adjacent-Finger Confounding Study
Version 1.5 - acknowledged serial hardware self-test

CURRENT STUDY SCOPE
-------------------
This program is designed for the current pre-sequence stage of VSPA:PE.

It runs two formal test types:

1. SINGLE-FINGER TESTING
   - first 5 trials are fixed: pinky -> ring -> middle -> index -> thumb
   - next 5 trials randomly select one finger per trial
   - exactly one LRA motor is activated on each of these trials
   - the participant identifies the finger using A/S/D/F/Space

2. ADJACENT-PAIR TEST
   - 5 trials total
   - each trial randomly selects one ordered pair of physically adjacent fingers
   - the two selected motors are activated in quick succession
   - both directions are possible, such as pinky -> ring or ring -> pinky
   - repeats are allowed because every trial is independently randomized
   - the participant responds with TWO keys in perceived order
   - the program records whether the response was exact, reversed, partially
     correct, or otherwise confused

TOTAL DEFAULT FORMAL TRIALS:
   5 fixed single + 5 random single + 5 random adjacent-pair = 15 trials

This version deliberately DOES NOT:
   - run the complete multi-finger sequence section
   - ask the participant for a comfort rating
   - ask the participant for a perceived-intensity rating
   - calculate a comfort-based threshold

It DOES ask one short confounding/spread question after each trial:
   - single finger: did the vibration seem to spread to another finger?
   - adjacent pair: did the two cues feel blended/confounded?

Set ASK_CONFOUNDING_REPORT = False if you want a purely objective
keyboard-response study without this subjective Y/N measure.

HARDWARE MAPPING
----------------
Pinky  -> SC0 -> A
Ring   -> SC1 -> S
Middle -> SC2 -> D
Index  -> SC3 -> F
Thumb  -> SC4 -> Space

SERIAL
------
REQUIRES the new matching Arduino firmware in this repository.
The old Arduino firmware SC0-SC4 does NOT support seconds-long pulses.
  v1.4 firmware also will be rejected: upload the matching v1.5 sketch.
New serial command format: PLAY,channel,duration_ms,rtp_level
Example: PLAY,0,1000,48

Default baud rate: 9600.
The firmware's READY response must be visible in the Arduino Serial Monitor
at 9600 baud during bench verification. The requested RTP value is not Vrms.
The program does not directly measure acceleration, velocity or frequency.

The program keeps the GUI responsive while connecting to the Arduino.

IMPORTANT TIMING NOTE
---------------------
ADJACENT_PAIR_GAP_SEC is the requested command-to-command gap between the first
and second adjacent-finger cues. The program logs the actual measured
command-to-command gap for every adjacent-pair trial so the delivered timing can
be audited after collection.

The default is 2.0 seconds. This value is intentionally configurable at the
top of the file.

The saved 235 Hz value is the nominal LRA operating frequency used by the
project. It is not a per-trial sensor measurement of the physical vibration at
the participant's skin.
"""

from __future__ import annotations

import csv
import json
import os
import random
import statistics
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError as exc:
    raise SystemExit("Tkinter is required to run this program.") from exc

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


# =============================================================================
# STUDY CONFIGURATION
# =============================================================================

SOFTWARE_VERSION = "VSPA_PE_Fall2026_SELF_TEST_v1.5"

SERIAL_BAUD = 9600
SERIAL_TIMEOUT_SEC = 0.05
SERIAL_WRITE_TIMEOUT_SEC = 0.50
ARDUINO_RESET_WAIT_SEC = 2.0

SIMULATION_MODE_DEFAULT = False

NOMINAL_FREQUENCY_HZ = 235.0

# Study timing
PRE_TRIAL_DELAY_SEC = 1.0
POST_STIMULUS_BEFORE_RESPONSE_SEC = 0.35
RESPONSE_TIMEOUT_SEC = 8.0
REST_BETWEEN_TRIALS_SEC = 2.00

# Critical adjacent-finger variable:
# delay from first motor command to second motor command
ADJACENT_PAIR_GAP_SEC = 2.00

# Current formal test structure
RUN_SINGLE_FINGER_DEFAULT = True
RUN_ADJACENT_PAIR_DEFAULT = True

# Exactly 15 formal trials per participant:
#   Stage 1: 5 fixed single-finger trials, pinky -> thumb
#   Stage 2: 5 randomized single-finger trials
#   Stage 3: 5 randomized adjacent-pair trials
FIXED_SINGLE_TRIALS = 5

# Pulse duration MUST be implemented by the matching RTP Arduino firmware.
# Python cannot lengthen a pre-programmed millisecond effect on older firmware.
STIMULUS_DURATION_SEC = 1.0  # per finger; increase cautiously after verification
RTP_DRIVE_VALUE = 48          # register command, NOT measured voltage or acceleration
assert 0.1 <= STIMULUS_DURATION_SEC <= 3.0
assert 0 <= RTP_DRIVE_VALUE <= 80
assert ADJACENT_PAIR_GAP_SEC >= STIMULUS_DURATION_SEC + 0.5, (
    "Keep at least 0.5 s silence between adjacent finger pulses."
)
RANDOM_SINGLE_TRIALS = 5
RANDOM_ADJACENT_PAIR_TRIALS = 5

# Optional subjective confounding/spread report.
ASK_CONFOUNDING_REPORT = True

OUTPUT_ROOT = Path("experiment_output")

FINGERS = ["pinky", "ring", "middle", "index", "thumb"]

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

DISPLAY_KEY_MAP = {
    "pinky": "A",
    "ring": "S",
    "middle": "D",
    "index": "F",
    "thumb": "SPACE",
}

KEY_TO_FINGER = {v: k for k, v in KEY_MAP.items()}

# Physical neighbors on the hand.
UNORDERED_ADJACENT_PAIRS = [
    ("pinky", "ring"),
    ("ring", "middle"),
    ("middle", "index"),
    ("index", "thumb"),
]

# Both temporal orders are intentionally tested.
ORDERED_ADJACENT_PAIRS = []
for left, right in UNORDERED_ADJACENT_PAIRS:
    ORDERED_ADJACENT_PAIRS.append((left, right))
    ORDERED_ADJACENT_PAIRS.append((right, left))


TRIAL_FIELDS = [
    "timestamp",
    "participant_id",
    "session_id",
    "software_version",
    "test_type",
    "pattern_name",
    "trial_number",
    "block_number",
    "repetition_number",
    "expected_finger_sequence",
    "expected_key_sequence",
    "expected_channel_sequence",
    "sent_command_sequence",
    "stimulus_count",
    "requested_pair_gap_sec",
    "actual_pair_gap_sec",
    "first_stimulus_perf_counter",
    "second_stimulus_perf_counter",
    "response_key_sequence",
    "response_finger_sequence",
    "response_length",
    "was_correct",
    "first_position_correct",
    "second_position_correct",
    "order_reversal",
    "duplicate_response",
    "first_key_latency_sec",
    "completion_latency_sec",
    "response_timeout",
    "confounding_or_spread_report",
    "arduino_reply_sequence",
    "technical_anomaly_flag",
    "researcher_note",
    "nominal_frequency_hz",
    "randomization_seed",
    "requested_stimulus_duration_sec",
    "requested_rtp_drive_value",
    "firmware_protocol",
    "timing_measurement_definition",
]


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class TrialSpec:
    test_type: str
    fingers: Tuple[str, ...]
    block_number: int
    repetition_number: int
    pattern_name: str


# =============================================================================
# SERIAL / HAPTIC DEVICE
# =============================================================================

class HapticDevice:
    """Serial transport with mandatory controller identity and stimulus ACK.

    v1.4 silently treated a successful USB write as a successful vibration.
    The Arduino can ignore a command, reject it with ERR, have the old
    firmware installed, or report missing mux channels. We must fail closed.
    """

    FIRMWARE_SIGNATURE = "READY,VSPA_PE_RTP_1.5,mask="

    def __init__(self, port: Optional[str], simulation: bool):
        self.port = port
        self.simulation = simulation
        self.ser = None
        self._lock = threading.Lock()
        self.firmware_response = "SIMULATION" if simulation else ""
        self.recent_events = []

    def _read_line_until(self, accepted: Tuple[str, ...], timeout_sec: float) -> str:
        """Wait for a matching firmware response or a meaningful error.

        Avoid reading only `in_waiting` once: USB serial delivery is asynchronous.
        """
        end = time.monotonic() + timeout_sec
        while time.monotonic() < end:
            raw = self.ser.readline()
            if raw:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                self.recent_events.append(line)
                if len(self.recent_events) > 80:
                    self.recent_events.pop(0)
                if line.startswith("ERR,"):
                    raise RuntimeError("Arduino refused command: " + line)
                if any(line.startswith(prefix) for prefix in accepted):
                    return line
            else:
                time.sleep(0.015)
        raise RuntimeError(
            "No expected acknowledgement from Arduino. Check the firmware "
            "version, selected serial port, motor-channel connections, and "
            "9600-baud settings. Recent replies: "
            + repr(self.recent_events[-6:])
        )

    def connect(self) -> None:
        if self.simulation:
            print("[SIMULATION] No device connected; no motor pulses occur.")
            return
        if serial is None:
            raise RuntimeError("pyserial is missing: python -m pip install pyserial")
        if not self.port:
            raise RuntimeError("No Arduino serial port selected")

        try:
            self.ser = serial.Serial(
                port=self.port, baudrate=SERIAL_BAUD,
                timeout=SERIAL_TIMEOUT_SEC,
                write_timeout=SERIAL_WRITE_TIMEOUT_SEC,
                xonxoff=False, rtscts=False, dsrdtr=False,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not open {self.port!r}: {exc}. Close the Arduino "
                "Serial Monitor and choose the correct USB port."
            ) from exc

        time.sleep(ARDUINO_RESET_WAIT_SEC)
        with self._lock:
            self.ser.reset_input_buffer()
            self.ser.write(b"PING\n")
            self.ser.flush()
            reply = self._read_line_until((self.FIRMWARE_SIGNATURE,), 1.5)
            mask = reply.split("mask=", 1)[1].strip()
            if mask != "11111":
                missing = [str(i) for i, value in enumerate(mask[:5]) if value != "1"]
                raise RuntimeError(
                    "Firmware v1.5 responded, but the following DRV2605L "
                    f"mux channels are not online: {', '.join(missing)}. "
                    "Check TCA9548A, motor controller power and I2C wiring. "
                    "Run the separate diagnostic console with SCAN."
                )
            self.firmware_response = reply
            print("[HARDWARE] " + reply)

    def close(self) -> None:
        with self._lock:
            if self.ser is not None:
                try:
                    self.ser.write(b"STOP\n")
                    self.ser.flush()
                except Exception:
                    pass
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None

    def _drain_input(self) -> None:
        if self.simulation or self.ser is None:
            return
        try:
            while self.ser.in_waiting:
                text = self.ser.readline().decode("utf-8", errors="replace").strip()
                if text:
                    self.recent_events.append(text)
        except Exception:
            pass

    def send_channel(self, finger: str) -> Tuple[str, str, float]:
        """Ask for a pulse and confirm START response; does NOT prove motion.

        A valid START means the firmware accepted the command and read back
        registers. Motor vibration and voltage still require physical checks.
        """
        channel = CHANNEL_MAP[finger]
        duration_ms = int(round(STIMULUS_DURATION_SEC * 1000))
        command = f"PLAY,{channel},{duration_ms},{RTP_DRIVE_VALUE}"
        if self.simulation:
            command_time = time.perf_counter()
            print(f"[SIM ONLY] {command}; motor is NOT being driven")
            return command, "SIMULATED", command_time
        with self._lock:
            if self.ser is None:
                raise RuntimeError("Serial device not connected")
            self._drain_input()
            command_time = time.perf_counter()
            self.ser.write((command + "\n").encode("ascii"))
            self.ser.flush()
            reply = self._read_line_until((f"START,{channel},",), 0.9)
            return command, reply, command_time


# =============================================================================
# SCHEDULE GENERATION
# =============================================================================

def shuffled_copy(items: List[Any], rng: random.Random) -> List[Any]:
    result = list(items)
    rng.shuffle(result)
    return result


def build_schedule(
    rng: random.Random,
    run_single_finger: bool,
    run_adjacent_pair: bool,
) -> List[TrialSpec]:
    """
    Build exactly 15 formal trials when both study sections are enabled.

    Stage 1: five fixed single-finger trials:
        pinky -> ring -> middle -> index -> thumb

    Stage 2: five randomized single-finger trials.
        Each trial independently selects one finger.
        Repeats are allowed.

    Stage 3: five randomized adjacent-pair trials.
        Each trial independently selects one ordered neighboring pair.
        Repeats are allowed.

    The complete multi-finger sequence block remains excluded.
    """
    schedule: List[TrialSpec] = []
    block_number = 0

    if run_single_finger:
        # Stage 1: fixed baseline from pinky to thumb.
        block_number += 1
        for trial_number, finger in enumerate(FINGERS, start=1):
            schedule.append(
                TrialSpec(
                    test_type="single_finger_fixed",
                    fingers=(finger,),
                    block_number=block_number,
                    repetition_number=trial_number,
                    pattern_name=f"fixed_single_{trial_number:02d}_{finger}",
                )
            )

        # Stage 2: five random single-finger trials.
        block_number += 1
        for trial_number in range(1, RANDOM_SINGLE_TRIALS + 1):
            finger = rng.choice(FINGERS)
            schedule.append(
                TrialSpec(
                    test_type="single_finger_random",
                    fingers=(finger,),
                    block_number=block_number,
                    repetition_number=trial_number,
                    pattern_name=f"random_single_{trial_number:02d}_{finger}",
                )
            )

    if run_adjacent_pair:
        # Stage 3: five random ordered adjacent-finger trials.
        block_number += 1
        for trial_number in range(1, RANDOM_ADJACENT_PAIR_TRIALS + 1):
            first, second = rng.choice(ORDERED_ADJACENT_PAIRS)
            schedule.append(
                TrialSpec(
                    test_type="adjacent_pair_random",
                    fingers=(first, second),
                    block_number=block_number,
                    repetition_number=trial_number,
                    pattern_name=(
                        f"random_adjacent_{trial_number:02d}_"
                        f"{first}_then_{second}"
                    ),
                )
            )

    return schedule


# =============================================================================
# DATA OUTPUT
# =============================================================================

class DataStore:
    def __init__(self, participant_id: str, session_id: str, output_dir: Path):
        self.participant_id = participant_id
        self.session_id = session_id
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_pid = "".join(
            ch for ch in participant_id if ch.isalnum() or ch in ("-", "_")
        ) or "participant"

        self.trial_log_path = (
            output_dir / f"{safe_pid}_trial_log_{stamp}.csv"
        )
        self.single_summary_path = (
            output_dir / f"{safe_pid}_single_finger_summary_{stamp}.csv"
        )
        self.pair_summary_path = (
            output_dir / f"{safe_pid}_adjacent_pair_summary_{stamp}.csv"
        )
        self.overall_summary_path = (
            output_dir / f"{safe_pid}_overall_summary_{stamp}.csv"
        )
        self.metadata_path = (
            output_dir / f"{safe_pid}_session_metadata_{stamp}.json"
        )

        self.rows: List[Dict[str, Any]] = []

        self._trial_file = self.trial_log_path.open(
            "w", newline="", encoding="utf-8"
        )
        self._writer = csv.DictWriter(
            self._trial_file,
            fieldnames=TRIAL_FIELDS,
        )
        self._writer.writeheader()
        self._trial_file.flush()

    def append_trial(self, row: Dict[str, Any]) -> None:
        safe = {field: row.get(field, "") for field in TRIAL_FIELDS}
        self.rows.append(safe)
        self._writer.writerow(safe)
        self._trial_file.flush()

    def save_metadata(self, metadata: Dict[str, Any]) -> None:
        with self.metadata_path.open("w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

    def close_trial_file(self) -> None:
        if not self._trial_file.closed:
            self._trial_file.flush()
            self._trial_file.close()

    @staticmethod
    def _bool(value: Any) -> bool:
        return str(value).strip().lower() in {"true", "1", "yes", "y"}

    @staticmethod
    def _mean(values: List[Any]) -> str:
        cleaned = []
        for value in values:
            if value in ("", None):
                continue
            try:
                cleaned.append(float(value))
            except (TypeError, ValueError):
                continue
        if not cleaned:
            return ""
        return f"{statistics.mean(cleaned):.4f}"

    @classmethod
    def _rate(cls, values: List[Any]) -> str:
        if not values:
            return ""
        nums = [1 if cls._bool(v) else 0 for v in values]
        return f"{statistics.mean(nums):.4f}"

    def write_summaries(self) -> None:
        self.close_trial_file()

        single_rows = [
            r for r in self.rows if r["test_type"] in {"single_finger_fixed", "single_finger_random"}
        ]
        pair_rows = [
            r for r in self.rows if r["test_type"] == "adjacent_pair_random"
        ]

        # ---------------------------------------------------------------------
        # Single-finger summary by target finger
        # ---------------------------------------------------------------------
        single_fields = [
            "participant_id",
            "session_id",
            "finger",
            "channel",
            "trials",
            "correct_trials",
            "accuracy",
            "spread_report_rate",
            "mean_response_latency_sec",
            "response_timeout_count",
        ]

        with self.single_summary_path.open(
            "w", newline="", encoding="utf-8"
        ) as f:
            writer = csv.DictWriter(f, fieldnames=single_fields)
            writer.writeheader()

            for finger in FINGERS:
                group = [
                    r for r in single_rows
                    if r["expected_finger_sequence"] == finger
                ]
                if not group:
                    continue

                correct = sum(self._bool(r["was_correct"]) for r in group)
                spread_values = [
                    r["confounding_or_spread_report"]
                    for r in group
                    if r["confounding_or_spread_report"] != ""
                ]

                writer.writerow({
                    "participant_id": self.participant_id,
                    "session_id": self.session_id,
                    "finger": finger,
                    "channel": CHANNEL_MAP[finger],
                    "trials": len(group),
                    "correct_trials": correct,
                    "accuracy": f"{correct / len(group):.4f}",
                    "spread_report_rate": self._rate(spread_values),
                    "mean_response_latency_sec": self._mean(
                        [r["first_key_latency_sec"] for r in group]
                    ),
                    "response_timeout_count": sum(
                        self._bool(r["response_timeout"]) for r in group
                    ),
                })

        # ---------------------------------------------------------------------
        # Adjacent-pair summary by ORDERED pair
        # ---------------------------------------------------------------------
        pair_fields = [
            "participant_id",
            "session_id",
            "ordered_pair",
            "channel_order",
            "requested_pair_gap_sec",
            "mean_actual_pair_gap_sec",
            "trials",
            "exact_correct_trials",
            "exact_accuracy",
            "first_position_accuracy",
            "second_position_accuracy",
            "order_reversal_rate",
            "duplicate_response_rate",
            "confounding_report_rate",
            "mean_first_key_latency_sec",
            "mean_completion_latency_sec",
            "response_timeout_count",
        ]

        with self.pair_summary_path.open(
            "w", newline="", encoding="utf-8"
        ) as f:
            writer = csv.DictWriter(f, fieldnames=pair_fields)
            writer.writeheader()

            for first, second in ORDERED_ADJACENT_PAIRS:
                expected = f"{first}-{second}"
                group = [
                    r for r in pair_rows
                    if r["expected_finger_sequence"] == expected
                ]
                if not group:
                    continue

                exact = sum(self._bool(r["was_correct"]) for r in group)
                first_correct = sum(
                    self._bool(r["first_position_correct"]) for r in group
                )
                second_correct = sum(
                    self._bool(r["second_position_correct"]) for r in group
                )
                reversals = [
                    r["order_reversal"] for r in group
                ]
                duplicates = [
                    r["duplicate_response"] for r in group
                ]
                confounding_values = [
                    r["confounding_or_spread_report"]
                    for r in group
                    if r["confounding_or_spread_report"] != ""
                ]

                writer.writerow({
                    "participant_id": self.participant_id,
                    "session_id": self.session_id,
                    "ordered_pair": expected,
                    "channel_order": (
                        f"SC{CHANNEL_MAP[first]}->SC{CHANNEL_MAP[second]}"
                    ),
                    "requested_pair_gap_sec": ADJACENT_PAIR_GAP_SEC,
                    "mean_actual_pair_gap_sec": self._mean(
                        [r["actual_pair_gap_sec"] for r in group]
                    ),
                    "trials": len(group),
                    "exact_correct_trials": exact,
                    "exact_accuracy": f"{exact / len(group):.4f}",
                    "first_position_accuracy": (
                        f"{first_correct / len(group):.4f}"
                    ),
                    "second_position_accuracy": (
                        f"{second_correct / len(group):.4f}"
                    ),
                    "order_reversal_rate": self._rate(reversals),
                    "duplicate_response_rate": self._rate(duplicates),
                    "confounding_report_rate": self._rate(
                        confounding_values
                    ),
                    "mean_first_key_latency_sec": self._mean(
                        [r["first_key_latency_sec"] for r in group]
                    ),
                    "mean_completion_latency_sec": self._mean(
                        [r["completion_latency_sec"] for r in group]
                    ),
                    "response_timeout_count": sum(
                        self._bool(r["response_timeout"]) for r in group
                    ),
                })

        # ---------------------------------------------------------------------
        # Overall summary
        # ---------------------------------------------------------------------
        overall_fields = [
            "participant_id",
            "session_id",
            "software_version",
            "total_trials",
            "single_finger_trials",
            "single_finger_accuracy",
            "single_finger_spread_report_rate",
            "adjacent_pair_trials",
            "adjacent_pair_exact_accuracy",
            "adjacent_pair_first_position_accuracy",
            "adjacent_pair_second_position_accuracy",
            "adjacent_pair_order_reversal_rate",
            "adjacent_pair_duplicate_response_rate",
            "adjacent_pair_confounding_report_rate",
            "mean_actual_pair_gap_sec",
            "response_timeout_count",
        ]

        def accuracy(rows: List[Dict[str, Any]]) -> str:
            if not rows:
                return ""
            correct = sum(self._bool(r["was_correct"]) for r in rows)
            return f"{correct / len(rows):.4f}"

        single_spread = [
            r["confounding_or_spread_report"]
            for r in single_rows
            if r["confounding_or_spread_report"] != ""
        ]
        pair_conf = [
            r["confounding_or_spread_report"]
            for r in pair_rows
            if r["confounding_or_spread_report"] != ""
        ]

        with self.overall_summary_path.open(
            "w", newline="", encoding="utf-8"
        ) as f:
            writer = csv.DictWriter(f, fieldnames=overall_fields)
            writer.writeheader()

            writer.writerow({
                "participant_id": self.participant_id,
                "session_id": self.session_id,
                "software_version": SOFTWARE_VERSION,
                "total_trials": len(self.rows),
                "single_finger_trials": len(single_rows),
                "single_finger_accuracy": accuracy(single_rows),
                "single_finger_spread_report_rate": self._rate(
                    single_spread
                ),
                "adjacent_pair_trials": len(pair_rows),
                "adjacent_pair_exact_accuracy": accuracy(pair_rows),
                "adjacent_pair_first_position_accuracy": (
                    self._rate(
                        [r["first_position_correct"] for r in pair_rows]
                    )
                ),
                "adjacent_pair_second_position_accuracy": (
                    self._rate(
                        [r["second_position_correct"] for r in pair_rows]
                    )
                ),
                "adjacent_pair_order_reversal_rate": (
                    self._rate(
                        [r["order_reversal"] for r in pair_rows]
                    )
                ),
                "adjacent_pair_duplicate_response_rate": (
                    self._rate(
                        [r["duplicate_response"] for r in pair_rows]
                    )
                ),
                "adjacent_pair_confounding_report_rate": (
                    self._rate(pair_conf)
                ),
                "mean_actual_pair_gap_sec": self._mean(
                    [r["actual_pair_gap_sec"] for r in pair_rows]
                ),
                "response_timeout_count": sum(
                    self._bool(r["response_timeout"])
                    for r in self.rows
                ),
            })


# =============================================================================
# GUI
# =============================================================================

class VSPAPEApp:
    def __init__(
        self,
        root: tk.Tk,
        participant_id: str,
        device: HapticDevice,
        datastore: DataStore,
        schedule: List[TrialSpec],
        random_seed: int,
    ):
        self.root = root
        self.participant_id = participant_id
        self.device = device
        self.datastore = datastore
        self.schedule = schedule
        self.random_seed = random_seed

        self.current_index = -1
        self.current_trial: Optional[TrialSpec] = None
        self.state = "instructions"
        self.session_stopped = False

        self.first_stimulus_perf_counter: Optional[float] = None
        self.second_stimulus_perf_counter: Optional[float] = None

        self.sent_commands: List[str] = []
        self.arduino_replies: List[str] = []

        self.response_keys: List[str] = []
        self.response_key_times: List[float] = []
        self.response_timeout = False
        self.confounding_report: Optional[bool] = None

        self.technical_anomaly_flag = False
        self.researcher_note = ""

        self.timeout_after_id = None

        self.progress_var = tk.StringVar()
        self.heading_var = tk.StringVar()
        self.prompt_var = tk.StringVar()
        self.detail_var = tk.StringVar()
        self.response_status_var = tk.StringVar()

        self._build_ui()
        self.root.bind("<KeyPress>", self.on_keypress)
        self.root.protocol("WM_DELETE_WINDOW", self.request_stop)
        self.show_instructions()

    def _build_ui(self) -> None:
        self.root.title(
            "VSPA:PE - Single-Finger and Adjacent-Finger Confounding Study"
        )
        self.root.geometry("1000x760")
        self.root.minsize(880, 650)

        outer = tk.Frame(self.root, padx=35, pady=25)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            textvariable=self.progress_var,
            font=("Arial", 11),
            anchor="e",
        ).pack(fill="x")

        tk.Label(
            outer,
            textvariable=self.heading_var,
            font=("Arial", 24, "bold"),
            pady=18,
            wraplength=900,
        ).pack(fill="x")

        tk.Label(
            outer,
            textvariable=self.prompt_var,
            font=("Arial", 18),
            pady=15,
            wraplength=900,
            justify="center",
        ).pack(fill="both", expand=True)

        tk.Label(
            outer,
            textvariable=self.detail_var,
            font=("Arial", 13),
            pady=8,
            wraplength=900,
            justify="center",
        ).pack(fill="x")

        tk.Label(
            outer,
            textvariable=self.response_status_var,
            font=("Arial", 12, "bold"),
            pady=6,
        ).pack(fill="x")

        self.action_button = tk.Button(
            outer,
            text="Begin Session",
            font=("Arial", 14, "bold"),
            width=24,
            command=self.begin_session,
        )
        self.action_button.pack(pady=12)

        self.stop_button = tk.Button(
            outer,
            text="Stop Session",
            font=("Arial", 11),
            command=self.request_stop,
        )
        self.stop_button.pack(pady=4)

    def show_instructions(self) -> None:
        self.state = "instructions"
        self.progress_var.set("")
        self.heading_var.set(
            "VSPA:PE Single-Finger + Adjacent-Finger Study"
        )
        self.prompt_var.set(
            "Respond using the following finger-to-key mapping:\n\n"
            "Pinky = A     Ring = S     Middle = D     "
            "Index = F     Thumb = SPACE"
        )
        self.detail_var.set(
            "Single-finger trials require ONE key.\n\n"
            "Adjacent-finger trials contain TWO separated, seconds-long vibrations on neighboring "
            "fingers. Enter TWO keys in the order you perceived the vibrations.\n\n"
            "The program will not tell you whether a response was correct."
        )
        self.response_status_var.set("")
        self.action_button.config(
            text="Begin Session",
            state="normal",
            command=self.begin_session,
        )

    def begin_session(self) -> None:
        self.action_button.config(state="disabled")
        self.next_trial()

    def _reset_trial_state(self) -> None:
        self.first_stimulus_perf_counter = None
        self.second_stimulus_perf_counter = None

        self.sent_commands = []
        self.arduino_replies = []

        self.response_keys = []
        self.response_key_times = []
        self.response_timeout = False
        self.confounding_report = None

        self.technical_anomaly_flag = False
        self.researcher_note = ""

        if self.timeout_after_id is not None:
            try:
                self.root.after_cancel(self.timeout_after_id)
            except Exception:
                pass
            self.timeout_after_id = None

    def next_trial(self) -> None:
        if self.session_stopped:
            return

        self.current_index += 1

        if self.current_index >= len(self.schedule):
            self.finish_session()
            return

        self.current_trial = self.schedule[self.current_index]
        self._reset_trial_state()

        self.state = "pre_trial"        self.progress_var.set(
            f"Trial {self.current_index + 1} of {len(self.schedule)}"
        )

        if self.current_trial.test_type in {"single_finger_fixed", "single_finger_random"}:
            self.heading_var.set("Single-Finger Test")
            self.detail_var.set(
                "One finger will vibrate. Identify the finger."
            )
        else:
            self.heading_var.set("Adjacent-Finger Test")
            self.detail_var.set(
                "Two neighboring fingers will vibrate one after the other. "
                "Identify both fingers in the order you perceived them."
            )

        self.prompt_var.set("Prepare for the next vibration.")
        self.response_status_var.set("")
        self.action_button.config(state="disabled")

        self.root.after(
            int(PRE_TRIAL_DELAY_SEC * 1000),
            self.deliver_first_stimulus,
        )

    def deliver_first_stimulus(self) -> None:
        if self.session_stopped or self.current_trial is None:
            return

        first_finger = self.current_trial.fingers[0]

        self.heading_var.set("Vibration")
        self.prompt_var.set("Attend to the vibration.")
        self.detail_var.set("")
        self.response_status_var.set("")

        try:
            (
                command,
                reply,
                command_time,
            ) = self.device.send_channel(first_finger)

            self.sent_commands.append(command)
            self.arduino_replies.append(reply)
            self.first_stimulus_perf_counter = command_time

        except Exception as exc:
            self.technical_anomaly_flag = True
            self.researcher_note = f"First stimulus error: {exc}"
            messagebox.showerror(
                "Hardware Error",
                f"Could not deliver the first stimulus:\n\n{exc}",
                parent=self.root,
            )
            self.request_stop(force=True)
            return

        if len(self.current_trial.fingers) == 1:
            self.root.after(
                int((STIMULUS_DURATION_SEC + POST_STIMULUS_BEFORE_RESPONSE_SEC) * 1000),
                self.enable_response,
            )
        else:
            # Tkinter schedules the second command without blocking the UI.
            self.root.after(
                int(ADJACENT_PAIR_GAP_SEC * 1000),
                self.deliver_second_stimulus,
            )

    def deliver_second_stimulus(self) -> None:
        if self.session_stopped or self.current_trial is None:
            return

        second_finger = self.current_trial.fingers[1]

        try:
            (
                command,
                reply,
                command_time,
            ) = self.device.send_channel(second_finger)

            self.sent_commands.append(command)
            self.arduino_replies.append(reply)
            self.second_stimulus_perf_counter = command_time

        except Exception as exc:
            self.technical_anomaly_flag = True
            self.researcher_note = f"Second stimulus error: {exc}"
            messagebox.showerror(
                "Hardware Error",
                f"Could not deliver the second stimulus:\n\n{exc}",
                parent=self.root,
            )
            self.request_stop(force=True)
            return

        self.root.after(
            int((STIMULUS_DURATION_SEC + POST_STIMULUS_BEFORE_RESPONSE_SEC) * 1000),
            self.enable_response,
        )

    def enable_response(self) -> None:
        if self.session_stopped or self.current_trial is None:
            return

        self.state = "waiting_response"

        expected_count = len(self.current_trial.fingers)

        if expected_count == 1:
            self.heading_var.set("Which finger did you feel?")
            self.prompt_var.set(
                "Pinky = A     Ring = S     Middle = D     "
                "Index = F     Thumb = SPACE"
            )
            self.detail_var.set(
                f"Enter ONE key within {RESPONSE_TIMEOUT_SEC:.0f} seconds."
            )
        else:
            self.heading_var.set("Which two fingers did you feel?")
            self.prompt_var.set(
                "Enter TWO keys in the order you perceived the cues.\n\n"
                "Pinky = A     Ring = S     Middle = D     "
                "Index = F     Thumb = SPACE"
            )
            self.detail_var.set(
                f"Enter TWO keys within {RESPONSE_TIMEOUT_SEC:.0f} seconds."
            )

        self.response_status_var.set(
            f"Responses entered: 0 of {expected_count}"
        )

        self.timeout_after_id = self.root.after(
            int(RESPONSE_TIMEOUT_SEC * 1000),
            self.handle_response_timeout,
        )

    def handle_response_timeout(self) -> None:
        if self.state != "waiting_response":
            return

        self.response_timeout = True
        self.timeout_after_id = None
        self.after_keyboard_response()

    @staticmethod
    def _normalize_key(event: tk.Event) -> Optional[str]:
        key = event.keysym.lower()

        if key == "space":
            return "space"

        if key in {"a", "s", "d", "f"}:
            return key

        return None

    def on_keypress(self, event: tk.Event) -> None:
        if self.session_stopped:
            return

        key = event.keysym.lower()

        if key == "escape":
            self.request_stop()
            return

        if self.state == "waiting_response":
            normalized = self._normalize_key(event)
            if normalized is None:
                return

            expected_count = len(self.current_trial.fingers)

            if len(self.response_keys) >= expected_count:
                return

            self.response_keys.append(normalized)
            self.response_key_times.append(time.perf_counter())

            self.response_status_var.set(
                f"Responses entered: {len(self.response_keys)} "
                f"of {expected_count}"
            )

            if len(self.response_keys) == expected_count:
                if self.timeout_after_id is not None:
                    try:
                        self.root.after_cancel(self.timeout_after_id)
                    except Exception:
                        pass
                    self.timeout_after_id = None

                self.after_keyboard_response()
            return

        if self.state == "waiting_confounding":
            if key in {"y", "n"}:
                self.confounding_report = key == "y"
                self.save_trial_and_continue()
            return

    def after_keyboard_response(self) -> None:
        if ASK_CONFOUNDING_REPORT:
            self.ask_confounding()
        else:
            self.save_trial_and_continue()

    def ask_confounding(self) -> None:
        if self.current_trial is None:
            return

        self.state = "waiting_confounding"

        if self.current_trial.test_type in {"single_finger_fixed", "single_finger_random"}:
            self.heading_var.set("Vibration spread")
            self.prompt_var.set(
                "Did the vibration seem to spread to another finger?"
            )
            self.detail_var.set("Press Y for Yes or N for No.")
        else:
            self.heading_var.set("Cue separation")
            self.prompt_var.set(
                "Did the two vibrations feel blended, overlapping, "
                "or confounded rather than clearly separate?"
            )
            self.detail_var.set("Press Y for Yes or N for No.")

        self.response_status_var.set("")

    def save_trial_and_continue(self) -> None:
        if self.current_trial is None:
            return

        expected_fingers = list(self.current_trial.fingers)
        expected_keys = [KEY_MAP[f] for f in expected_fingers]
        expected_channels = [CHANNEL_MAP[f] for f in expected_fingers]

        response_fingers = [
            KEY_TO_FINGER[key]
            for key in self.response_keys
            if key in KEY_TO_FINGER
        ]

        was_correct = response_fingers == expected_fingers

        first_position_correct = (
            len(response_fingers) >= 1
            and response_fingers[0] == expected_fingers[0]
        )

        second_position_correct: Any = ""
        order_reversal: Any = ""
        duplicate_response: Any = ""

        if len(expected_fingers) == 2:
            second_position_correct = (
                len(response_fingers) >= 2
                and response_fingers[1] == expected_fingers[1]
            )

            order_reversal = (
                len(response_fingers) == 2
                and response_fingers
                == [expected_fingers[1], expected_fingers[0]]
            )

            duplicate_response = (
                len(response_fingers) == 2
                and response_fingers[0] == response_fingers[1]
            )

        first_key_latency: Any = ""
        completion_latency: Any = ""

        if (
            self.first_stimulus_perf_counter is not None
            and self.response_key_times
        ):
            first_key_latency = round(
                self.response_key_times[0]
                - self.first_stimulus_perf_counter,
                4,
            )
            completion_latency = round(
                self.response_key_times[-1]
                - self.first_stimulus_perf_counter,
                4,
            )

        actual_pair_gap: Any = ""
        if (
            self.first_stimulus_perf_counter is not None
            and self.second_stimulus_perf_counter is not None
        ):
            actual_pair_gap = round(
                self.second_stimulus_perf_counter
                - self.first_stimulus_perf_counter,
                6,
            )

        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "participant_id": self.participant_id,
            "session_id": self.datastore.session_id,
            "software_version": SOFTWARE_VERSION,
            "test_type": self.current_trial.test_type,
            "pattern_name": self.current_trial.pattern_name,
            "trial_number": self.current_index + 1,
            "block_number": self.current_trial.block_number,
            "repetition_number": self.current_trial.repetition_number,
            "expected_finger_sequence": "-".join(expected_fingers),
            "expected_key_sequence": "-".join(expected_keys),
            "expected_channel_sequence": "-".join(
                str(ch) for ch in expected_channels
            ),
            "sent_command_sequence": "|".join(self.sent_commands),
            "stimulus_count": len(expected_fingers),
            "requested_pair_gap_sec": (
                ADJACENT_PAIR_GAP_SEC
                if len(expected_fingers) == 2
                else ""
            ),
            "actual_pair_gap_sec": actual_pair_gap,
            "first_stimulus_perf_counter": (
                round(self.first_stimulus_perf_counter, 6)
                if self.first_stimulus_perf_counter is not None
                else ""
            ),
            "second_stimulus_perf_counter": (
                round(self.second_stimulus_perf_counter, 6)
                if self.second_stimulus_perf_counter is not None
                else ""
            ),
            "response_key_sequence": "-".join(self.response_keys),
            "response_finger_sequence": "-".join(response_fingers),
            "response_length": len(self.response_keys),
            "was_correct": was_correct,
            "first_position_correct": first_position_correct,
            "second_position_correct": second_position_correct,
            "order_reversal": order_reversal,
            "duplicate_response": duplicate_response,
            "first_key_latency_sec": first_key_latency,
            "completion_latency_sec": completion_latency,
            "response_timeout": self.response_timeout,
            "confounding_or_spread_report": (
                self.confounding_report
                if self.confounding_report is not None
                else ""
            ),
            "arduino_reply_sequence": "|".join(
                reply for reply in self.arduino_replies if reply
            ),
            "technical_anomaly_flag": self.technical_anomaly_flag,
            "researcher_note": self.researcher_note,
            "nominal_frequency_hz": NOMINAL_FREQUENCY_HZ,
            "randomization_seed": self.random_seed,
            "requested_stimulus_duration_sec": STIMULUS_DURATION_SEC,
            "requested_rtp_drive_value": RTP_DRIVE_VALUE,
            "firmware_protocol": "v1.5:PLAY,channel,duration_ms,rtp_level",
            "timing_measurement_definition": "host command issuance only, not sensor measured vibration",
        }

        self.datastore.append_trial(row)

        self.state = "rest"
        self.heading_var.set("Response recorded")
        self.prompt_var.set("Please relax your hand before the next trial.")
        self.detail_var.set("")
        self.response_status_var.set("")

        self.root.after(
            int(REST_BETWEEN_TRIALS_SEC * 1000),
            self.next_trial,
        )

    def request_stop(self, force: bool = False) -> None:
        if self.session_stopped:
            return

        if not force:
            should_stop = messagebox.askyesno(
                "Stop Session",
                "Stop the session now?\n\n"
                "All completed trials will still be saved.",
                parent=self.root,
            )
            if not should_stop:
                return

        self.session_stopped = True

        if self.timeout_after_id is not None:
            try:
                self.root.after_cancel(self.timeout_after_id)
            except Exception:
                pass
            self.timeout_after_id = None

        self.finalize_outputs(partial=True)

    def finish_session(self) -> None:
        self.session_stopped = True
        self.finalize_outputs(partial=False)

    def finalize_outputs(self, partial: bool) -> None:
        try:
            self.datastore.write_summaries()
        except Exception as exc:
            try:
                self.datastore.close_trial_file()
            except Exception:
                pass

            messagebox.showerror(
                "Output Error",
                "The raw trial log was saved, but one or more summary "
                f"files could not be generated:\n\n{exc}",
                parent=self.root,
            )

        self.device.close()

        self.state = "finished"
        self.progress_var.set("")
        self.heading_var.set(
            "Session stopped" if partial else "Session complete"
        )
        self.prompt_var.set(
            f"Completed trials saved: {len(self.datastore.rows)}\n\n"
            f"Output directory:\n"
            f"{self.datastore.output_dir.resolve()}"
        )
        self.detail_var.set(
            "Saved outputs include the raw trial log, single-finger summary, "
            "adjacent-pair summary, overall summary, and session metadata."
        )
        self.response_status_var.set("")

        self.action_button.config(
            text="Close",
            state="normal",
            command=self.root.destroy,
        )
        self.stop_button.config(state="disabled")


# =============================================================================
# STARTUP GUI
# =============================================================================

def available_port_records() -> List[Tuple[str, str]]:
    if list_ports is None:
        return []

    records = []

    for port in list_ports.comports():
        device = port.device or ""
        description = port.description or ""
        label = f"{device} — {description}" if description else device
        records.append((device, label))

    def score(record: Tuple[str, str]) -> Tuple[int, str]:
        text = (record[0] + " " + record[1]).lower()

        likely_usb = any(
            token in text
            for token in (
                "usbmodem",
                "usbserial",
                "arduino",
                "wchusbserial",
                "ttyacm",
                "com",
            )
        )
        bluetooth = "bluetooth" in text

        if likely_usb and not bluetooth:
            rank = 0
        elif not bluetooth:
            rank = 1
        else:
            rank = 2

        return rank, text

    records.sort(key=score)
    return records


class SetupWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.result: Optional[Dict[str, Any]] = None

        self.participant_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.simulation_var = tk.BooleanVar(
            value=SIMULATION_MODE_DEFAULT
        )
        self.single_var = tk.BooleanVar(
            value=RUN_SINGLE_FINGER_DEFAULT
        )
        self.pair_var = tk.BooleanVar(
            value=RUN_ADJACENT_PAIR_DEFAULT
        )
        self.seed_var = tk.StringVar(
            value=str(
                random.SystemRandom().randint(
                    1,
                    2_147_483_647,
                )
            )
        )

        self.port_map: Dict[str, str] = {}

        self._build()
        self.refresh_ports()

    def _build(self) -> None:
        self.root.title("VSPA:PE Session Setup")
        self.root.geometry("760x610")
        self.root.minsize(700, 560)

        frame = tk.Frame(self.root, padx=30, pady=25)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text="VSPA:PE Session Setup",
            font=("Arial", 20, "bold"),
        ).grid(
            row=0,
            column=0,
            columnspan=3,
            pady=(0, 20),
        )

        tk.Label(
            frame,
            text="Participant ID:",
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=7)

        participant_entry = tk.Entry(
            frame,
            textvariable=self.participant_var,
            width=35,
        )
        participant_entry.grid(
            row=1,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=7,
        )

        tk.Label(
            frame,
            text="Arduino serial port:",
            anchor="w",
        ).grid(row=2, column=0, sticky="w", pady=7)

        self.port_combo = ttk.Combobox(
            frame,
            textvariable=self.port_var,
            state="readonly",
            width=48,
        )
        self.port_combo.grid(
            row=2,
            column=1,
            sticky="ew",
            pady=7,
        )

        tk.Button(
            frame,
            text="Refresh",
            command=self.refresh_ports,
            width=10,
        ).grid(row=2, column=2, padx=(8, 0), pady=7)

        tk.Checkbutton(
            frame,
            text="Simulation Mode (do not send commands to Arduino)",
            variable=self.simulation_var,
        ).grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(10, 5),
        )

        tk.Label(
            frame,
            text="Formal blocks:",
            font=("Arial", 11, "bold"),
        ).grid(
            row=4,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(18, 5),
        )

        tk.Checkbutton(
            frame,
            text=(
                "Single-finger testing "
                "(5 fixed pinky-to-thumb + 5 random = 10 trials)"
            ),
            variable=self.single_var,
        ).grid(
            row=5,
            column=0,
            columnspan=3,
            sticky="w",
            pady=4,
        )

        pair_trials = RANDOM_ADJACENT_PAIR_TRIALS

        tk.Checkbutton(
            frame,
            text=(
                f"Adjacent-finger confounding "
                f"({pair_trials} random adjacent-pair trials)"
            ),
            variable=self.pair_var,
        ).grid(
            row=6,
            column=0,
            columnspan=3,
            sticky="w",
            pady=4,
        )

        tk.Label(
            frame,
            text=(
                f"Pulse: {STIMULUS_DURATION_SEC:.2f} s | "
                f"start-to-start adjacent gap: {ADJACENT_PAIR_GAP_SEC:.2f} s | "
                f"RTP level (not volts): {RTP_DRIVE_VALUE}"
            ),
        ).grid(
            row=7,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 2),
        )

        tk.Label(
            frame,
            text=(
                "Random pair trials select from both orders of these neighbor pairs: "
                "pinky/ring, ring/middle, middle/index, index/thumb."
            ),
            wraplength=650,
            justify="left",
        ).grid(
            row=8,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(2, 12),
        )

        tk.Label(
            frame,
            text="Randomization seed:",
        ).grid(row=9, column=0, sticky="w", pady=7)

        tk.Entry(
            frame,
            textvariable=self.seed_var,
            width=35,
        ).grid(
            row=9,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=7,
        )

        tk.Label(
            frame,
            text=(
                "This version does not ask for comfort or perceived intensity. "
                + (
                    "It asks only a Y/N spread/confounding question after the "
                    "keyboard response."
                    if ASK_CONFOUNDING_REPORT
                    else "It uses keyboard responses only."
                )
            ),
            wraplength=650,
            justify="left",
        ).grid(
            row=10,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(15, 15),
        )

        button_frame = tk.Frame(frame)
        button_frame.grid(
            row=11,
            column=0,
            columnspan=3,
            pady=15,
        )

        tk.Button(
            button_frame,
            text="Start",
            width=16,
            font=("Arial", 12, "bold"),
            command=self.start,
        ).pack(side="left", padx=8)

        tk.Button(
            button_frame,
            text="Cancel",
            width=16,
            command=self.cancel,
        ).pack(side="left", padx=8)

        frame.columnconfigure(1, weight=1)

        participant_entry.focus_set()

    def refresh_ports(self) -> None:
        records = available_port_records()

        labels = []
        self.port_map = {}

        for device, label in records:
            labels.append(label)
            self.port_map[label] = device

        self.port_combo["values"] = labels

        if labels:
            self.port_var.set(labels[0])
        else:
            self.port_var.set("")

    def start(self) -> None:
        pid = self.participant_var.get().strip()

        if not pid:
            messagebox.showerror(
                "Missing Participant ID",
                "Enter a participant ID.",
                parent=self.root,
            )
            return

        if not self.single_var.get() and not self.pair_var.get():
            messagebox.showerror(
                "No Test Selected",
                "Select at least one formal test block.",
                parent=self.root,
            )
            return

        try:
            seed = int(self.seed_var.get().strip())
        except ValueError:
            messagebox.showerror(
                "Invalid Randomization Seed",
                "The randomization seed must be an integer.",
                parent=self.root,
            )
            return

        simulation = self.simulation_var.get()

        port_label = self.port_var.get().strip()
        port = self.port_map.get(port_label, port_label or None)

        if not simulation and not port:
            messagebox.showerror(
                "Arduino Port Required",
                "Select the Arduino serial port or enable Simulation Mode.",
                parent=self.root,
            )
            return

        self.result = {
            "participant_id": pid,
            "port": port,
            "simulation": simulation,
            "run_single": self.single_var.get(),
            "run_pair": self.pair_var.get(),
            "seed": seed,
        }

        self.root.quit()

    def cancel(self) -> None:
        self.result = None
        self.root.quit()


class ConnectingWindow:
    def __init__(self, root: tk.Tk, port: Optional[str], simulation: bool):
        self.root = root

        for widget in root.winfo_children():
            widget.destroy()

        root.title("VSPA:PE - Connecting")
        root.geometry("700x360")

        frame = tk.Frame(root, padx=35, pady=35)
        frame.pack(fill="both", expand=True)

        tk.Label(
            frame,
            text="Connecting to the haptic controller",
            font=("Arial", 20, "bold"),
            pady=20,
        ).pack()

        message = (
            "Simulation Mode is enabled. Preparing the experiment..."
            if simulation
            else (
                f"Opening {port} at {SERIAL_BAUD} baud.\n\n"
                "The Arduino Uno may reset for approximately two seconds.\nThe program will verify firmware v1.5 and all five channels."
            )
        )

        tk.Label(
            frame,
            text=message,
            font=("Arial", 13),
            wraplength=600,
            justify="center",
        ).pack(pady=15)

        self.status_var = tk.StringVar(value="Please wait...")
        tk.Label(
            frame,
            textvariable=self.status_var,
            font=("Arial", 12, "bold"),
        ).pack(pady=15)


# =============================================================================
# MAIN
# =============================================================================

def clear_root(root: tk.Tk) -> None:
    for widget in root.winfo_children():
        widget.destroy()


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    os.chdir(script_dir)

    # Keep the root visible. This avoids the hidden-window behavior that caused
    # the earlier startup issue on some desktop/Tk configurations.
    root = tk.Tk()

    setup_window = SetupWindow(root)
    root.protocol("WM_DELETE_WINDOW", setup_window.cancel)

    # Process the setup window.
    root.mainloop()

    setup = setup_window.result

    if setup is None:
        try:
            root.destroy()
        except Exception:
            pass
        return

    participant_id = setup["participant_id"]
    session_id = str(uuid.uuid4())

    rng = random.Random(setup["seed"])

    schedule = build_schedule(
        rng=rng,
        run_single_finger=setup["run_single"],
        run_adjacent_pair=setup["run_pair"],
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUT_ROOT / f"{participant_id}_{stamp}"

    datastore = DataStore(
        participant_id=participant_id,
        session_id=session_id,
        output_dir=output_dir,
    )

    metadata = {
        "participant_id": participant_id,
        "session_id": session_id,
        "software_version": SOFTWARE_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "serial_port": setup["port"],
        "serial_baud": SERIAL_BAUD,
        "simulation_mode": setup["simulation"],
        "nominal_frequency_hz": NOMINAL_FREQUENCY_HZ,
        "pre_trial_delay_sec": PRE_TRIAL_DELAY_SEC,
        "post_stimulus_before_response_sec": (
            POST_STIMULUS_BEFORE_RESPONSE_SEC
        ),
        "response_timeout_sec": RESPONSE_TIMEOUT_SEC,
        "rest_between_trials_sec": REST_BETWEEN_TRIALS_SEC,
        "adjacent_pair_gap_sec": ADJACENT_PAIR_GAP_SEC,
        "fixed_single_trials": FIXED_SINGLE_TRIALS,
        "random_single_trials": RANDOM_SINGLE_TRIALS,
        "random_adjacent_pair_trials": RANDOM_ADJACENT_PAIR_TRIALS,
        "ask_confounding_report": ASK_CONFOUNDING_REPORT,
        "comfort_rating_collected": False,
        "perceived_intensity_collected": False,
        "run_single_finger": setup["run_single"],
        "run_adjacent_pair": setup["run_pair"],
        "complete_multi_finger_sequence_included": False,
        "randomization_seed": setup["seed"],
        "requested_stimulus_duration_sec": STIMULUS_DURATION_SEC,
        "requested_rtp_drive_value": RTP_DRIVE_VALUE,
        "firmware_protocol": "v1.5:PLAY,channel,duration_ms,rtp_level",
        "actual_vibration_amplitude_measured": False,
        "actual_vibration_frequency_measured": False,
        "actual_vibration_velocity_measured": False,
        "finger_channel_map": CHANNEL_MAP,
        "finger_key_map": KEY_MAP,
        "ordered_adjacent_pairs": ORDERED_ADJACENT_PAIRS,
        "planned_trial_count": len(schedule),
    }

    datastore.save_metadata(metadata)

    device = HapticDevice(
        port=setup["port"],
        simulation=setup["simulation"],
    )

    # Re-enter Tk event processing without hiding the root.
    clear_root(root)
    connecting = ConnectingWindow(
        root,
        setup["port"],
        setup["simulation"],
    )

    result_holder = {
        "done": False,
        "error": None,
    }

    def connect_worker() -> None:
        try:
            device.connect()
        except Exception as exc:
            result_holder["error"] = exc
        finally:
            result_holder["done"] = True

    thread = threading.Thread(
        target=connect_worker,
        daemon=True,
    )
    thread.start()

    def poll_connection() -> None:
        if not result_holder["done"]:
            root.after(100, poll_connection)
            return

        if result_holder["error"] is not None:
            datastore.close_trial_file()
            messagebox.showerror(
                "Serial Connection Error",
                str(result_holder["error"]),
                parent=root,
            )
            device.close()
            root.destroy()
            return
        clear_root(root)
        print("[HARDWARE] Arduino connected; stimulus commands now require START ACK.")

        VSPAPEApp(
            root=root,
            participant_id=participant_id,
            device=device,
            datastore=datastore,
            schedule=schedule,
            random_seed=setup["seed"],
        )

    root.after(100, poll_connection)

    # Because the first setup mainloop exited via root.quit(), explicitly start
    # a fresh event loop for connection + study operation.
    root.mainloop()


if __name__ == "__main__":
    main()