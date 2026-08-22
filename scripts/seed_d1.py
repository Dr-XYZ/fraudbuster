#!/usr/bin/env python3
"""
scripts/seed_d1.py - 將本機舊格式 (reports.json & tracking_db.json) 轉碼並匯入 Cloudflare D1 資料庫
使用方式:
  1. 自動生成 SQL 檔案:
     python3 scripts/seed_d1.py --export-sql

  2. 透過 Cloudflare REST API 直接匯入 D1:
     python3 scripts/seed_d1.py --account-id <ACCOUNT_ID> --database-id <D1_DATABASE_ID> --token <API_TOKEN>
"""

import sys
import os
import json
import argparse
import requests

REPORTS_FILE = "reports.json"
DB_FILE = "tracking_db.json"
OUTPUT_SQL = "seed_data.sql"

def escape_sql(val):
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    clean_val = str(val).replace("'", "''")
    return f"'{clean_val}'"

def generate_sql_file():
    print("🔄 正在將舊 JSON 資料轉換為 D1 SQL 格式 (seed_data.sql)...")
    sql_lines = []
    sql_lines.append("-- Auto-generated Cloudflare D1 SQL seed script\n")
    sql_lines.append("""
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
""")

    reports_count = 0
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "r", encoding="utf-8") as f:
            reports_data = json.load(f)

        items = list(reports_data.items()) if isinstance(reports_data, dict) else reports_data
        sql_lines.append("-- Inserting reports data\n")
        for url, r in items:
            username = escape_sql(r.get("username", ""))
            case_id = escape_sql(r.get("case_id", ""))
            reported_at = escape_sql(r.get("reported_at", ""))
            url_sql = escape_sql(url)

            sql_lines.append(
                f"INSERT INTO reports (url, username, case_id, reported_at) VALUES ({url_sql}, {username}, {case_id}, {reported_at}) ON CONFLICT(url) DO UPDATE SET username=excluded.username, case_id=excluded.case_id, reported_at=excluded.reported_at;\n"
            )
            reports_count += 1

    cases_count = 0
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            db_data = json.load(f)

        items = list(db_data.items()) if isinstance(db_data, dict) else db_data
        sql_lines.append("\n-- Inserting cases tracking data\n")
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

            sql_lines.append(
                f"INSERT INTO cases (url, username, case_id, reported_at, fraudbuster_url, fb_is_final, timeline_stages, threads_actual_status, first_checked_at, last_checked_at, takedown_detected_at, check_count, check_history, updated_at) VALUES ({url_sql}, {username}, {case_id}, {reported_at}, {fraudbuster_url}, {fb_is_final}, {stages_json}, {threads_actual_status}, {first_checked_at}, {last_checked_at}, {takedown_detected_at}, {check_count}, {checks_json}, {last_checked_at}) ON CONFLICT(url) DO UPDATE SET username=excluded.username, case_id=excluded.case_id, reported_at=excluded.reported_at, fraudbuster_url=excluded.fraudbuster_url, fb_is_final=excluded.fb_is_final, timeline_stages=excluded.timeline_stages, threads_actual_status=excluded.threads_actual_status, first_checked_at=excluded.first_checked_at, last_checked_at=excluded.last_checked_at, takedown_detected_at=excluded.takedown_detected_at, check_count=excluded.check_count, check_history=excluded.check_history, updated_at=excluded.updated_at;\n"
            )
            cases_count += 1

    with open(OUTPUT_SQL, "w", encoding="utf-8") as f:
        f.writelines(sql_lines)

    print(f"✅ 已成功將舊格式資料轉換產出為 `{OUTPUT_SQL}` ({os.path.getsize(OUTPUT_SQL)/1024/1024:.2f} MB)！")
    print(f"  - 轉換 reports 筆數: {reports_count}")
    print(f"  - 轉換 cases 筆數: {cases_count}")

def execute_d1_sql(account_id, database_id, token, sql_query, params=None):
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "sql": sql_query,
        "params": params or []
    }
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code == 200 and resp.json().get("success"):
        return True, resp.json()
    else:
        return False, resp.text

