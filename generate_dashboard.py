import csv
import json
import os
import statistics
from datetime import datetime

CSV_FILE = "fraud_tracking_summary.csv"
OUTPUT_DIR = "public"
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "index.html")

def parse_dt(dt_str):
    if not dt_str or dt_str.strip() in ["-", "", "None"]:
        return None
    clean = " ".join(dt_str.strip().split())
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"]:
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue
    return None

def main():
    rows = []
    if os.path.exists(CSV_FILE):
        with open(CSV_FILE, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)

    total = len(rows)
    now = datetime.now()

    verdict_counts = {}
    threads_counts = {}
    notified_meta_count = 0
    official_resp_hours = []
    aging_buckets = {
        "24小時內": {"total": 0, "removed": 0},
        "24~48小時": {"total": 0, "removed": 0},
        "48~72小時": {"total": 0, "removed": 0},
        "超過72小時": {"total": 0, "removed": 0},
    }

    for r in rows:
        verdict = r.get("takedown_verdict", "未知")
        th_status = r.get("threads_actual_status", "未知")
        notified = r.get("notified_meta", "否")

        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        threads_counts[th_status] = threads_counts.get(th_status, 0) + 1
        if notified == "是":
            notified_meta_count += 1

        t_rep = parse_dt(r.get("reported_at", ""))
        t_prog = parse_dt(r.get("latest_step_time", ""))

        if t_rep:
            elapsed = max(0.0, (now - t_rep).total_seconds() / 3600.0)
            if elapsed <= 24:
                b_key = "24小時內"
            elif elapsed <= 48:
                b_key = "24~48小時"
            elif elapsed <= 72:
                b_key = "48~72小時"
            else:
                b_key = "超過72小時"

            aging_buckets[b_key]["total"] += 1
            if th_status == "Removed":
                aging_buckets[b_key]["removed"] += 1

        if t_rep and t_prog and t_prog >= t_rep:
            official_resp_hours.append((t_prog - t_rep).total_seconds() / 3600.0)

    # 安全計算各項比率（避免除以零）
    removed = threads_counts.get("Removed", 0)
    takedown_rate = (removed / total * 100) if total > 0 else 0.0
    confirmed = verdict_counts.get("已確認下架", 0)
    meta_rate = (confirmed / notified_meta_count * 100) if notified_meta_count > 0 else 0.0
    official_fraud_rate = (notified_meta_count / total * 100) if total > 0 else 0.0
    avg_resp = f"{statistics.mean(official_resp_hours):.1f} 小時" if official_resp_hours else "尚無數據"

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 產生 HTML
    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>打詐通報與 Threads 下架成效追蹤</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen p-4 md:p-8">
  <div class="max-w-7xl mx-auto space-y-6">
    <!-- 頂部標題 -->
    <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-700 pb-4">
      <div>
        <h1 class="text-2xl md:text-3xl font-bold text-white tracking-wide">🛡️ 打詐通報與 Threads 下架成效追蹤</h1>
        <p class="text-sm text-slate-400 mt-1">最後更新時間：{now.strftime("%Y-%m-%d %H:%M:%S")} (台北時間)</p>
      </div>
      <div class="mt-4 md:mt-0 bg-slate-800 border border-slate-700 px-4 py-2 rounded-lg text-sm">
        追蹤樣本數：<span class="text-emerald-400 font-bold">{total}</span> 筆
      </div>
    </div>

    <!-- 關鍵指標卡片 -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <div class="text-slate-400 text-xs uppercase font-medium">總體下架成功率</div>
        <div class="text-3xl font-extrabold text-emerald-400 mt-2">{takedown_rate:.1f}%</div>
        <div class="text-xs text-slate-400 mt-1">已下架 {removed} / 總數 {total}</div>
      </div>
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <div class="text-slate-400 text-xs uppercase font-medium">Meta 下架執行率 (已通報中)</div>
        <div class="text-3xl font-extrabold text-blue-400 mt-2">{meta_rate:.1f}%</div>
        <div class="text-xs text-slate-400 mt-1">已下架 {confirmed} / 已通知 {notified_meta_count}</div>
      </div>
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <div class="text-slate-400 text-xs uppercase font-medium">官方判定詐騙率</div>
        <div class="text-3xl font-extrabold text-purple-400 mt-2">{official_fraud_rate:.1f}%</div>
        <div class="text-xs text-slate-400 mt-1">通知 Meta {notified_meta_count} 筆</div>
      </div>
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <div class="text-slate-400 text-xs uppercase font-medium">官方平均審核耗時</div>
        <div class="text-3xl font-extrabold text-amber-400 mt-2">{avg_resp}</div>
        <div class="text-xs text-slate-400 mt-1">通報 ➔ 通知 Meta 移除</div>
      </div>
    </div>

    <!-- 圖表區 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <h3 class="font-semibold text-slate-200 mb-4">案件處置現況分佈</h3>
        <div class="h-64 flex justify-center items-center">
          <canvas id="verdictChart"></canvas>
        </div>
      </div>
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <h3 class="font-semibold text-slate-200 mb-4">通報經過時間 (Aging) vs 下架率</h3>
        <div class="h-64">
          <canvas id="agingChart"></canvas>
        </div>
      </div>
    </div>

    <!-- 資料清單表格 -->
    <div class="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden shadow-lg">
      <div class="p-4 border-b border-slate-700 flex flex-col md:flex-row justify-between gap-3">
        <h3 class="font-semibold text-slate-200 self-center">案件詳細清單</h3>
        <input type="text" id="tableSearch" placeholder="搜尋帳號、Case ID 或狀態..." class="bg-slate-900 border border-slate-700 px-3 py-1.5 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-emerald-500 w-full md:w-72">
      </div>
      <div class="overflow-x-auto max-h-[500px]">
        <table class="w-full text-left text-sm text-slate-300" id="casesTable">
          <thead class="bg-slate-900/80 text-xs uppercase text-slate-400 sticky top-0 backdrop-blur">
            <tr>
              <th class="p-3">帳號</th>
              <th class="p-3">通報時間</th>
              <th class="p-3">打詐通報最新進度</th>
              <th class="p-3">Threads 實況</th>
              <th class="p-3">綜合判決</th>
              <th class="p-3 text-right">連結</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-700" id="tableBody">
