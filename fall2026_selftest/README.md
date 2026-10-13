# VSPA:PE Fall 2026 self-test code

This folder contains the **v1.5 corrected baseline** used for the October 13, 2026 researcher-only 15-trial self-test.

- `self_test_gui_v1_5.py`: Python GUI, 5 fixed single-finger trials, 5 random single-finger trials, 5 random adjacent-pair trials, keyboard-response scoring and CSV output. Run with Python 3, tkinter, and pyserial installed.
- `firmware_vspa_rtp_v1_5.ino`: Arduino Uno R3 matching serial protocol. Requires Arduino Wire and Adafruit DRV2605 libraries.

**Protocol:** 9600 baud. Python sends `PLAY,channel,duration_ms,rtp_level`; Arduino reports `START,...`. The baseline requested duration is 1000 ms, gap between first and second commands is 2000 ms, drive register value is 48. The nominal motor frequency is 235 Hz.

**Hardware:** Arduino Uno R3 A4/SDA A5/SCL -> TCA9548A 0x70 -> DRV2605L 0x5A on channels 0–4 -> VG0832022D LRA motors. Index: SC3, pinky: SC0, ring: SC1, middle: SC2, thumb: SC4.

**Limits:** This code logs host command timing and firmware acknowledgements, **not** measured physical vibration, acceleration, voltage or velocity. Run bench checks before wearing. No clinical or neuropathy claims are validated. This public folder deliberately includes no participant-level raw data, signatures, or confidential records.

**Research manuscript:** A separate Overleaf ZIP and compiled PDF document the October 2026 single-session results, observed figures, theoretical velocity/frequency models, and earlier-project history.