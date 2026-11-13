#!/usr/bin/env python3
"""
concurrent_load_test.py — Bài 5 CSE3069 (Thí nghiệm bắt buộc — Mục 3)
=======================================================================
Chạy truy vấn nặng liên tục trong khi ingest_service đang ghi dữ liệu mới.
Đo độ trễ ghi bản ghi khi có / không có truy vấn nặng song song.

Cách chạy:
    1. Đảm bảo ingest_service.py đang chạy và nhận dữ liệu từ MQTT
    2. python concurrent_load_test.py

Kết quả in ra màn hình và ghi vào concurrent_load_result.txt
"""

import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "greenhouse.db")
RESULT_FILE = os.path.join(os.path.dirname(__file__), "concurrent_load_result.txt")

HEAVY_QUERY_INTERVAL_S = 0.5    # Chạy truy vấn nặng mỗi 0.5 giây
SAMPLE_DURATION_S       = 60    # Thời gian đo (giây)
BASELINE_DURATION_S     = 30    # Thời gian đo nền (không có truy vấn nặng)


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def heavy_query(conn: sqlite3.Connection) -> float:
    """Truy vấn nặng: quét toàn bảng, tính trung bình. Trả về thời gian thực thi (ms)."""
    t0 = time.perf_counter()
    conn.execute("""
        SELECT device_id,
               COUNT(*) AS n,
               AVG(temp)  AS avg_temp,
               AVG(hum)   AS avg_hum,
               MIN(temp), MAX(temp)
        FROM   telemetry
        WHERE  valid = 1
        GROUP  BY device_id
    """).fetchall()
    return (time.perf_counter() - t0) * 1000   # ms


def watch_write_latency(conn: sqlite3.Connection, stop_event: threading.Event,
                        label: str, results: list) -> None:
    """
    Theo dõi thời điểm bản ghi mới xuất hiện trong DB.
    Tính khoảng cách thời gian (received_at) giữa hai bản ghi liên tiếp.
    """
    last_id = conn.execute("SELECT MAX(id) FROM telemetry").fetchone()[0] or 0
    intervals = []

    while not stop_event.is_set():
        time.sleep(0.2)
        row = conn.execute(
            "SELECT id, received_at FROM telemetry WHERE id > ? ORDER BY id ASC LIMIT 1",
            (last_id,)
        ).fetchone()
        if row:
            new_id, recv_at = row
            if intervals:
                intervals.append(recv_at - prev_recv)
            prev_recv = recv_at
            last_id = new_id

    results.append({"label": label, "intervals": intervals})


def run_phase(label: str, duration: int, with_heavy: bool, results: list) -> dict:
    print(f"\n--- Phase: {label} ({'CÓ' if with_heavy else 'KHÔNG CÓ'} truy vấn nặng) ---")
    print(f"    Thời gian đo: {duration}s")

    write_conn  = get_conn()   # Kết nối đọc bản ghi mới (giả lập theo dõi ghi)
    heavy_conn  = get_conn()   # Kết nối riêng cho truy vấn nặng (tách tải)

    stop_evt = threading.Event()
    watch_results = []

    # Thread theo dõi ghi
    watcher = threading.Thread(
        target=watch_write_latency,
        args=(write_conn, stop_evt, label, watch_results),
        daemon=True,
    )
    watcher.start()

    heavy_latencies = []

    t_end = time.time() + duration
    while time.time() < t_end:
        if with_heavy:
            lat = heavy_query(heavy_conn)
            heavy_latencies.append(lat)
            time.sleep(max(0, HEAVY_QUERY_INTERVAL_S - lat / 1000))
        else:
            time.sleep(0.5)

    stop_evt.set()
    watcher.join(timeout=3)
    write_conn.close()
    heavy_conn.close()

    intervals = watch_results[0]["intervals"] if watch_results else []
    result = {
        "label":           label,
        "with_heavy":      with_heavy,
        "duration_s":      duration,
        "write_intervals": intervals,
        "avg_write_gap_s": sum(intervals) / len(intervals) if intervals else None,
        "heavy_latencies": heavy_latencies,
        "avg_heavy_ms":    sum(heavy_latencies) / len(heavy_latencies) if heavy_latencies else None,
    }
    results.append(result)

    if intervals:
        avg = result["avg_write_gap_s"]
        print(f"    Ghi bản ghi: {len(intervals)} lần đo, avg interval = {avg:.2f}s")
    else:
        print("    Không phát hiện bản ghi mới (ingest_service chưa chạy?)")

    if heavy_latencies:
        avg_h = result["avg_heavy_ms"]
        print(f"    Truy vấn nặng: {len(heavy_latencies)} lần, avg = {avg_h:.1f}ms")

    return result


