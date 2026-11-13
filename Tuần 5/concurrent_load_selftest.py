#!/usr/bin/env python3
"""
concurrent_load_selftest.py — Bài 5 CSE3069
=============================================
Thí nghiệm tự đủ: ghi bản ghi giả lập vào DB trong 1 thread,
đồng thời chạy truy vấn nặng trong thread khác.
Không cần MQTT broker hay ESP32.

Cách dùng:
    python concurrent_load_selftest.py
"""

import os
import sqlite3
import threading
import time
import random
from datetime import datetime, timezone

DB_PATH     = os.path.join(os.path.dirname(__file__), "greenhouse.db")
RESULT_FILE = os.path.join(os.path.dirname(__file__), "concurrent_load_result.txt")

WRITE_INTERVAL_S = 1.0   # Ghi 1 bản ghi/giây (tương đương ESP32)
BASELINE_S       = 20    # Đo nền không có truy vấn nặng
LOADED_S         = 30    # Đo có truy vấn nặng song song


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def writer_thread(stop_evt: threading.Event, write_times: list, label: str):
    """Ghi bản ghi mới vào DB mỗi WRITE_INTERVAL_S giây, ghi lại thời điểm hoàn thành."""
    wconn = get_conn()
    base_ts = int(time.time())
    seq = 9000  # seq cao để phân biệt với dữ liệu seed

    while not stop_evt.is_set():
        t0 = time.perf_counter()
        seq += 1
        ts = base_ts + seq
        temp = round(random.uniform(24.0, 35.0), 1)
        hum  = round(random.uniform(55.0, 85.0), 1)
        wconn.execute(
            "INSERT INTO telemetry (device_id,ts,seq,temp,hum,rain,unit_temp,unit_hum,unit_rain,valid,fsm_state,pump,received_at,suspect) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("node01_load_test", ts, seq, temp, hum, 0, "C", "%", "binary", 1, "RUNNING", 100, int(time.time()), 0)
        )
        wconn.commit()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        write_times.append(elapsed_ms)
        # Ngủ còn lại của interval
        sleep_s = max(0, WRITE_INTERVAL_S - elapsed_ms / 1000)
        stop_evt.wait(timeout=sleep_s)

    wconn.close()


def heavy_query_thread(stop_evt: threading.Event, query_times: list):
    """Chạy truy vấn nặng liên tục mỗi 0.5 giây."""
    rconn = get_conn()   # Kết nối riêng (tách tải)

    while not stop_evt.is_set():
        t0 = time.perf_counter()
        rconn.execute("""
            SELECT device_id, COUNT(*) AS n,
                   AVG(temp) AS avg_temp, AVG(hum) AS avg_hum,
                   MIN(temp), MAX(temp)
            FROM   telemetry
            WHERE  valid = 1
            GROUP  BY device_id
        """).fetchall()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        query_times.append(elapsed_ms)
        stop_evt.wait(timeout=max(0, 0.5 - elapsed_ms / 1000))

    rconn.close()


def run_phase(label: str, duration: int, with_heavy: bool) -> dict:
    print(f"\n--- Phase: {label} ({'CÓ' if with_heavy else 'KHÔNG CÓ'} truy vấn nặng) ---")
    print(f"    Thời gian đo: {duration}s")

    stop_evt    = threading.Event()
    write_times = []
    query_times = []

    # Thread ghi
    wt = threading.Thread(target=writer_thread, args=(stop_evt, write_times, label), daemon=True)
    wt.start()

    # Thread đọc nặng (chỉ phase loaded)
    qt = None
    if with_heavy:
        qt = threading.Thread(target=heavy_query_thread, args=(stop_evt, query_times), daemon=True)
        qt.start()

    time.sleep(duration)
    stop_evt.set()
    wt.join(timeout=3)
    if qt:
        qt.join(timeout=3)

    avg_write = sum(write_times) / len(write_times) if write_times else None
    avg_query = sum(query_times) / len(query_times) if query_times else None

    if avg_write is not None:
        print(f"    Ghi: {len(write_times)} lần, avg = {avg_write:.2f}ms/lần")
    if avg_query is not None:
        print(f"    Truy vấn nặng: {len(query_times)} lần, avg = {avg_query:.2f}ms/lần")

    return {
        "label":       label,
        "with_heavy":  with_heavy,
        "duration_s":  duration,
        "write_times": write_times,
        "query_times": query_times,
        "avg_write_ms": avg_write,
        "avg_query_ms": avg_query,
    }


