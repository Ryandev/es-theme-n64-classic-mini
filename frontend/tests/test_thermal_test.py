"""REQ-04: the on-console cooling check (n64mini.console.thermal_test), against a fake sysfs."""

from __future__ import annotations

import io
import os
import threading
from typing import TYPE_CHECKING, override

import pytest

from n64mini.console import thermal_test as tt

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _sysfs(
    root: Path,
    *,
    package_mc: int | None = 60_000,
    energy: int | None = 1_000_000,
    throttles: int | None = 0,
    nvme_mc: int | None = 45_000,
) -> Path:
    hw = root / "class/hwmon"
    core = hw / "hwmon1"
    core.mkdir(parents=True)
    (core / "name").write_text("coretemp\n")
    (core / "temp2_label").write_text("Core 0\n")
    (core / "temp2_input").write_text("50000\n")
    if package_mc is not None:
        (core / "temp1_label").write_text("Package id 0\n")
        (core / "temp1_input").write_text(f"{package_mc}\n")
    if nvme_mc is not None:
        nv = hw / "hwmon2"
        nv.mkdir()
        (nv / "name").write_text("nvme\n")
        (nv / "temp1_input").write_text(f"{nvme_mc}\n")
    if energy is not None:
        rapl = root / "class/powercap/intel-rapl:0"
        rapl.mkdir(parents=True)
        (rapl / "energy_uj").write_text(f"{energy}\n")
        (rapl / "max_energy_range_uj").write_text("262143328850\n")
    if throttles is not None:
        throttle_dir = root / "devices/system/cpu/cpu0/thermal_throttle"
        throttle_dir.mkdir(parents=True)
        (throttle_dir / "package_throttle_count").write_text(f"{throttles}\n")
    return root


def _s(
    t: float, c: float, e: int | None = None, throttles: int | None = 0, nv: float | None = 40.0
) -> tt.Sample:
    return tt.Sample(t=t, package_c=c, energy_uj=e, throttles=throttles, nvme_c=nv)


def test_sensors_are_read_from_sysfs(tmp_path: Path) -> None:
    root = _sysfs(tmp_path)
    s = tt.read_sample(root, 3.0)
    assert s == tt.Sample(t=3.0, package_c=60.0, energy_uj=1_000_000, throttles=0, nvme_c=45.0)


def test_optional_sensors_read_as_none_and_a_missing_package_sensor_is_an_error(
    tmp_path: Path,
) -> None:
    root = _sysfs(tmp_path / "a", energy=None, throttles=None, nvme_mc=None)
    s = tt.read_sample(root, 0.0)
    assert (s.energy_uj, s.throttles, s.nvme_c) == (None, None, None)
    with pytest.raises(tt.SensorError, match="no coretemp package temperature"):
        tt.package_temp_c(_sysfs(tmp_path / "b", package_mc=None))
    with pytest.raises(tt.SensorError):
        tt.package_temp_c(tmp_path / "empty")


def test_an_unreadable_hwmon_name_or_value_is_skipped(tmp_path: Path) -> None:
    root = _sysfs(tmp_path)
    broken = root / "class/hwmon/hwmon0"
    broken.mkdir()
    (broken / "name").mkdir()  # reading a directory raises OSError
    assert tt.package_temp_c(root) == 60.0
    (root / "class/hwmon/hwmon2/temp1_input").write_text("garbage\n")
    assert tt.nvme_temp_c(root) is None
    (root / "class/hwmon/hwmon1/temp1_input").write_text("garbage\n")
    with pytest.raises(tt.SensorError):
        tt.package_temp_c(root)


def test_package_power_comes_from_the_energy_counter_including_wraparound() -> None:
    assert tt.power_w(_s(0, 50, 0), _s(10, 50, 60_000_000), None) == pytest.approx(6.0)
    assert tt.power_w(_s(0, 50, 990), _s(1, 50, 10), 1000) == pytest.approx(20e-6)
    assert tt.power_w(_s(0, 50, 990), _s(1, 50, 10), None) is None
    assert tt.power_w(_s(0, 50, None), _s(1, 50, 10), None) is None
    assert tt.power_w(_s(1, 50, 0), _s(1, 50, 10), None) is None


def test_judge_passes_a_cool_quiet_run_and_reports_each_measure() -> None:
    samples = [_s(t, 70.0, int(t * 6e6), 3, 50.0) for t in range(0, 601, 60)]
    v = tt.judge(samples, room_c=22.0, wrap_uj=None)
    assert v.passed
    assert v.lines[0].startswith(
        "package peak 70.0 C over the last 5 min: 48.0 K above the 22.0 C room"
    )
    assert v.lines[1] == "package power 6.0 W"
    assert v.lines[2] == "thermal throttling 0 events -> ok"
    assert v.lines[3] == "SSD peak 50.0 C (limit 70 C) -> ok"


