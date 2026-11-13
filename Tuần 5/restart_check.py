#!/usr/bin/env python3
"""
restart_check.py — Bài 5 CSE3069
==================================
Kiểm tra tính bền vững của dữ liệu (D5) sau khi khởi động lại service/DB.

Quy trình:
  Bước 1  — Chốt T0/T1 và chạy truy vấn TRƯỚC restart → ghi log_before_restart.txt
  Bước 2  — Dừng ingest_service (Ctrl+C hoặc tắt container DB)
  Bước 3  — Khởi động lại service/DB
  Bước 4  — Chạy lại script với cùng T0/T1 → ghi log_after_restart.txt
  Bước 5  — So sánh hai file để xác nhận dữ liệu còn nguyên

Cách dùng:
    python restart_check.py --t0 1730621000 --t1 1730624600
    # hoặc không tham số → dùng T0/T1 tự động (gần nhất ~10 phút)
"""

import argparse
import os
import sqlite3
import time
from datetime import datetime, timezone

DB_PATH     = os.path.join(os.path.dirname(__file__), "greenhouse.db")
LOG_BEFORE  = os.path.join(os.path.dirname(__file__), "log_before_restart.txt")
LOG_AFTER   = os.path.join(os.path.dirname(__file__), "log_after_restart.txt")


def query(conn, device_id, t0, t1):
    cur = conn.execute(
        """
        SELECT id, device_id, ts, seq, temp, hum, rain, valid, suspect, received_at
        FROM   telemetry
        WHERE  device_id = ?
          AND  ts BETWEEN ? AND ?
        ORDER  BY ts ASC
        """,
        (device_id, t0, t1),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def format_rows(rows, t0, t1, label):
    t0s = datetime.fromtimestamp(t0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1s = datetime.fromtimestamp(t1, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "=" * 60,
        f"[{label}]",
        f"Truy vấn: device=node01  T0={t0s}  T1={t1s}",
        f"Số bản ghi: {len(rows)}",
        "-" * 60,
    ]
    for r in rows:
        ts_s = datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%H:%M:%S") \
               if r["ts"] > 1_700_000_000 else f"uptime={r['ts']}"
        lines.append(
            f"  id={r['id']:>5}  seq={str(r['seq']):>4}  ts={ts_s}"
            f"  temp={r['temp']}  hum={r['hum']}"
            f"  valid={'✓' if r['valid'] else '✗'}  suspect={'⚠' if r['suspect'] else '-'}"
        )
    if not rows:
        lines.append("  (Không có bản ghi)")
    lines.append("=" * 60)
    return "\n".join(lines)


def compare(before_rows, after_rows):
    print("\n--- So sánh TRƯỚC / SAU restart ---")
    before_keys = {(r["device_id"], r["ts"], r["seq"]) for r in before_rows}
    after_keys  = {(r["device_id"], r["ts"], r["seq"]) for r in after_rows}

    missing = before_keys - after_keys
    extra   = after_keys  - before_keys

    if not missing and not extra:
        print("✓ KHỚP HOÀN TOÀN — dữ liệu không bị mất sau restart.")
        print(f"  Số bản ghi trước: {len(before_rows)}")
        print(f"  Số bản ghi sau:   {len(after_rows)}")
    else:
        if missing:
            print(f"✗ Mất {len(missing)} bản ghi sau restart:")
            for k in sorted(missing)[:10]:
                print(f"    device={k[0]} ts={k[1]} seq={k[2]}")
        if extra:
            print(f"⚠ Có {len(extra)} bản ghi MỚI (xuất hiện sau restart — bình thường nếu ghi tiếp):")
            for k in sorted(extra)[:5]:
                print(f"    device={k[0]} ts={k[1]} seq={k[2]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--device", default="node01")
    p.add_argument("--t0", type=int, default=None)
    p.add_argument("--t1", type=int, default=None)
    args = p.parse_args()

    now = int(time.time())
    t1 = args.t1 or now
    t0 = args.t0 or (t1 - 600)   # mặc định 10 phút gần nhất

    if not os.path.exists(DB_PATH):
        print(f"[LỖI] Không tìm thấy DB: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    rows = query(conn, args.device, t0, t1)
    conn.close()

    # Quyết định đây là lần BEFORE hay AFTER dựa vào file nào đã tồn tại
    if not os.path.exists(LOG_BEFORE):
        label   = "BEFORE RESTART"
        log_out = LOG_BEFORE
        print(f"[Bước 1] Ghi kết quả TRƯỚC restart → {log_out}")
        print(f"         T0={t0}  T1={t1}  (ghi lại hai giá trị này để dùng ở bước 4)")
        txt = format_rows(rows, t0, t1, label)
        print(txt)
        with open(log_out, "w", encoding="utf-8") as f:
            f.write(f"T0={t0}\nT1={t1}\n")
            f.write(txt)
        print(f"\nĐã lưu: {log_out}")
        print("\nTiếp theo:")
        print("  1. Dừng ingest_service.py (Ctrl+C) và/hoặc container DB.")
        print("  2. Khởi động lại service/DB.")
        print("  3. Chạy lại: python restart_check.py"
              f" --t0 {t0} --t1 {t1}")
    else:
        # Đọc T0/T1 từ log BEFORE nếu không được truyền
        if not args.t0 or not args.t1:
            with open(LOG_BEFORE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("T0="):
                        t0 = int(line.strip().split("=")[1])
                    if line.startswith("T1="):
                        t1 = int(line.strip().split("=")[1])
            # Truy vấn lại với T0/T1 đã lưu
            conn = sqlite3.connect(DB_PATH)
            rows = query(conn, args.device, t0, t1)
            conn.close()

        label   = "AFTER RESTART"
        log_out = LOG_AFTER
        print(f"[Bước 4] Ghi kết quả SAU restart → {log_out}")
        txt = format_rows(rows, t0, t1, label)
        print(txt)
        with open(log_out, "w", encoding="utf-8") as f:
            f.write(f"T0={t0}\nT1={t1}\n")
            f.write(txt)
        print(f"\nĐã lưu: {log_out}")

        # Đọc dữ liệu BEFORE để so sánh
        before_rows = []
        with open(LOG_BEFORE, "r", encoding="utf-8") as f:
            # Đọc T0/T1 từ header
            saved_t0 = saved_t1 = None
            for line in f:
                if line.startswith("T0="): saved_t0 = int(line.strip().split("=")[1])
                if line.startswith("T1="): saved_t1 = int(line.strip().split("=")[1])

        conn2 = sqlite3.connect(DB_PATH)
        before_rows = query(conn2, args.device, saved_t0 or t0, saved_t1 or t1)
        conn2.close()

        compare(before_rows, rows)


if __name__ == "__main__":
    main()
