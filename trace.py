import json
import csv
import time
import random
import re
import os
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

# ==================== 參數設定 ====================
INPUT_FILE = "reports.json"                  # 原始待查清單
RESULT_JSON = "fraud_tracking_result.json"  # 累積追蹤歷史資料庫
OUTPUT_CSV = "fraud_tracking_summary.csv"   # 供 Dashboard 讀取的完整 CSV
BATCH_SIZE = 100                            # 每次執行上限筆數

FIRST_CHECK_DELAY_DAYS = 2  # 通報滿 2 天才進行首次檢查
RECHECK_INTERVAL_DAYS = 1   # 仍在線者，距離上次檢查需間隔滿 1 天

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

# 數發部/官方判定結案的終止關鍵字
FINAL_KEYWORDS = ["通知Meta移除", "通知 Meta 移除", "非屬詐騙", "未通過", "非詐騙", "重複通報", "已結案", "不予處理"]

def parse_dt(dt_str: str):
    """解析日期時間字串"""
    if not dt_str or dt_str.strip() in ["-", "", "None"]:
        return None
    clean_str = " ".join(dt_str.strip().split())
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(clean_str, fmt)
        except ValueError:
            continue
    return None

def parse_fraudbuster_case(case_id: str, session: requests.Session) -> dict:
    """爬取打詐通報網最新進度，並判定是否已達最終結案狀態"""
    url = f"https://fraudbuster.digiat.org.tw/accessibility/detail?listType=N&id={case_id}"
    data = {
        "fb_url": url,
        "latest_step_time": "",
        "latest_step_text": "未取得進度",
        "is_notified_meta": False,
        "fb_is_final": False  # 是否已完成官方流程
    }
    
    try:
        resp = session.get(url, headers=BROWSER_HEADERS, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            timeline = soup.select_one("section.caseTimeline")
            if timeline:
                events = []
                for li in timeline.select("ol.timeline li"):
                    t = li.find("time")
                    p = li.find("p")
                    t_txt = t.get_text(strip=True) if t else ""
                    p_txt = p.get_text(strip=True) if p else ""
                    is_complete_class = "complete" in li.get("class", [])
                    
                    if p_txt:
                        events.append({"time": t_txt, "desc": p_txt, "complete": is_complete_class})
                
                if events:
                    latest = events[-1]
                    data["latest_step_time"] = latest["time"]
                    data["latest_step_text"] = latest["desc"]
                    all_desc = " ".join([e["desc"] for e in events])
                    
                    # 1. 判定是否已通知 Meta 移除
                    data["is_notified_meta"] = "通知Meta移除" in all_desc or "通知 Meta 移除" in all_desc
                    
                    # 2. 判定三階段是否走完 (命中完成標籤或結案關鍵字)
                    is_final_text = any(kw in all_desc for kw in FINAL_KEYWORDS)
                    if latest.get("complete") or data["is_notified_meta"] or is_final_text:
                        data["fb_is_final"] = True

        elif resp.status_code == 429:
            print("⚠️ 打詐通報網觸發 429 限流，暫停 15 秒...")
            time.sleep(15)
    except requests.RequestException:
        pass
    return data

def check_threads_status(url: str, username: str, session: requests.Session) -> str:
    """檢查 Threads 帳號實際存活狀態"""
    try:
        resp = session.get(url, headers=SPIDER_HEADERS, timeout=8, allow_redirects=True)
        if resp.status_code == 404:
            return "Removed"
        elif resp.status_code == 429:
            time.sleep(20)
            return "Rate Limited"
        elif resp.status_code != 200:
            return f"HTTP_{resp.status_code}"

        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        og_t = soup.find("meta", property="og:title")
        og_title = og_t["content"].strip() if og_t and "content" in og_t.attrs else ""
        og_d = soup.find("meta", property="og:description")
        og_desc = og_d["content"].strip() if og_d and "content" in og_d.attrs else ""

        if og_title in GENERIC_LOGIN_TITLES or title in GENERIC_LOGIN_TITLES:
            return "Removed"
        if any(kw in og_desc for kw in GENERIC_LOGIN_DESCS):
            return "Removed"

        clean_user = username.lower().strip().lstrip("@")
        if clean_user and (clean_user in og_title.lower() or clean_user in title.lower()):
            return "Active"

        return "Removed"
    except requests.RequestException:
        return "Error"

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ 找不到檔案 {INPUT_FILE}")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw_input = json.load(f)

    # 讀取歷史資料庫
    history = {}
    if os.path.exists(RESULT_JSON):
        try:
            with open(RESULT_JSON, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}

    now = datetime.now()
    eligible_items = []
    skipped_too_early = 0
    skipped_interval = 0

    for url, info in raw_input.items():
        hist_rec = history.get(url)

        # 1. 已下架者永久跳過
        if hist_rec and hist_rec.get("threads_actual_status") == "Removed":
            continue

        reported_at_dt = parse_dt(info.get("reported_at", ""))
        
        # 2. 首次檢查判斷：通報滿 2 天 (48 小時)
        if not hist_rec or not hist_rec.get("last_checked_at"):
            if reported_at_dt:
                time_since_report = now - reported_at_dt
                if time_since_report < timedelta(days=FIRST_CHECK_DELAY_DAYS):
                    skipped_too_early += 1
                    continue
            eligible_items.append((url, info))

        # 3. 複查判斷：距離上次檢查滿 1 天 (24 小時)
        else:
            last_checked_dt = parse_dt(hist_rec.get("last_checked_at", ""))
            if last_checked_dt:
                time_since_check = now - last_checked_dt
                if time_since_check < timedelta(days=RECHECK_INTERVAL_DAYS):
                    skipped_interval += 1
                    continue
            eligible_items.append((url, info))

    batch = eligible_items[:BATCH_SIZE]
    print(f"⏰ 當前檢查時段：{now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"• 符合檢查條件：{len(eligible_items)} 筆 | 本次執行：{len(batch)} 筆")
    print(f"• 略過未滿 2 天通報：{skipped_too_early} 筆 | 略過複查間隔未滿 1 天：{skipped_interval} 筆\n")

    if not batch:
        print("☕ 目前無任何到達檢查時間點的案件。")
        regenerate_csv(history)
        return

    session = requests.Session()

    for idx, (url, info) in enumerate(batch, 1):
        username = info.get("username", "")
        case_id = info.get("case_id", "")
        reported_at = info.get("reported_at", "")
        hist_rec = history.get(url, {})

        # 1. 決定是否需要重新爬取數發部
        # 若歷史紀錄已標記官方已結案（fb_is_final == True），直接沿用舊資料
        if hist_rec.get("fb_is_final"):
            fb_info = {
                "fb_url": hist_rec.get("fraudbuster_url", ""),
                "latest_step_time": hist_rec.get("latest_step_time", ""),
                "latest_step_text": hist_rec.get("latest_progress", ""),
                "is_notified_meta": (hist_rec.get("notified_meta") == "是"),
                "fb_is_final": True
            }
            fb_source = "歷史快取(已結案)"
        else:
            fb_info = parse_fraudbuster_case(case_id, session) if case_id else {
                "fb_url": "", "latest_step_time": "", "latest_step_text": "無 Case ID",
                "is_notified_meta": False, "fb_is_final": False
            }
            fb_source = "即時爬取"

        # 2. 檢測 Threads 實際存活狀況
        th_status = check_threads_status(url, username, session)

        # 3. 結論判定
        if th_status == "Removed":
            verdict = "已確認下架" if fb_info["is_notified_meta"] else "已下架(官方審核中)"
        elif th_status == "Active":
            verdict = "仍存活(Meta未下架)" if fb_info["is_notified_meta"] else "處理中/未下架"
        else:
            verdict = th_status

        # 4. 回寫資料庫（保留 fb_is_final 狀態）
        history[url] = {
            "username": username,
            "case_id": case_id,
            "reported_at": reported_at,
            "latest_step_time": fb_info["latest_step_time"],
            "latest_progress": fb_info["latest_step_text"],
            "notified_meta": "是" if fb_info["is_notified_meta"] else "否",
            "fb_is_final": fb_info["fb_is_final"],
            "threads_actual_status": th_status,
            "takedown_verdict": verdict,
            "threads_url": url,
            "fraudbuster_url": fb_info["fb_url"],
            "last_checked_at": now.strftime("%Y-%m-%d %H:%M:%S")
        }

        print(f"[{idx:02d}/{len(batch):02d}] @{username:<18} | 數發部[{fb_source}]: {fb_info['latest_step_text'][:12]}... | Threads: {th_status:<7} -> 【{verdict}】")
        time.sleep(random.uniform(1.0, 1.8))

    # 存檔
    with open(RESULT_JSON, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    regenerate_csv(history)
    print(f"\n✅ 已將最新狀態回寫至 {RESULT_JSON} 與 {OUTPUT_CSV}")

def regenerate_csv(history: dict):
    """產出完整 CSV 報表"""
    fieldnames = [
        "username", "case_id", "reported_at", "latest_step_time",
        "latest_progress", "notified_meta", "threads_actual_status",
        "takedown_verdict", "threads_url", "fraudbuster_url", "last_checked_at"
    ]
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rec in history.values():
            writer.writerow({k: rec.get(k, "") for k in fieldnames})

if __name__ == "__main__":
    main()