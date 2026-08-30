#!/usr/bin/env python3
"""
scripts/batch_crawler.py - GitHub Actions 高並發大批次爬蟲 (純 JSON 資料庫版)
功能：
1. 從 reports.json 及 tracking_db.json 讀取待爬案件。
2. 自動相容 case_id / caseId 與 URL 帳號解析。
3. 優先重試未完整擷取/限流/未初查案件，並定期複查在線案件。
4. 使用 ThreadPoolExecutor 並發抓取 Threads 及 打詐通報網 狀態。
5. 爬取結果直接寫入 tracking_db.json。
"""

import json
import time
import random
import re
import os
import sqlite3
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

INPUT_FILE = "reports.json"
DB_FILE = "tracking_db.json"

DEFAULT_SQLITE_PATHS = [
    "/home/poan/Desktop/th/data/scam_hitter.db",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "th", "data", "scam_hitter.db"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scam_hitter.db"),
]

def load_cases_from_sqlite(db_path: str) -> dict:
    """從 SQLite 資料庫的 reported_history 表直接讀取待追蹤通報案件"""
    cases = {}
    if not os.path.exists(db_path):
        return cases
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT post_url, username, reported_at, details FROM reported_history ORDER BY id DESC")
        rows = cursor.fetchall()
        for post_url, username, reported_at, details_str in rows:
            if not post_url:
                continue
            case_id = ""
            risk = "中"
            if details_str:
                try:
                    d = json.loads(details_str)
                    case_id = d.get("caseId") or d.get("case_id") or ""
                    risk = d.get("risk") or "中"
                    if not reported_at:
                        reported_at = d.get("reported_at") or ""
                    if not username:
                        username = d.get("username") or ""
                except Exception:
                    pass
            if not username and "/@" in post_url:
                username = post_url.split("/@")[-1].split("?")[0].split("/")[0].strip()
            cases[post_url] = {
                "reported_at": reported_at,
                "case_id": case_id,
                "username": username,
                "risk": risk
            }
        conn.close()
    except Exception as e:
        print(f"⚠️ 讀取 SQLite 資料庫失敗 ({db_path}): {e}")
    return cases

RECHECK_INTERVAL_DAYS = 0.5   # 活躍中且已成功檢查過的案件，間隔滿 0.5 天複查

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
    if "T" in clean_str:
        clean_str = clean_str.split(".")[0].replace("T", " ")
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d"]:
        try:
            return datetime.strptime(clean_str, fmt)
        except ValueError:
            continue
    return None

def extract_case_info(url, info):
    username = info.get("username") or info.get("userName") or ""
    if not username and "/@" in url:
        username = url.split("/@")[-1].split("?")[0].split("/")[0].strip()
    case_id = info.get("case_id") or info.get("caseId") or ""
    reported_at = info.get("reported_at") or ""
    return username, case_id, reported_at

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
            time.sleep(2)
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
    
    username, case_id, reported_at = extract_case_info(url, info)

    # 1. 打詐通報網歷程
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
    # 若遇到 Rate Limited 但之前有有效狀態，保留上次狀態避免資料被沖掉
    if th_status == "Rate Limited" and hist_rec.get("threads_actual_status") in ["Active", "Removed"]:
        th_status = hist_rec.get("threads_actual_status")

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
    parser = argparse.ArgumentParser(description="High-throughput batch crawler for Fraudbuster cases (SQLite / JSON)")
    parser.add_argument("--batch-size", type=int, default=500, help="Number of cases to process in this run (default: 500)")
    parser.add_argument("--concurrency", type=int, default=10, help="Number of worker threads (default: 10)")
    parser.add_argument("--db", "--sqlite-db", dest="sqlite_db", default="", help="Path to SQLite database (default: auto-detect th/data/scam_hitter.db)")
    parser.add_argument("--export-reports-json", action="store_true", help="Export loaded SQLite cases to reports.json")
    parser.add_argument("--dry-run", action="store_true", help="Do not write results to disk")
    args = parser.parse_args()

    # 資料來源判定：優先讀取 SQLite 資料庫，若無則回退讀取 reports.json
    raw_input = {}
    db_source_desc = ""

    target_db = args.sqlite_db
    if not target_db:
        for p in DEFAULT_SQLITE_PATHS:
            if os.path.exists(p):
                target_db = p
                break

    if target_db and os.path.exists(target_db):
        raw_input = load_cases_from_sqlite(target_db)
        if raw_input:
            db_source_desc = f"SQLite 資料庫 ({target_db})"

    if not raw_input:
        if os.path.exists(INPUT_FILE):
            try:
                with open(INPUT_FILE, "r", encoding="utf-8") as f:
                    raw_input = json.load(f)
                db_source_desc = f"JSON 檔案 ({INPUT_FILE})"
            except Exception as e:
                print(f"❌ 讀取 {INPUT_FILE} 失敗: {e}")
                return
        else:
            print(f"❌ 找不到 SQLite 資料庫且無 {INPUT_FILE}！")
            return

    if args.export_reports_json and raw_input:
        with open(INPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(raw_input, f, ensure_ascii=False, indent=2)
        print(f"📦 已同步匯出 {len(raw_input):,} 筆案件至 {INPUT_FILE}")

    history = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    fresh_items = []     # 從未初查 或 待補抓資料
    recheck_items = []   # 正常在線需複查

    for url, info in raw_input.items():
        hist_rec = history.get(url, {})
        username, case_id, reported_at = extract_case_info(url, info)
        info["username"] = username
        info["case_id"] = case_id
        info["reported_at"] = reported_at

        th_status = hist_rec.get("threads_actual_status")
        has_stages = bool(hist_rec.get("timeline_stages"))

        # 已判定下架且已有通報網歷程則不重複爬
        if th_status == "Removed" and (has_stages or not case_id):
            continue

        # 判定是否需要重試/初查（從未初查、被限流Rate Limited、發生錯誤Error、或有case_id但沒抓到stages）
        is_incomplete = (
            not hist_rec or 
            not hist_rec.get("first_checked_at") or
            th_status in ["Rate Limited", "Error", "None", None] or
            str(th_status).startswith("HTTP_") or
            (case_id and not has_stages)
        )

        if is_incomplete:
            fresh_items.append((url, info, hist_rec, now_str))
        else:
            # 正常在線案件：需間隔滿 1 天複查
            last_checked_dt = parse_dt(hist_rec.get("last_checked_at", ""))
            if last_checked_dt and (now - last_checked_dt) < timedelta(days=RECHECK_INTERVAL_DAYS):
                continue
            recheck_items.append((url, info, hist_rec, now_str))

    # 排序複查項目：距離上次檢查時間最久者優先
    recheck_items.sort(key=lambda x: parse_dt(x[2].get("last_checked_at", "")) or datetime.min)

    # 優先處理已在線需複查的案件，剩餘配額給未初查/待補抓的新案件
    eligible_items = recheck_items + fresh_items
    batch = eligible_items[:args.batch_size]

    print(f"⏰ [Batch Crawler] 執行時間：{now_str}")
    print(f"• 資料庫來源：{db_source_desc} (總通報案件: {len(raw_input):,} 筆)")
    print(f"• 待檢查合格案件：{len(eligible_items)} 筆 (優先複查: {len(recheck_items)}, 待補齊/初查: {len(fresh_items)})")
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
    print(f"💾 已更新資料庫：{DB_FILE}")

if __name__ == "__main__":
    main()
