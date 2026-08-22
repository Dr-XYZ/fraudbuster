# ⏱️ Meta 通報與尚未移除時間關係觀測站 (Cloudflare Worker + D1 版)

本專案已完全升級為原生 **Cloudflare Workers + Cloudflare D1 SQL 資料庫** 架構：
1. **高效能 SQL 儲存 (D1)**：使用 Cloudflare D1 SQLite 資料庫維護 `reports` 與 `cases` 表格，支援結構化查詢與 Batch 操作。
2. **舊格式資料轉碼**：已內建工具自動將舊有 `reports.json` (7,789 筆) 與 `tracking_db.json` (1,600 筆歷程) 轉換為 SQL 腳本。
3. **即時動態儀表板**：造訪 Worker 網址時直接由 Edge 端進行 SQL 統計並渲染 Tailwind CSS & Chart.js 儀表板。
4. **自動化 Cron 排程**：每 20 分鐘自動執行增量爬取（檢測打詐通報網歷程階段與 Threads 實況狀態），並自動寫入 Cloudflare D1。

---

## 🛠️ Cloudflare D1 部署與舊資料轉碼匯入步驟

在專屬的 Cloudflare 帳號中進行以下操作：

### 步驟 1：建立 Cloudflare D1 資料庫

1. 登入 [Cloudflare Dashboard](https://dash.cloudflare.com/)。
2. 進入側邊欄 **Workers & Pages** -> **D1**。
3. 點擊 **Create Database**，名稱輸入：`fraudbuster-db`。
4. 複製產生的 **Database ID** (一串 32 位的 HEX UUID 字串)。
5. 開啟專案根目錄的 `wrangler.toml` 檔案，將 `database_id` 填入：
   ```toml
   [[d1_databases]]
   binding = "DB"
   database_name = "fraudbuster-db"
   database_id = "您的_D1_DATABASE_ID"
   ```

---

### 步驟 2：舊格式資料轉碼 (Convert Old Data to D1 SQL)

執行專案內建的轉碼腳本：
```bash
python3 scripts/seed_d1.py --export-sql
```
執行後會自動掃描 `reports.json` 與 `tracking_db.json` 並生成 `seed_data.sql`（包含建立資料表與 9,000+ 筆資料 Insert 指令）。

---

### 步驟 3：匯入轉碼資料至 Cloudflare D1

您可以選擇以下任一方式匯入：

#### 方法 A：使用 Wrangler CLI 直接匯入 (推薦)
```bash
npx wrangler d1 execute fraudbuster-db --remote --file=seed_data.sql
```

#### 方法 B：使用 REST API 自動寫入
```bash
python3 scripts/seed_d1.py \
  --account-id <YOUR_ACCOUNT_ID> \
  --database-id <YOUR_D1_DATABASE_ID> \
  --token <YOUR_API_TOKEN>
```

---

### 步驟 4：設定 GitHub Secrets 並自動部署

在 GitHub Repository 的 **Settings** -> **Secrets and variables** -> **Actions** 中新增 2 個 Secrets：

| Secret 名稱 | 內容說明 |
| :--- | :--- |
| `CLOUDFLARE_ACCOUNT_ID` | 您的 Cloudflare Account ID |
| `CLOUDFLARE_API_TOKEN` | 您的 Cloudflare API Token (需含 D1:Edit 與 Workers:Edit 權限) |

當您 `git push` 至 `main` 分支時，GitHub Actions 會自動執行 `wrangler deploy` 將 Worker 與 D1 綁定部署上線！

---

## ⚡ API 與功能說明

- **`/` (GET)**：從 D1 讀取數據，顯示完整視覺化儀表板與案件時效歷程。
- **`/api/db` (GET)**：取得 D1 資料庫內所有 `cases` 的 JSON 資料。
- **`/api/trigger` (POST)**：手動觸發一次增量檢查排程。
- **`/api/seed` (POST)**：可透過 HTTP API 批量更新或初始化 D1 資料。
