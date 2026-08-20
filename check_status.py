import json
import os
import requests
import time
from datetime import datetime
from bs4 import BeautifulSoup

TARGETS_FILE = "targets.json"
STATUS_FILE = "status.json"

def load_json(filepath):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def check_gov_status(case_id):
    url = f"https://fraudbuster.digiat.org.tw/accessibility/detail?listType=H&id={case_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            page_text = soup.get_text()
            if "已通知Meta移除" in page_text or "已通知平台業者移除" in page_text:
                return "已通知Meta移除"
            elif "處理中" in page_text:
                return "處理中"
            elif "已移除" in page_text:
                return "已移除"
            return "已成案/其他狀態"
        return f"HTTP {res.status_code}"
    except Exception as e:
        return f"連線失敗: {str(e)}"

def check_meta_removed(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Language": "zh-TW,zh;q=0.9"
    }
    try:
        res = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        if res.status_code == 404 or ("此頁面無法使用" in res.text):
            return True
        return False
    except Exception:
        return False

def main():
    targets = load_json(TARGETS_FILE)
    status_db = load_json(STATUS_FILE)
    
    for target_url, info in targets.items():
        case_id = info.get("case_id")
        username = info.get("username", "Unknown")

        # 讀取過往紀錄，判斷是否已完結
        history = status_db.get(target_url, {})
        if history.get("is_completed"):
            print(f"跳過 [{username}]: 過去已完成追蹤 ({history.get('gov_status')})")
            continue

        # 執行實際查詢
        gov_status = check_gov_status(case_id)
        meta_removed = check_meta_removed(target_url)
        is_completed = ("已通知Meta移除" in gov_status) or meta_removed

        # 更新狀態
        status_db[target_url] = {
            "case_id": case_id,
            "username": username,
            "gov_status": gov_status,
            "meta_removed": meta_removed,
            "is_completed": is_completed,
            "last_checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        print(f"更新 [{username}]: 通報 -> {gov_status} | Meta移除 -> {meta_removed} | 結案 -> {is_completed}")
        time.sleep(2)

    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_db, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