@pytest.mark.parametrize(
    ("samples", "line", "text"),
    [
        ([_s(0, 60), _s(600, 80.0)], 0, "TOO HOT"),  # 58 K above a 22 C room
        ([_s(0, 60, throttles=0), _s(600, 60, throttles=4)], 2, "4 events -> THROTTLED"),
        ([_s(0, 60), _s(600, 60, nv=75.0)], 3, "peak 75.0 C (limit 70 C) -> TOO HOT"),
    ],
)
def test_judge_fails_on_heat_throttling_or_a_hot_ssd(
    samples: list[tt.Sample], line: int, text: str
) -> None:
    v = tt.judge(samples, room_c=22.0, wrap_uj=None)
    assert not v.passed
    assert text in v.lines[line]


def test_judge_says_what_it_cannot_measure() -> None:
    v = tt.judge(
        [_s(0, 60, throttles=None, nv=None), _s(1, 60, throttles=None, nv=None)], 22.0, None
    )
    assert v.passed
    assert v.lines[1] == "package power n/a (no RAPL counter)"
    assert v.lines[2] == "thermal throttling not reported by this kernel -> ok"
    assert v.lines[3] == "SSD temperature not reported (limit 70 C) -> ok"
    assert tt.judge([_s(0, 60)], 22.0, None) == tt.Verdict(
        passed=False, lines=("too few samples to judge",)
    )


def test_collect_loads_the_cpu_then_the_ssd_for_the_last_stretch_and_always_stops(
    tmp_path: Path,
) -> None:
    root = _sysfs(tmp_path / "sys")
    disk = tmp_path / "nvme0n1"
    disk.write_bytes(b"x")
    clock = iter(float(t) for t in (0, 0, 100, 200, 300, 400))
    events: list[str] = []

    def cpu(cores: int) -> Callable[[], None]:
        events.append(f"cpu {cores}")
        return lambda: events.append("cpu stop")

    def read(dev: Path) -> Callable[[], None]:
        events.append(f"read {dev.name}")
        return lambda: events.append("read stop")

    run = tt.Run(
        root,
        400.0,
        100.0,
        load=True,
        nvme=disk,
        clock=lambda: next(clock),
        sleep=lambda _s: None,
        start_cpu=cpu,
        start_read=read,
    )
    samples = tt.collect(run)
    assert [s.t for s in samples] == [0, 100, 200, 300, 400]
    assert events == [f"cpu {os.cpu_count() or 4}", "read nvme0n1", "cpu stop", "read stop"]

    events.clear()
    no_disk = tt.Run(
        root,
        0.0,
        1.0,
        load=True,
        nvme=tmp_path / "missing",
        clock=iter([0.0, 0.0]).__next__,
        sleep=lambda _s: None,
        start_cpu=cpu,
        start_read=read,
    )
    assert len(tt.collect(no_disk)) == 1
    assert events == [f"cpu {os.cpu_count() or 4}", "cpu stop"]


def test_the_reader_wraps_at_the_end_and_stops_when_told() -> None:
    done = threading.Event()

    class _Disk(io.BytesIO):
        reads = 0

        @override
        def read(self, size: int | None = -1) -> bytes:
            self.reads += 1
            if self.reads == 3:  # stop on the third read
                done.set()
            return super().read(size)

    disk = _Disk(b"x")
    tt.read_until(disk, done)
    assert disk.reads == 3  # 1 byte, then end of file (wraps to the start), then stop
    assert disk.tell() == 1


def test_the_real_load_starters_run_and_stop(tmp_path: Path) -> None:
    stop = tt.start_cpu_load(1)
    stop()
    disk = tmp_path / "disk"
    disk.write_bytes(b"x" * 10)  # shorter than one read: the reader wraps to the start
    stop_read = tt.start_nvme_read(disk)
    stop_read()


def test_main_writes_the_csv_and_exits_by_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _sysfs(tmp_path / "sys")
    out = tmp_path / "log" / "t.csv"
    clock = iter([0.0, 0.0, 600.0])
    run = tt.Run(
        root,
        600.0,
        600.0,
        load=False,
        nvme=tmp_path / "none",
        clock=lambda: next(clock),
        sleep=lambda _s: None,
    )
    assert tt.main(["--room", "22", "--csv", str(out)], run_override=run) == 0
    assert out.read_text().splitlines()[0] == "t_s,package_c,energy_uj,throttles,nvme_c"
    assert capsys.readouterr().out.endswith(f"PASS (samples in {out})\n")
    # The real Run from the arguments: no load, zero minutes -> one sample -> too few to judge.
    assert (
        tt.main(
            ["--room", "22", "--sys", str(root), "--minutes", "0", "--no-load", "--csv", str(out)]
        )
        == 1
    )
    assert "too few samples to judge" in capsys.readouterr().out


def test_main_reports_a_missing_package_sensor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        tt.main(
            ["--room", "22", "--sys", str(tmp_path), "--no-load", "--csv", str(tmp_path / "c.csv")]
        )
        == 2
    )
    assert "no coretemp package temperature" in capsys.readouterr().err