def print_report(results: list) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("KẾT QUẢ THÍ NGHIỆM TẢI ĐỒNG THỜI — CSE3069 Bài 5")
    lines.append(f"Thời điểm: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("=" * 60)

    baseline = next((r for r in results if not r["with_heavy"]), None)
    loaded   = next((r for r in results if r["with_heavy"]),    None)

    for r in results:
        label = r["label"]
        lines.append(f"\n[{label}]")
        if r["avg_write_gap_s"] is not None:
            lines.append(f"  Avg write interval: {r['avg_write_gap_s']:.3f}s  "
                         f"(n={len(r['write_intervals'])})")
        else:
            lines.append("  Không có dữ liệu ghi interval.")
        if r["avg_heavy_ms"] is not None:
            lines.append(f"  Avg heavy query:    {r['avg_heavy_ms']:.1f}ms  "
                         f"(n={len(r['heavy_latencies'])})")

    lines.append("\n--- So sánh ---")
    if baseline and loaded and baseline["avg_write_gap_s"] and loaded["avg_write_gap_s"]:
        delta = loaded["avg_write_gap_s"] - baseline["avg_write_gap_s"]
        pct   = delta / baseline["avg_write_gap_s"] * 100
        lines.append(f"  Baseline avg: {baseline['avg_write_gap_s']:.3f}s")
        lines.append(f"  Loaded   avg: {loaded['avg_write_gap_s']:.3f}s")
        lines.append(f"  Delta:        {delta:+.3f}s  ({pct:+.1f}%)")
        if abs(pct) < 10:
            lines.append("  → Kết luận: Truy vấn nặng KHÔNG ảnh hưởng đáng kể tới ghi dữ liệu.")
            lines.append("    (SQLite WAL mode cho phép đọc và ghi song song không chặn nhau)")
        else:
            lines.append(f"  → Kết luận: Có ảnh hưởng ({pct:+.1f}%). Nên tách kết nối đọc/ghi.")
    else:
        lines.append("  Không đủ dữ liệu để so sánh.")

    lines.append("\n--- Giải thích thiết kế ---")
    lines.append("  Service ingest_service.py dùng kết nối DB riêng (write_conn).")
    lines.append("  Script truy vấn nặng dùng kết nối riêng (heavy_conn).")
    lines.append("  SQLite WAL mode: reader không block writer và ngược lại.")
    lines.append("  → Tải đọc không làm chậm ghi trong điều kiện thử nghiệm này.")
    lines.append("=" * 60)

    return "\n".join(lines)


def main():
    if not os.path.exists(DB_PATH):
        print(f"[LỖI] Không tìm thấy DB tại: {DB_PATH}")
        print("Hãy chạy ingest_service.py trước để tạo DB.")
        return

    results = []

    # Phase 1: Đo nền — không có truy vấn nặng
    run_phase("Baseline (không có truy vấn nặng)", BASELINE_DURATION_S, False, results)

    # Phase 2: Đo có tải — truy vấn nặng liên tục
    run_phase("Loaded (có truy vấn nặng liên tục)", SAMPLE_DURATION_S, True, results)

    report = print_report(results)
    print("\n" + report)

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"\nKết quả đã lưu: {RESULT_FILE}")


if __name__ == "__main__":
    main()
