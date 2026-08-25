#!/usr/bin/env python3
"""
scripts/seed_d1.py - 將 reports.json 與 tracking_db.json 轉碼並匯入 Cloudflare D1 資料庫
使用方式:
  1. 生成單一/分塊 SQL 檔案:
     python3 scripts/seed_d1.py --export-sql --chunk-size 1000
  2. 僅生成 reports SQL:
     python3 scripts/seed_d1.py --export-sql --reports-only
  3. 透過 Cloudflare REST API 直接匯入 D1:
     python3 scripts/seed_d1.py --account-id <ACCOUNT_ID> --database-id <D1_DATABASE_ID> --token <API_TOKEN>
"""

import sys
import os
import glob
import json
import argparse
import urllib.request
import urllib.parse

REPORTS_FILE = "reports.json"
DB_FILE = "tracking_db.json"
OUTPUT_SQL = "seed_data.sql"
CHUNKS_DIR = "d1_chunks"

def escape_sql(val):
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    clean_val = str(val).replace("'", "''")
    return f"'{clean_val}'"

def create_schema_sql():
    return """
CREATE TABLE IF NOT EXISTS reports (
    url TEXT PRIMARY KEY,
    username TEXT,
    case_id TEXT,
    reported_at TEXT
);

CREATE TABLE IF NOT EXISTS cases (
    url TEXT PRIMARY KEY,
    username TEXT,
    case_id TEXT,
    reported_at TEXT,
    fraudbuster_url TEXT,
    fb_is_final INTEGER DEFAULT 0,
    timeline_stages TEXT,
    threads_actual_status TEXT DEFAULT 'Active',
    first_checked_at TEXT,
    last_checked_at TEXT,
    takedown_detected_at TEXT,
    check_count INTEGER DEFAULT 0,
    check_history TEXT,
    updated_at TEXT
);
"""