def push_data_via_api(account_id, database_id, token):
    # 確保 schema 表格存在
    create_tables = """
    CREATE TABLE IF NOT EXISTS reports (
        url TEXT PRIMARY KEY, username TEXT, case_id TEXT, reported_at TEXT
    );
    CREATE TABLE IF NOT EXISTS cases (
        url TEXT PRIMARY KEY, username TEXT, case_id TEXT, reported_at TEXT,
        fraudbuster_url TEXT, fb_is_final INTEGER DEFAULT 0, timeline_stages TEXT,
        threads_actual_status TEXT DEFAULT 'Active', first_checked_at TEXT,
        last_checked_at TEXT, takedown_detected_at TEXT, check_count INTEGER DEFAULT 0,
        check_history TEXT, updated_at TEXT
    );
    """
    execute_d1_sql(account_id, database_id, token, create_tables)

    # 1. 匯入 reports.json
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "r", encoding="utf-8") as f:
            reports_data = json.load(f)
        items = list(reports_data.items()) if isinstance(reports_data, dict) else reports_data
        print(f"🚀 正在發送 REST API 匯入 {len(items)} 筆 reports 至 Cloudflare D1...")
        count = 0
        for url, r in items:
            sql = "INSERT INTO reports (url, username, case_id, reported_at) VALUES (?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET username=excluded.username, case_id=excluded.case_id, reported_at=excluded.reported_at;"
            params = [url, r.get("username", ""), r.get("case_id", ""), r.get("reported_at", "")]
            ok, _ = execute_d1_sql(account_id, database_id, token, sql, params)
            if ok: count += 1
        print(f"✅ 成功寫入 {count}/{len(items)} 筆 reports！")

    # 2. 匯入 tracking_db.json
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            db_data = json.load(f)
        items = list(db_data.items()) if isinstance(db_data, dict) else db_data
        print(f"🚀 正在發送 REST API 匯入 {len(items)} 筆 cases 歷程紀錄至 Cloudflare D1...")
        count = 0
        for url, r in items:
            sql = """
            INSERT INTO cases (
                url, username, case_id, reported_at, fraudbuster_url, fb_is_final,
                timeline_stages, threads_actual_status, first_checked_at, last_checked_at,
                takedown_detected_at, check_count, check_history, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                username = excluded.username, case_id = excluded.case_id, reported_at = excluded.reported_at,
                fraudbuster_url = excluded.fraudbuster_url, fb_is_final = excluded.fb_is_final,
                timeline_stages = excluded.timeline_stages, threads_actual_status = excluded.threads_actual_status,
                first_checked_at = excluded.first_checked_at, last_checked_at = excluded.last_checked_at,
                takedown_detected_at = excluded.takedown_detected_at, check_count = excluded.check_count,
                check_history = excluded.check_history, updated_at = excluded.updated_at;
            """
            stages = r.get("timeline_stages", [])
            checks = r.get("check_history", [])
            params = [
                r.get("threads_url", url), r.get("username", ""), r.get("case_id", ""), r.get("reported_at", ""),
                r.get("fraudbuster_url", ""), 1 if r.get("fb_is_final") else 0,
                json.dumps(stages, ensure_ascii=False) if isinstance(stages, list) else stages,
                r.get("threads_actual_status", "Active"), r.get("first_checked_at", ""), r.get("last_checked_at", ""),
                r.get("takedown_detected_at", ""), r.get("check_count", 1),
                json.dumps(checks, ensure_ascii=False) if isinstance(checks, list) else checks,
                r.get("last_checked_at", "")
            ]
            ok, _ = execute_d1_sql(account_id, database_id, token, sql, params)
            if ok: count += 1
        print(f"✅ 成功寫入 {count}/{len(items)} 筆 cases 歷程！")

def main():
    parser = argparse.ArgumentParser(description="Convert old JSON data format to Cloudflare D1 format & seed DB")
    parser.add_argument("--export-sql", action="store_true", help="Export data as seed_data.sql")
    parser.add_argument("--account-id", help="Cloudflare Account ID")
    parser.add_argument("--database-id", help="Cloudflare D1 Database ID")
    parser.add_argument("--token", help="Cloudflare API Token")

    args = parser.parse_args()

    # 預設總是產出 seed_data.sql
    generate_sql_file()

    if args.account_id and args.database_id and args.token:
        push_data_via_api(args.account_id, args.database_id, args.token)

if __name__ == "__main__":
    main()