"""

    if total == 0:
        html_content += """
            <tr>
              <td colspan="6" class="p-8 text-center text-slate-400">
                ⏳ 尚無已檢查資料（案件通報滿 2 天後系統將自動開始追蹤）
              </td>
            </tr>"""
    else:
        for r in rows:
            v = r.get("takedown_verdict", "")
            if "已確認下架" in v or "已下架" in v:
                badge_class = "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
            elif "仍存活" in v:
                badge_class = "bg-rose-500/10 text-rose-400 border border-rose-500/20"
            else:
                badge_class = "bg-amber-500/10 text-amber-400 border border-amber-500/20"

            th_status = r.get("threads_actual_status", "")
            th_badge = "text-emerald-400" if th_status == "Removed" else "text-rose-400"

            html_content += f"""
            <tr class="hover:bg-slate-750 transition">
              <td class="p-3 font-medium text-slate-100">@{r.get('username','')}</td>
              <td class="p-3 text-slate-400">{r.get('reported_at','')}</td>
              <td class="p-3">{r.get('latest_progress','')}</td>
              <td class="p-3 font-semibold {th_badge}">{th_status}</td>
              <td class="p-3"><span class="px-2 py-0.5 rounded-full text-xs {badge_class}">{v}</span></td>
              <td class="p-3 text-right space-x-2">
                <a href="{r.get('threads_url','')}" target="_blank" class="text-blue-400 hover:underline">Threads</a>
                <a href="{r.get('fraudbuster_url','')}" target="_blank" class="text-purple-400 hover:underline">通報網</a>
              </td>
            </tr>"""

    aging_labels = list(aging_buckets.keys())
    aging_rates = [
        round((b["removed"] / b["total"] * 100) if b["total"] > 0 else 0, 1)
        for b in aging_buckets.values()
    ]

    verdict_labels = list(verdict_counts.keys()) if verdict_counts else ["無資料"]
    verdict_data = list(verdict_counts.values()) if verdict_counts else [1]
    verdict_colors = ['#10b981', '#f43f5e', '#3b82f6', '#f59e0b', '#8b5cf6'] if verdict_counts else ['#475569']

    html_content += f"""
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    // 圓餅圖
    new Chart(document.getElementById('verdictChart'), {{
      type: 'doughnut',
      data: {{
        labels: {json.dumps(verdict_labels, ensure_ascii=False)},
        datasets: [{{
          data: {verdict_data},
          backgroundColor: {json.dumps(verdict_colors)},
          borderWidth: 0
        }}]
      }},
      options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#94a3b8' }} }} }} }}
    }});

    // 長條圖
    new Chart(document.getElementById('agingChart'), {{
      type: 'bar',
      data: {{
        labels: {json.dumps(aging_labels, ensure_ascii=False)},
        datasets: [{{
          label: '下架率 (%)',
          data: {aging_rates},
          backgroundColor: '#3b82f6',
          borderRadius: 6
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          y: {{ max: 100, ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
          x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }}
        }},
        plugins: {{ legend: {{ display: false }} }}
      }}
    }});

    // 表格搜尋
    document.getElementById('tableSearch').addEventListener('input', function(e) {{
      const val = e.target.value.toLowerCase();
      document.querySelectorAll('#tableBody tr').forEach(tr => {{
        tr.style.display = tr.innerText.toLowerCase().includes(val) ? '' : 'none';
      }});
    }});
  </script>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✅ 儀表板網頁已產出至：{OUTPUT_HTML}")

if __name__ == "__main__":
    main()