def generate_sql_files(chunk_size=1000, reports_only=False):
    print("🔄 正在讀取 JSON 資料並產出 D1 SQL 語句...")
    os.makedirs(CHUNKS_DIR, exist_ok=True)
    
    # 清理舊的 chunks
    for f in glob.glob(f"{CHUNKS_DIR}/*.sql"):
        os.remove(f)

    # 處理 reports
    reports_stmts = []
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "r", encoding="utf-8") as f:
            reports_data = json.load(f)
        items = list(reports_data.items()) if isinstance(reports_data, dict) else reports_data
        for url, r in items:
            username = escape_sql(r.get("username", ""))
            case_id = escape_sql(r.get("case_id", ""))
            reported_at = escape_sql(r.get("reported_at", ""))
            url_sql = escape_sql(url)
            stmt = f"INSERT INTO reports (url, username, case_id, reported_at) VALUES ({url_sql}, {username}, {case_id}, {reported_at}) ON CONFLICT(url) DO UPDATE SET username=excluded.username, case_id=excluded.case_id, reported_at=excluded.reported_at;\n"
            reports_stmts.append(stmt)
    
    print(f"📊 總計 reports 筆數: {len(reports_stmts)}")

    # 處理 cases
    cases_stmts = []
    if not reports_only and os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            db_data = json.load(f)
        items = list(db_data.items()) if isinstance(db_data, dict) else db_data
        for url, r in items:
            username = escape_sql(r.get("username", ""))
            case_id = escape_sql(r.get("case_id", ""))
            reported_at = escape_sql(r.get("reported_at", ""))
            fraudbuster_url = escape_sql(r.get("fraudbuster_url", ""))
            fb_is_final = 1 if r.get("fb_is_final") else 0
            stages = r.get("timeline_stages", [])
            stages_json = escape_sql(json.dumps(stages, ensure_ascii=False) if isinstance(stages, list) else stages)
            threads_actual_status = escape_sql(r.get("threads_actual_status", "Active"))
            first_checked_at = escape_sql(r.get("first_checked_at", ""))
            last_checked_at = escape_sql(r.get("last_checked_at", ""))
            takedown_detected_at = escape_sql(r.get("takedown_detected_at", ""))
            check_count = r.get("check_count", 1)
            checks = r.get("check_history", [])
            checks_json = escape_sql(json.dumps(checks, ensure_ascii=False) if isinstance(checks, list) else checks)
            url_sql = escape_sql(r.get("threads_url", url))

            stmt = f"INSERT INTO cases (url, username, case_id, reported_at, fraudbuster_url, fb_is_final, timeline_stages, threads_actual_status, first_checked_at, last_checked_at, takedown_detected_at, check_count, check_history, updated_at) VALUES ({url_sql}, {username}, {case_id}, {reported_at}, {fraudbuster_url}, {fb_is_final}, {stages_json}, {threads_actual_status}, {first_checked_at}, {last_checked_at}, {takedown_detected_at}, {check_count}, {checks_json}, {last_checked_at}) ON CONFLICT(url) DO UPDATE SET username=excluded.username, case_id=excluded.case_id, reported_at=excluded.reported_at, fraudbuster_url=excluded.fraudbuster_url, fb_is_final=excluded.fb_is_final, timeline_stages=excluded.timeline_stages, threads_actual_status=excluded.threads_actual_status, first_checked_at=excluded.first_checked_at, last_checked_at=excluded.last_checked_at, takedown_detected_at=excluded.takedown_detected_at, check_count=excluded.check_count, check_history=excluded.check_history, updated_at=excluded.updated_at;\n"
            cases_stmts.append(stmt)
        print(f"📊 總計 cases 筆數: {len(cases_stmts)}")

    all_stmts = reports_stmts + cases_stmts
    
    # 產出單一全量 SQL 檔
    with open(OUTPUT_SQL, "w", encoding="utf-8") as f:
        f.write(create_schema_sql())
        f.writelines(all_stmts)
    print(f"✅ 已產出完整 SQL 檔案: `{OUTPUT_SQL}` ({os.path.getsize(OUTPUT_SQL)/1024/1024:.2f} MB)")

    # 產出 Chunk SQL 檔 (用於 Wrangler D1 批次安全執行)
    chunk_index = 1
    # 建立 schema 專用 chunk
    with open(f"{CHUNKS_DIR}/000_schema.sql", "w", encoding="utf-8") as f:
        f.write(create_schema_sql())

    for i in range(0, len(all_stmts), chunk_size):
        chunk_file = f"{CHUNKS_DIR}/chunk_{chunk_index:03d}.sql"
        with open(chunk_file, "w", encoding="utf-8") as f:
            f.writelines(all_stmts[i:i+chunk_size])
        chunk_index += 1

    print(f"📦 已產出 {chunk_index - 1} 個分塊 SQL 檔案至 `{CHUNKS_DIR}/` (每塊 {chunk_size} 筆)")

def execute_d1_sql(account_id, database_id, token, sql_query, params=None):
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = json.dumps({
        "sql": sql_query,
        "params": params or []
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if resp.status == 200 and data.get("success"):
                return True, data
            return False, data
    except Exception as e:
        return False, str(e)

def main():
    parser = argparse.ArgumentParser(description="Convert JSON data format to Cloudflare D1 format & seed DB")
    parser.add_argument("--export-sql", action="store_true", help="Export data as seed_data.sql and chunks")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Chunk size for SQL export (default: 1000)")
    parser.add_argument("--reports-only", action="store_true", help="Only export reports table")
    parser.add_argument("--account-id", help="Cloudflare Account ID")
    parser.add_argument("--database-id", help="Cloudflare D1 Database ID")
    parser.add_argument("--token", help="Cloudflare API Token")

    args = parser.parse_args()

    if args.export_sql or not (args.account_id and args.database_id and args.token):
        generate_sql_files(chunk_size=args.chunk_size, reports_only=args.reports_only)

if __name__ == "__main__":
    main()
