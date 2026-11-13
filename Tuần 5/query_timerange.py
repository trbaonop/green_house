#!/usr/bin/env python3
"""
query_timerange.py — Bài 5 CSE3069
====================================
Truy vấn telemetry theo thiết bị và khoảng thời gian,
vẽ biểu đồ nhiệt độ / độ ẩm bằng Matplotlib.

Cách dùng:
    # Lấy 1 giờ gần nhất của node01
    python query_timerange.py

    # Chỉ định khoảng thời gian cụ thể (epoch)
    python query_timerange.py --device node01 --t0 1730621000 --t1 1730624600

    # Lưu biểu đồ ra file (không hiện cửa sổ)
    python query_timerange.py --save chart.png
"""

import argparse
import os
import sqlite3
import time
from datetime import datetime, timezone

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

DB_PATH = os.path.join(os.path.dirname(__file__), "greenhouse.db")


# ---------------------------------------------------------------------------
# Truy vấn
# ---------------------------------------------------------------------------
def query_timerange(
    conn: sqlite3.Connection,
    device_id: str,
    t0: int,
    t1: int,
) -> list[dict]:
    """
    Lấy toàn bộ bản ghi của device_id trong khoảng thời gian [t0, t1].
    Truy vấn theo cột ts (thời gian đo từ thiết bị).

    Nếu node chưa đồng bộ NTP, ts là uptime (ms) không phải epoch thật —
    khi đó dùng received_at thay thế và chú thích rõ trên biểu đồ.
    """
    cur = conn.execute(
        """
        SELECT id, device_id, ts, seq, temp, hum, rain,
               unit_temp, unit_hum, valid, fsm_state, pump,
               received_at, suspect
        FROM   telemetry
        WHERE  device_id = ?
          AND  ts BETWEEN ? AND ?
        ORDER  BY ts ASC
        """,
        (device_id, t0, t1),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def print_summary(rows: list[dict], t0: int, t1: int) -> None:
    t0_str = datetime.fromtimestamp(t0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1_str = datetime.fromtimestamp(t1, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n{'='*60}")
    print(f"Truy vấn: device={rows[0]['device_id'] if rows else '?'}  T0={t0_str}  T1={t1_str}")
    print(f"Số bản ghi: {len(rows)}")
    if rows:
        valids   = sum(1 for r in rows if r["valid"])
        suspects = sum(1 for r in rows if r["suspect"])
        invalids = len(rows) - valids
        print(f"  Hợp lệ (valid=1):   {valids}")
        print(f"  Không hợp lệ:        {invalids}")
        print(f"  Nghi ngờ (suspect):  {suspects}")
        print(f"  seq min..max:        {min(r['seq'] for r in rows if r['seq'] is not None)}"
              f"..{max(r['seq'] for r in rows if r['seq'] is not None)}")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Vẽ biểu đồ
# ---------------------------------------------------------------------------
def plot_data(rows: list[dict], save_path: str | None = None) -> None:
    if not rows:
        print("Không có dữ liệu để vẽ.")
        return

    # Phân tích xem ts có phải epoch hợp lệ không (> 1_700_000_000 = Nov 2023)
    epoch_ok = all(r["ts"] > 1_700_000_000 for r in rows)
    if epoch_ok:
        # Dùng ts (thời gian đo từ thiết bị)
        x_times = [datetime.fromtimestamp(r["ts"], tz=timezone.utc) for r in rows]
        x_label = "Thời gian đo (UTC, từ thiết bị)"
    else:
        # Node chưa đồng bộ NTP → dùng received_at (thời gian server nhận)
        x_times = [datetime.fromtimestamp(r["received_at"], tz=timezone.utc) for r in rows]
        x_label = "Thời gian nhận (UTC, server) — node chưa đồng bộ NTP"

    # Tách dữ liệu
    temp_all  = [r["temp"]    for r in rows]
    hum_all   = [r["hum"]     for r in rows]
    valid_arr = [bool(r["valid"]) for r in rows]
    suspect_arr = [bool(r["suspect"]) for r in rows]

    # Màu điểm: xanh=valid, cam=suspect, đỏ=invalid
    def point_color(r):
        if r["suspect"]:    return "orange"
        if not r["valid"]:  return "red"
        return "steelblue"

    colors = [point_color(r) for r in rows]

    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    fig.suptitle(
        f"Telemetry — {rows[0]['device_id']} "
        f"({len(rows)} bản ghi, "
        f"{sum(1 for r in rows if not r['suspect'] and r['valid'])} hợp lệ, "
        f"{sum(1 for r in rows if r['suspect'])} nghi ngờ, "
        f"{sum(1 for r in rows if not r['valid'])} invalid)",
        fontsize=12,
    )

    # --- Nhiệt độ ---
    ax_temp = axes[0]
    temp_valid   = [(x, v) for x, v, ok, susp in zip(x_times, temp_all, valid_arr, suspect_arr) if v is not None and ok and not susp]
    temp_invalid = [(x, v) for x, v, ok, susp in zip(x_times, temp_all, valid_arr, suspect_arr) if v is not None and (not ok or susp)]

    if temp_valid:
        ax_temp.plot([t for t, _ in temp_valid], [v for _, v in temp_valid],
                     color="steelblue", linewidth=1.2, label="temp (valid)", zorder=2)
        ax_temp.scatter([t for t, _ in temp_valid], [v for _, v in temp_valid],
                        color="steelblue", s=20, zorder=3)
    if temp_invalid:
        ax_temp.scatter([t for t, _ in temp_invalid], [v for _, v in temp_invalid],
                        color="orange", s=40, marker="x", zorder=4,
                        label="temp (nghi ngờ / invalid)")

    # Đánh dấu điểm null (không có nhiệt độ)
    null_x = [x for x, v in zip(x_times, temp_all) if v is None]
    if null_x:
        ax_temp.scatter(null_x, [0] * len(null_x), color="red", marker="|",
                        s=60, zorder=4, label="temp=null (cảm biến lỗi)")

    ax_temp.set_ylabel("Nhiệt độ (°C)")
    ax_temp.legend(loc="upper right", fontsize=8)
    ax_temp.grid(True, linestyle="--", alpha=0.5)

    # --- Độ ẩm ---
    ax_hum = axes[1]
    hum_valid   = [(x, v) for x, v, ok, susp in zip(x_times, hum_all, valid_arr, suspect_arr) if v is not None and ok and not susp]
    hum_invalid = [(x, v) for x, v, ok, susp in zip(x_times, hum_all, valid_arr, suspect_arr) if v is not None and (not ok or susp)]

    if hum_valid:
        ax_hum.plot([t for t, _ in hum_valid], [v for _, v in hum_valid],
                    color="seagreen", linewidth=1.2, label="hum (valid)", zorder=2)
        ax_hum.scatter([t for t, _ in hum_valid], [v for _, v in hum_valid],
                       color="seagreen", s=20, zorder=3)
    if hum_invalid:
        ax_hum.scatter([t for t, _ in hum_invalid], [v for _, v in hum_invalid],
                       color="orange", s=40, marker="x", zorder=4,
                       label="hum (nghi ngờ / invalid)")

    null_hum_x = [x for x, v in zip(x_times, hum_all) if v is None]
    if null_hum_x:
        ax_hum.scatter(null_hum_x, [0] * len(null_hum_x), color="red", marker="|",
                       s=60, zorder=4, label="hum=null (cảm biến lỗi)")

    ax_hum.set_ylabel("Độ ẩm (%)")
    ax_hum.set_xlabel(x_label)
    ax_hum.legend(loc="upper right", fontsize=8)
    ax_hum.grid(True, linestyle="--", alpha=0.5)

    # Định dạng trục thời gian
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate()

    # Chú giải màu
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color="steelblue",  label="Hợp lệ"),
        Line2D([0], [0], color="orange",    linestyle="none", marker="x", label="Nghi ngờ / ngoài dải"),
        Line2D([0], [0], color="red",       linestyle="none", marker="|", label="null (cảm biến lỗi)"),
    ]
    fig.legend(handles=legend_elements, loc="lower center", ncol=3, fontsize=9,
               bbox_to_anchor=(0.5, -0.02))

    plt.tight_layout(rect=[0, 0.04, 1, 1])

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Biểu đồ đã lưu: {save_path}")
    else:
        plt.show()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Query telemetry + plot")
    p.add_argument("--device", default="node01", help="device_id (default: node01)")
    p.add_argument("--t0", type=int, default=None,
                   help="Unix epoch bắt đầu (default: 1 giờ trước)")
    p.add_argument("--t1", type=int, default=None,
                   help="Unix epoch kết thúc (default: hiện tại)")
    p.add_argument("--save", default=None, metavar="FILE",
                   help="Lưu biểu đồ vào file thay vì hiện cửa sổ")
    return p.parse_args()


def main():
    args = parse_args()
    now = int(time.time())
    t1 = args.t1 if args.t1 else now
    t0 = args.t0 if args.t0 else (t1 - 3600)   # mặc định 1 giờ gần nhất

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")

    rows = query_timerange(conn, args.device, t0, t1)
    print_summary(rows, t0, t1)

    if rows:
        # In 5 bản ghi đầu để xem
        print("5 bản ghi đầu:")
        for r in rows[:5]:
            ts_str = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%H:%M:%S") \
                     if r["ts"] > 1_700_000_000 else f"uptime={r['ts']}"
            print(f"  seq={r['seq']:>4}  ts={ts_str}  "
                  f"temp={r['temp']}°C  hum={r['hum']}%  "
                  f"valid={'✓' if r['valid'] else '✗'}  suspect={'⚠' if r['suspect'] else '-'}")
        if len(rows) > 5:
            print(f"  ... ({len(rows)-5} bản ghi nữa)")

    save = args.save or os.path.join(os.path.dirname(__file__), "chart.png")
    plot_data(rows, save_path=save)
    conn.close()


if __name__ == "__main__":
    main()
