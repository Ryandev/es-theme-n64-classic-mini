"""``n64mini-thermal-test``: the cooling's real-system check, run on the console (REQ-04).

Loads every core so the N100 sits at its sustained power limit (PL1, 6 W stock), reads the SSD
for the last few minutes (everything hot at once), and samples the package temperature, package
power (RAPL energy counter), the thermal-throttle counter and the NVMe temperature. It passes when,
over the steady last stretch, the package stays within ``MAX_RISE_K`` of the room, the CPU never
throttled, and the SSD stays at or under ``NVME_MAX_C``. It prints the CAD model's prediction next
to the measurement, so a model that is wrong shows up here.

    python3 -m n64mini.console.thermal_test --room 22      # room temperature in C, measured
    python3 -m n64mini.console.thermal_test --room 22 --no-load --minutes 30   # watch real play

Writes every sample to a CSV. Runs on the console, so it uses only the standard library.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

MAX_RISE_K: Final = 55.0  # 85 C target in a 30 C room (cad: subassembly/cooling_check.py)
PREDICTED_RISE_K: Final = 50.0  # the CAD model's Tj at 6 W, 80.0 C, minus its 30 C room
NVME_MAX_C: Final = 70.0  # below where M.2 2230 drives start throttling
STEADY_S: Final = 300.0  # judge the last five minutes
NVME_READ_S: Final = 300.0
CSV_PATH: Final = Path("/userdata/system/logs/n64mini-thermal.csv")
NVME_DEV: Final = Path("/dev/nvme0n1")
MIN_SAMPLES: Final = 2  # power needs two readings


class SensorError(RuntimeError):
    """A sensor the verdict needs is missing."""


@dataclass(frozen=True, slots=True)
class Sample:
    """One reading. Optional sensors are None where the kernel doesn't expose them."""

    t: float
    package_c: float
    energy_uj: int | None
    throttles: int | None
    nvme_c: float | None


@dataclass(frozen=True, slots=True)
class Verdict:
    """The judgement over the steady window."""

    passed: bool
    lines: tuple[str, ...]


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _hwmon(sysfs: Path, name: str) -> Path | None:
    for d in sorted((sysfs / "class" / "hwmon").glob("hwmon*")):
        with contextlib.suppress(OSError):
            if (d / "name").read_text(encoding="utf-8").strip() == name:
                return d
    return None


def package_temp_c(sysfs: Path) -> float:
    """The CPU package temperature (coretemp "Package id 0")."""
    hw = _hwmon(sysfs, "coretemp")
    if hw is not None:
        for label in sorted(hw.glob("temp*_label")):
            if label.read_text(encoding="utf-8").strip().startswith("Package"):
                value = _read_int(hw / label.name.replace("_label", "_input"))
                if value is not None:
                    return value / 1000.0
    raise SensorError(f"no coretemp package temperature under {sysfs}/class/hwmon")


def nvme_temp_c(sysfs: Path) -> float | None:
    """The SSD's composite temperature, if the nvme hwmon driver is loaded."""
    hw = _hwmon(sysfs, "nvme")
    value = None if hw is None else _read_int(hw / "temp1_input")
    return None if value is None else value / 1000.0


def read_sample(sysfs: Path, now: float) -> Sample:
    """Read every sensor once."""
    return Sample(
        t=now,
        package_c=package_temp_c(sysfs),
        energy_uj=_read_int(sysfs / "class/powercap/intel-rapl:0/energy_uj"),
        throttles=_read_int(
            sysfs / "devices/system/cpu/cpu0/thermal_throttle/package_throttle_count"
        ),
        nvme_c=nvme_temp_c(sysfs),
    )


def power_w(a: Sample, b: Sample, wrap_uj: int | None) -> float | None:
    """Package power between two samples from the RAPL energy counter (which wraps)."""
    if a.energy_uj is None or b.energy_uj is None or b.t <= a.t:
        return None
    delta = b.energy_uj - a.energy_uj
    if delta < 0:
        if wrap_uj is None:
            return None
        delta += wrap_uj
    return delta / 1e6 / (b.t - a.t)


