#!/usr/bin/env python3
"""
scripts/batch_crawler.py - GitHub Actions 高並發大批次爬蟲
功能：
1. 從 reports.json 及 tracking_db.json (或 Cloudflare D1) 讀取待爬案件。
2. 優先處理「尚未初查」的新案件，其次處理「需要複查」的活躍案件。
3. 使用 ThreadPoolExecutor 並發抓取 Threads 及 打詐通報網 狀態。
4. 輸出更新 SQL 檔案並直接寫入 tracking_db.json 與 (選填) Cloudflare D1。
"""

import json
import time
import random
import re
import os
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import requests
from bs4 import BeautifulSoup

INPUT_FILE = "reports.json"
DB_FILE = "tracking_db.json"
OUTPUT_SQL = "crawler_update.sql"

FIRST_CHECK_DELAY_DAYS = 1  # 滿 1 天可初查
RECHECK_INTERVAL_DAYS = 1   # 活躍中案件間隔滿 1 天複查

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

SPIDER_HEADERS = {
    "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.html)",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

GENERIC_LOGIN_TITLES = ["Threads • 登入", "Threads • Log in", "Threads - 登入", "Threads", "Instagram"]
GENERIC_LOGIN_DESCS = ["加入 Threads 即可分享意見", "Say more on Threads", "使用你的 Instagram 登入"]
TERMINAL_KEYWORDS = [
    "通知Meta移除", "通知 Meta 移除", "高風險訊息", "非屬詐騙", "非詐騙", "未通過", "重複通報", "已結案"
]

def parse_dt(dt_str: str):
    if not dt_str or str(dt_str).strip() in ["-", "", "None"]:
        return None
    clean_str = " ".join(str(dt_str).strip().split())
    # Handle ISO formats
    if "T" in clean_str:
        clean_str = clean_str.split(".")[0].replace("T", " ")
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d"]:
        try:
            return datetime.strptime(clean_str, fmt)
        except ValueError:
            continue
    return None

def escape_sql(val):
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    clean_val = str(val).replace("'", "''")
    return f"'{clean_val}'"

def fetch_raw_fraudbuster_stages(case_id: str, session: requests.Session) -> dict:
    url = f"https://fraudbuster.digiat.org.tw/accessibility/detail?listType=N&id={case_id}"
    result = {
        "fb_url": url,
        "timeline_stages": [],
        "fb_is_final": False
    }
    
    try:
        resp = session.get(url, headers=BROWSER_HEADERS, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            timeline = soup.select_one("section.caseTimeline")
            if timeline:
                stages = []
                for idx, li in enumerate(timeline.select("ol.timeline li"), 1):
                    t_el = li.find("time")
                    p_el = li.find("p")
                    t_txt = t_el.get_text(strip=True) if t_el else ""
                    p_txt = p_el.get_text(strip=True) if p_el else ""
                    is_complete = "complete" in li.get("class", [])
                    
                    if p_txt:
                        stages.append({
                            "stage_num": idx,
                            "time": t_txt,
                            "desc": p_txt,
                            "is_complete": is_complete
                        })
                
                result["timeline_stages"] = stages
                all_desc = " ".join([s["desc"] for s in stages])
                
                if len(stages) >= 3 or any(kw in all_desc for kw in TERMINAL_KEYWORDS) or (stages and stages[-1].get("is_complete")):
                    result["fb_is_final"] = True
        elif resp.status_code == 429:
            time.sleep(5)
    except requests.RequestException:
        pass

    return result

def check_threads_status(url: str, username: str, session: requests.Session) -> str:
    try:
        resp = session.get(url, headers=SPIDER_HEADERS, timeout=8, allow_redirects=True)
        if resp.status_code == 404:
            return "Removed"
        elif resp.status_code == 429:
            return "Rate Limited"
        elif resp.status_code != 200:
            return f"HTTP_{resp.status_code}"

        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        og_t = soup.find("meta", property="og:title")
        og_d = soup.find("meta", property="og:description")
        og_title = og_t["content"].strip() if og_t and "content" in og_t.attrs else ""
        og_desc = og_d["content"].strip() if og_d and "content" in og_d.attrs else ""

        if og_title in GENERIC_LOGIN_TITLES or title in GENERIC_LOGIN_TITLES or any(kw in og_desc for kw in GENERIC_LOGIN_DESCS):
            return "Removed"

        clean_user = (username or "").lower().strip().lstrip("@")
        if clean_user and (clean_user in og_title.lower() or clean_user in title.lower()):
            return "Active"

        return "Removed"
    except requests.RequestException:
        return "Error"

def process_single_case(item_tuple):
    url, info, hist_rec, now_str = item_tuple
    session = requests.Session()
    
    username = info.get("username", "")
    case_id = info.get("case_id", "")
    reported_at = info.get("reported_at", "")

    # 1. 打詐通報網
    if hist_rec.get("fb_is_final") and hist_rec.get("timeline_stages"):
        fb_data = {
            "fb_url": hist_rec.get("fraudbuster_url", ""),
            "timeline_stages": hist_rec.get("timeline_stages", []),
            "fb_is_final": True
        }
    else:
        fb_data = fetch_raw_fraudbuster_stages(case_id, session) if case_id else {
            "fb_url": "", "timeline_stages": [], "fb_is_final": False
        }

    # 2. Threads 狀態檢測
    th_status = check_threads_status(url, username, session)

    first_checked_at = hist_rec.get("first_checked_at") or now_str
    check_count = (hist_rec.get("check_count") or 0) + 1
    
    check_history = list(hist_rec.get("check_history", []))
    check_history.append({
        "check_index": check_count,
        "checked_at": now_str,
        "threads_status": th_status
    })

    takedown_detected_at = hist_rec.get("takedown_detected_at")
    if th_status == "Removed" and not takedown_detected_at:
        takedown_detected_at = now_str

    updated_record = {
        "username": username,
        "case_id": case_id,
        "reported_at": reported_at,
        "fraudbuster_url": fb_data["fb_url"],
        "fb_is_final": fb_data["fb_is_final"],
        "timeline_stages": fb_data["timeline_stages"],
        "threads_actual_status": th_status,
        "first_checked_at": first_checked_at,
        "last_checked_at": now_str,
        "takedown_detected_at": takedown_detected_at or "",
        "check_count": check_count,
        "check_history": check_history,
        "threads_url": url
    }

    return url, updated_record

def main():
    parser = argparse.ArgumentParser(description="High-throughput batch crawler for Fraudbuster cases")
    parser.add_argument("--batch-size", type=int, default=500, help="Number of cases to process in this run (default: 500)")
    parser.add_argument("--concurrency", type=int, default=10, help="Number of worker threads (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write results to disk or D1")
    parser.add_argument("--export-sql", action="store_true", default=True, help="Export update SQL file for D1")
    args = parser.parse_args()

    if not os.path.exists(INPUT_FILE):
        print(f"❌ 找不到檔案 {INPUT_FILE}")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw_input = json.load(f)

    history = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    fresh_items = []     # 從未初查
    recheck_items = []   # 需複查

    for url, info in raw_input.items():
        hist_rec = history.get(url, {})

        # 已判定下架則不重複爬
        if hist_rec.get("threads_actual_status") == "Removed":
            continue

        reported_at_dt = parse_dt(info.get("reported_at", ""))

        if not hist_rec or not hist_rec.get("first_checked_at"):
            # 首次檢查門檻 (滿 1 天)
            if reported_at_dt and (now - reported_at_dt) < timedelta(days=FIRST_CHECK_DELAY_DAYS):
                continue
            fresh_items.append((url, info, hist_rec, now_str))
        else:
            last_checked_dt = parse_dt(hist_rec.get("last_checked_at", ""))
            if last_checked_dt and (now - last_checked_dt) < timedelta(days=RECHECK_INTERVAL_DAYS):
                continue
            recheck_items.append((url, info, hist_rec, now_str))

    # 優先處理未曾初查的新案件，剩餘配額給需複查案件
    eligible_items = fresh_items + recheck_items
    batch = eligible_items[:args.batch_size]

    print(f"⏰ [Batch Crawler] 執行時間：{now_str}")
    print(f"• 待檢查合格案件：{len(eligible_items)} 筆 (未初查: {len(fresh_items)}, 需複查: {len(recheck_items)})")
    print(f"• 本次並發處理：{len(batch)} 筆 (並發數: {args.concurrency})")

    if not batch:
        print("☕ 目前無任何符合檢查時間條件的案件。")
        return

    start_time = time.time()
    results = {}
    completed_count = 0

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {executor.submit(process_single_case, item): item[0] for item in batch}
        for future in as_completed(futures):
            url = futures[future]
            try:
                url_res, updated_rec = future.result()
                results[url_res] = updated_rec
                completed_count += 1
                if completed_count % 25 == 0 or completed_count == len(batch):
                    elapsed = time.time() - start_time
                    rate = completed_count / elapsed if elapsed > 0 else 0
                    print(f"  ⚡ 已完成: {completed_count}/{len(batch)} 筆 ({rate:.1f} 筆/秒)...")
            except Exception as e:
                print(f"  ❌ 處理失敗 [{url}]: {e}")

    elapsed_total = time.time() - start_time
    print(f"\n🎉 批次處理完成！耗時: {elapsed_total:.2f} 秒 (平均 {(len(results)/elapsed_total if elapsed_total else 0):.1f} 筆/秒)")

    if args.dry_run:
        print("🔍 Dry run 模式，不寫入檔案。")
        return

    # 1. 更新本機 tracking_db.json
    for url, rec in results.items():
        history[url] = rec

    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"💾 已更新本機資料庫：{DB_FILE}")

    # 2. 產出 D1 專用增量 SQL 更新檔
    sql_stmts = []
    for url, r in results.items():
        username = escape_sql(r.get("username", ""))
        case_id = escape_sql(r.get("case_id", ""))
        reported_at = escape_sql(r.get("reported_at", ""))
        fraudbuster_url = escape_sql(r.get("fraudbuster_url", ""))
        fb_is_final = 1 if r.get("fb_is_final") else 0
        stages = r.get("timeline_stages", [])
        stages_json = escape_sql(json.dumps(stages, ensure_ascii=False))
        threads_actual_status = escape_sql(r.get("threads_actual_status", "Active"))
        first_checked_at = escape_sql(r.get("first_checked_at", ""))
        last_checked_at = escape_sql(r.get("last_checked_at", ""))
        takedown_detected_at = escape_sql(r.get("takedown_detected_at", ""))
        check_count = r.get("check_count", 1)
        checks = r.get("check_history", [])
        checks_json = escape_sql(json.dumps(checks, ensure_ascii=False))
        url_sql = escape_sql(url)

        stmt = f"INSERT INTO cases (url, username, case_id, reported_at, fraudbuster_url, fb_is_final, timeline_stages, threads_actual_status, first_checked_at, last_checked_at, takedown_detected_at, check_count, check_history, updated_at) VALUES ({url_sql}, {username}, {case_id}, {reported_at}, {fraudbuster_url}, {fb_is_final}, {stages_json}, {threads_actual_status}, {first_checked_at}, {last_checked_at}, {takedown_detected_at}, {check_count}, {checks_json}, {last_checked_at}) ON CONFLICT(url) DO UPDATE SET username=excluded.username, case_id=excluded.case_id, reported_at=excluded.reported_at, fraudbuster_url=excluded.fraudbuster_url, fb_is_final=excluded.fb_is_final, timeline_stages=excluded.timeline_stages, threads_actual_status=excluded.threads_actual_status, first_checked_at=excluded.first_checked_at, last_checked_at=excluded.last_checked_at, takedown_detected_at=excluded.takedown_detected_at, check_count=excluded.check_count, check_history=excluded.check_history, updated_at=excluded.updated_at;\n"
        sql_stmts.append(stmt)

    with open(OUTPUT_SQL, "w", encoding="utf-8") as f:
        f.writelines(sql_stmts)
    print(f"📄 已產出 D1 增量更新 SQL：`{OUTPUT_SQL}` (共 {len(sql_stmts)} 筆)")

if __name__ == "__main__":
    main()