def generate_report(results: list) -> str:
    baseline = next((r for r in results if not r["with_heavy"]), None)
    loaded   = next((r for r in results if r["with_heavy"]),    None)

    lines = [
        "=" * 60,
        "KẾT QUẢ THÍ NGHIỆM TẢI ĐỒNG THỜI — CSE3069 Bài 5",
        f"Thời điểm: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "Phương pháp: Self-contained (ghi giả lập + truy vấn nặng song song)",
        "=" * 60,
    ]

    for r in results:
        lines.append(f"\n[{r['label']}]")
        if r["avg_write_ms"] is not None:
            lines.append(f"  Số lần ghi:          {len(r['write_times'])}")
            lines.append(f"  Avg write latency:   {r['avg_write_ms']:.3f}ms")
            lines.append(f"  Max write latency:   {max(r['write_times']):.3f}ms")
        if r["avg_query_ms"] is not None:
            lines.append(f"  Số lần query nặng:   {len(r['query_times'])}")
            lines.append(f"  Avg query latency:   {r['avg_query_ms']:.2f}ms")
            lines.append(f"  Max query latency:   {max(r['query_times']):.2f}ms")

    lines.append("\n--- So sánh ---")
    if baseline and loaded and baseline["avg_write_ms"] and loaded["avg_write_ms"]:
        delta = loaded["avg_write_ms"] - baseline["avg_write_ms"]
        pct   = delta / baseline["avg_write_ms"] * 100
        lines.append(f"  Baseline avg write:  {baseline['avg_write_ms']:.3f}ms")
        lines.append(f"  Loaded   avg write:  {loaded['avg_write_ms']:.3f}ms")
        lines.append(f"  Delta:               {delta:+.3f}ms  ({pct:+.1f}%)")
        if abs(pct) < 15:
            lines.append("  → Kết luận: Truy vấn nặng KHÔNG ảnh hưởng đáng kể tới ghi dữ liệu.")
        else:
            lines.append(f"  → Kết luận: Có ảnh hưởng ({pct:+.1f}%). Nên tách kết nối đọc/ghi.")

    lines.append("\n--- Giải thích thiết kế tách tải ---")
    lines.append("  ingest_service.py:        kết nối SQLite riêng (write_conn) + _db_lock")
    lines.append("  query/concurrent_test:    kết nối SQLite riêng (heavy_conn)")
    lines.append("  SQLite WAL mode:          reader KHÔNG block writer, writer KHÔNG block reader")
    lines.append("  Kết quả: ghi dữ liệu và truy vấn nặng chạy độc lập, không tranh chấp.")
    lines.append("\n  Nếu scale lên (nhiều node, >10 req/s): chuyển sang PostgreSQL")
    lines.append("  với connection pool riêng cho read-service và write-service.")
    lines.append("=" * 60)

    return "\n".join(lines)


def main():
    if not os.path.exists(DB_PATH):
        print(f"[LỖI] Không tìm thấy DB: {DB_PATH}")
        return

    results = []
    results.append(run_phase("Baseline (không có truy vấn nặng)", BASELINE_S, False))
    results.append(run_phase("Loaded (có truy vấn nặng liên tục)", LOADED_S, True))

    report = generate_report(results)
    print("\n" + report)

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"\nKết quả đã lưu: {RESULT_FILE}")

    # Dọn dẹp bản ghi test
    conn = get_conn()
    deleted = conn.execute("DELETE FROM telemetry WHERE device_id='node01_load_test'").rowcount
    conn.commit()
    conn.close()
    print(f"Đã xóa {deleted} bản ghi test khỏi DB.")


if __name__ == "__main__":
    main()