def judge(samples: Sequence[Sample], room_c: float, wrap_uj: int | None) -> Verdict:
    """Pass/fail over the last STEADY_S seconds, with the model's prediction alongside."""
    if len(samples) < MIN_SAMPLES:
        return Verdict(passed=False, lines=("too few samples to judge",))
    end = samples[-1].t
    steady = [s for s in samples if s.t >= end - STEADY_S]
    steady = steady if len(steady) >= MIN_SAMPLES else list(samples[-MIN_SAMPLES:])
    peak = max(s.package_c for s in steady)
    rise = peak - room_c
    watts = power_w(steady[0], steady[-1], wrap_uj)
    first_count, last_count = samples[0].throttles, samples[-1].throttles
    throttled = None if first_count is None or last_count is None else last_count - first_count
    nvme = [s.nvme_c for s in samples if s.nvme_c is not None]
    nvme_peak = max(nvme) if nvme else None
    ok_rise = rise <= MAX_RISE_K
    ok_throttle = throttled in (None, 0)
    ok_nvme = nvme_peak is None or nvme_peak <= NVME_MAX_C
    lines = (
        (
            f"package peak {peak:.1f} C over the last {STEADY_S / 60:.0f} min: {rise:.1f} K "
            f"above the {room_c:.1f} C room (limit {MAX_RISE_K:.0f} K; the model predicts "
            f"{PREDICTED_RISE_K:.0f} K at 6 W) -> {'ok' if ok_rise else 'TOO HOT'}"
        ),
        "package power " + ("n/a (no RAPL counter)" if watts is None else f"{watts:.1f} W"),
        "thermal throttling "
        + ("not reported by this kernel" if throttled is None else f"{throttled} events")
        + (" -> ok" if ok_throttle else " -> THROTTLED"),
        "SSD "
        + ("temperature not reported" if nvme_peak is None else f"peak {nvme_peak:.1f} C")
        + f" (limit {NVME_MAX_C:.0f} C)"
        + (" -> ok" if ok_nvme else " -> TOO HOT"),
    )
    return Verdict(passed=ok_rise and ok_throttle and ok_nvme, lines=lines)


def start_cpu_load(cores: int) -> Callable[[], None]:
    """One busy loop per core; returns the function that stops them."""
    procs = [subprocess.Popen([sys.executable, "-c", "while True: pass"]) for _ in range(cores)]

    def stop() -> None:
        for p in procs:
            p.kill()
            p.wait()

    return stop


def read_until(fh: BinaryIO, done: threading.Event) -> None:
    """Read 1 MiB at a time, wrapping at the end, until `done` is set."""
    while not done.is_set():
        if not fh.read(1 << 20):
            fh.seek(0)


def start_nvme_read(device: Path) -> Callable[[], None]:
    """Sequential reads of the SSD in a background thread (the drive is far larger than RAM)."""
    done = threading.Event()

    def reader() -> None:
        with device.open("rb", buffering=0) as fh:
            read_until(fh, done)

    worker = threading.Thread(target=reader, daemon=True)
    worker.start()

    def stop() -> None:
        done.set()
        worker.join(timeout=5.0)

    return stop


@dataclass(frozen=True, slots=True)
class Run:
    """How a run goes; tests substitute the clock, the sleep and the load starters."""

    sysfs: Path
    seconds: float
    interval: float
    load: bool
    nvme: Path
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    start_cpu: Callable[[int], Callable[[], None]] = start_cpu_load
    start_read: Callable[[Path], Callable[[], None]] = start_nvme_read


def collect(run: Run) -> list[Sample]:
    """Sample until the time is up, loading the CPU and (for the last stretch) the SSD."""
    stops: list[Callable[[], None]] = []
    if run.load:
        stops.append(run.start_cpu(os.cpu_count() or 4))
    samples: list[Sample] = []
    t0 = run.clock()
    reading = False
    try:
        while True:
            now = run.clock() - t0
            samples.append(read_sample(run.sysfs, now))
            if now >= run.seconds:
                break
            if run.load and not reading and now >= run.seconds - NVME_READ_S and run.nvme.exists():
                stops.append(run.start_read(run.nvme))
                reading = True
            run.sleep(run.interval)
    finally:
        for stop in stops:
            stop()
    return samples


def write_csv(path: Path, samples: Sequence[Sample]) -> None:
    """Every sample, for a closer look."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        out = csv.writer(fh)
        out.writerow(["t_s", "package_c", "energy_uj", "throttles", "nvme_c"])
        for s in samples:
            out.writerow([f"{s.t:.1f}", s.package_c, s.energy_uj, s.throttles, s.nvme_c])


def main(argv: list[str] | None = None, *, run_override: Run | None = None) -> int:
    """CLI entry point. Exit 0 pass, 1 fail, 2 bad usage or a missing sensor."""
    parser = argparse.ArgumentParser(description="Load the console and check its cooling.")
    parser.add_argument("--room", type=float, required=True, help="room temperature, C")
    parser.add_argument("--minutes", type=float, default=20.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--no-load", action="store_true", help="only watch (e.g. during play)")
    parser.add_argument("--sys", type=Path, default=Path("/sys"))
    parser.add_argument("--nvme", type=Path, default=NVME_DEV)
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    args = parser.parse_args(argv)
    run = run_override or Run(
        sysfs=args.sys,
        seconds=args.minutes * 60.0,
        interval=args.interval,
        load=not args.no_load,
        nvme=args.nvme,
    )
    try:
        samples = collect(run)
    except SensorError as exc:
        sys.stderr.write(f"n64mini-thermal-test: {exc}\n")
        return 2
    write_csv(args.csv, samples)
    wrap = _read_int(run.sysfs / "class/powercap/intel-rapl:0/max_energy_range_uj")
    verdict = judge(samples, args.room, wrap)
    for line in verdict.lines:
        sys.stdout.write(line + "\n")
    sys.stdout.write(("PASS" if verdict.passed else "FAIL") + f" (samples in {args.csv})\n")
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
