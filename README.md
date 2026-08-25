# ⏱️ Meta 通報與尚未移除時間關係觀測站 (純 GitHub 版)

本專案採用 **100% 純 GitHub 生態系（GitHub Actions + GitHub Pages + JSON 資料庫）**：
1. **GitHub Actions 高並發爬蟲**：定時（每 2 小時）或手動一鍵並發爬取 Threads 實際存活狀態與數發部打詐通報網歷程。
2. **免資料庫維護**：以 `tracking_db.json` 作為資料儲存庫，每次爬取自動 commit 回儲存庫進行版本控管。
3. **GitHub Pages 靜態儀表板**：自動將最新數據編譯為兼具 Tailwind CSS 與 Chart.js 的視覺化圖表與時間軸，直接託管於 GitHub Pages。

---

## 🛠️ 自動化工作流程 (GitHub Workflows)

### 1. 定時爬蟲與自動發布 (`crawler.yml`)
* **排程**：每 2 小時（避開整點尖峰於 23 分）自動執行。
* **流程**：多執行緒爬取 ➔ 更新 `tracking_db.json` ➔ 重新編譯 `index.html` ➔ 自動發布至 GitHub Pages ➔ 自動提交資料庫變更。
* **手動觸發**：可於 GitHub 專案的 **Actions** -> **High-Throughput Case Tracking** -> **Run workflow** 指定批次筆數（如 500 或 1000 筆）立即執行。

### 2. 靜態網頁編譯與部署 (`deploy.yml`)
* 當 `reports.json` 或儀表板程式碼更新並 push 到 `main` 時，自動編譯最新 HTML 並更新 GitHub Pages 網站。

---

## 💻 本機執行方式

### 1. 安裝 Python 依賴套件
```bash
pip install -r requirements.txt
```

### 2. 本機執行批次爬蟲
```bash
python3 scripts/batch_crawler.py --batch-size 100 --concurrency 10
```

### 3. 本機產出儀表板 HTML
```bash
python3 generate_dashboard.py
```
執行後即可在瀏覽器直接開啟專案根目錄的 `index.html`。
