import json
import time
import random
import re
import os
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

# ==================== 參數設定 ====================
INPUT_FILE = "reports.json"       # 原始待查清單
DB_FILE = "tracking_db.json"      # 完整歷程資料庫 (唯一的 JSON)
BATCH_SIZE = 100                  # 每次排程執行上限筆數

FIRST_CHECK_DELAY_DAYS = 1  # 通報滿 2 天 (48 小時) 才進行初查
RECHECK_INTERVAL_DAYS = 1   # 仍在線者，距離上次檢查需間隔滿 1 天 (24 小時)

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

# 判斷官方流程是否已結束的特徵詞（結束後不再重複爬取數發部）
TERMINAL_KEYWORDS = [
    "通知Meta移除", "通知 Meta 移除", "高風險訊息", "非屬詐騙", "非詐騙", "未通過", "重複通報", "已結案"
]

def parse_dt(dt_str: str):
    if not dt_str or str(dt_str).strip() in ["-", "", "None"]:
        return None
    clean_str = " ".join(str(dt_str).strip().split())
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"]:
        try:
            return datetime.strptime(clean_str, fmt)
        except ValueError:
            continue
    return None

def fetch_raw_fraudbuster_stages(case_id: str, session: requests.Session) -> dict:
    """完整無損抓取打詐通報網各階段原始時間與文字"""
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
                
                # 判定是否已走完三階段或命中結案文字
                if len(stages) >= 3 or any(kw in all_desc for kw in TERMINAL_KEYWORDS) or (stages and stages[-1].get("is_complete")):
                    result["fb_is_final"] = True

        elif resp.status_code == 429:
            print("⚠️ 打詐通報網觸發 429 限流，暫停 15 秒...")
            time.sleep(15)
    except requests.RequestException:
        pass

    return result

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
        og_d = soup.find("meta", property="og:description")
        og_title = og_t["content"].strip() if og_t and "content" in og_t.attrs else ""
        og_desc = og_d["content"].strip() if og_d and "content" in og_d.attrs else ""

        if og_title in GENERIC_LOGIN_TITLES or title in GENERIC_LOGIN_TITLES or any(kw in og_desc for kw in GENERIC_LOGIN_DESCS):
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

    # 讀取累積資料庫
    history = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}

    now = datetime.now()
    eligible_items = []
    skipped_too_early = 0
    skipped_interval = 0

    for url, info in raw_input.items():
        hist_rec = history.get(url)

        # 已下架者不再重複爬取
        if hist_rec and hist_rec.get("threads_actual_status") == "Removed":
            continue

        reported_at_dt = parse_dt(info.get("reported_at", ""))
        
        # 首次檢查門檻（滿 2 天）
        if not hist_rec or not hist_rec.get("last_checked_at"):
            if reported_at_dt and (now - reported_at_dt) < timedelta(days=FIRST_CHECK_DELAY_DAYS):
                skipped_too_early += 1
                continue
            eligible_items.append((url, info))
        # 複查門檻（間隔滿 1 天）
        else:
            last_checked_dt = parse_dt(hist_rec.get("last_checked_at", ""))
            if last_checked_dt and (now - last_checked_dt) < timedelta(days=RECHECK_INTERVAL_DAYS):
                skipped_interval += 1
                continue
            eligible_items.append((url, info))

    batch = eligible_items[:BATCH_SIZE]
    print(f"⏰ 當前執行時段：{now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"• 待檢查目標：{len(eligible_items)} 筆 | 本次執行：{len(batch)} 筆")
    print(f"• 略過未滿 2 天：{skipped_too_early} 筆 | 略過複查未滿 1 天：{skipped_interval} 筆\n")

    if not batch:
        print("☕ 目前無任何到達檢查時間點的案件。")
        return

    session = requests.Session()

    for idx, (url, info) in enumerate(batch, 1):
        username = info.get("username", "")
        case_id = info.get("case_id", "")
        reported_at = info.get("reported_at", "")
        hist_rec = history.get(url, {})

        # 1. 數發部歷程獲取：若已結案直接沿用快取
        if hist_rec.get("fb_is_final") and hist_rec.get("timeline_stages"):
            fb_data = {
                "fb_url": hist_rec.get("fraudbuster_url", ""),
                "timeline_stages": hist_rec.get("timeline_stages", []),
                "fb_is_final": True
            }
            fb_source = "快取(已結案)"
        else:
            fb_data = fetch_raw_fraudbuster_stages(case_id, session) if case_id else {
                "fb_url": "", "timeline_stages": [], "fb_is_final": False
            }
            fb_source = "即時爬取"

        # 2. Threads 狀態檢測
        th_status = check_threads_status(url, username, session)

        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        first_checked_at = hist_rec.get("first_checked_at") or now_str
        check_count = (hist_rec.get("check_count") or 0) + 1
        
        # 完整記錄歷次偵測時間與狀態
        check_history = hist_rec.get("check_history", [])
        check_history.append({
            "check_index": check_count,
            "checked_at": now_str,
            "threads_status": th_status
        })

        # 記錄首次偵測到下架的時間點
        takedown_detected_at = hist_rec.get("takedown_detected_at")
        if th_status == "Removed" and not takedown_detected_at:
            takedown_detected_at = now_str

        # 3. 儲存原始資料（不預先做業務結論計算）
        history[url] = {
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

        stage_len = len(fb_data['timeline_stages'])
        print(f"[{idx:02d}/{len(batch):02d}] @{username:<18} | 數發部[{fb_source}]: 共 {stage_len} 階段 | Threads: {th_status:<7} (第 {check_count} 次偵測)")
        time.sleep(random.uniform(1.0, 1.8))

    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完整歷程資料已寫入：{DB_FILE}")

if __name__ == "__main__":
    main()
