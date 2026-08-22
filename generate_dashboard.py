import json
import os
import re
import statistics
from datetime import datetime

DB_FILE = "tracking_db.json"
OUTPUT_DIR = "public"
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "index.html")

def parse_dt(dt_str):
    if not dt_str or str(dt_str).strip() in ["-", "", "None"]:
        return None
    clean = " ".join(str(dt_str).strip().split())
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"]:
        try:
            return datetime.strptime(clean, fmt)
        except ValueError:
            continue
    return None

def analyze_case(record, now):
    """
    集中解析通報歷程文字與計算指標
    """
    stages = record.get("timeline_stages", [])
    all_desc = " ".join([s.get("desc", "") for s in stages])
    
    # 1. 解析部會
    dept = "未知"
    dept_match = re.search(r"(?:通知|已通知|確認中|由)\s*([^\s，,。]+(?:部|會|署|局))", all_desc)
    if dept_match:
        dept = dept_match.group(1).strip()
    elif "內政部" in all_desc:
        dept = "內政部"
    elif "數發部" in all_desc or "數位" in all_desc:
        dept = "數位發展部"

    # 2. 第二、三階段狀態與關鍵字判定
    is_notified_meta = False
    is_high_risk = False
    is_not_fraud = False
    is_in_review = False
    official_verified_time_str = ""

    # 依序掃描階段
    for s in stages:
        desc = s.get("desc", "")
        t_str = s.get("time", "")
        
        # 第三階段 / 最終結果關鍵字
        if "通知Meta移除" in desc or "通知 Meta 移除" in desc:
            is_notified_meta = True
            official_verified_time_str = t_str
        elif "高風險訊息" in desc:
            is_high_risk = True
            official_verified_time_str = t_str
        elif "非詐騙訊息" in desc or "非屬詐騙" in desc:
            is_not_fraud = True
            official_verified_time_str = t_str
            
        # 第二階段關鍵字
        elif "確認中" in desc or "處理中" in desc:
            is_in_review = True

    # 3. 綜合判決判定
    th_status = record.get("threads_actual_status", "未知")
    if is_not_fraud:
        verdict = "官方判定非詐騙"
    elif is_notified_meta:
        verdict = "已確認下架 (已通知Meta)" if th_status == "Removed" else "仍存活 (Meta未下架)"
    elif is_high_risk:
        verdict = "已下架 (數發部標記高風險)" if th_status == "Removed" else "存活 (數發部標記高風險)"
    elif is_in_review or len(stages) == 2:
        verdict = "已下架 (部會確認中)" if th_status == "Removed" else "部會確認中 / 未下架"
    elif th_status == "Removed":
        verdict = "已下架 (審核中/主動移除)"
    else:
        verdict = "處理中 / 存活"

    # 4. 時效與時長計算 (小時)
    t_reported = parse_dt(record.get("reported_at", ""))
    t_verified = parse_dt(official_verified_time_str) or (parse_dt(stages[-1]["time"]) if stages else None)
    t_takedown = parse_dt(record.get("takedown_detected_at", "")) or (now if th_status == "Removed" else None)

    official_processing_hours = None
    if t_reported and t_verified and (is_notified_meta or is_high_risk or is_not_fraud) and t_verified >= t_reported:
        official_processing_hours = round((t_verified - t_reported).total_seconds() / 3600.0, 1)

    meta_takedown_hours = None
    if t_verified and t_takedown and is_notified_meta and t_takedown >= t_verified:
        meta_takedown_hours = round((t_takedown - t_verified).total_seconds() / 3600.0, 1)

    total_survival_hours = None
    if t_reported:
        end_time = t_takedown if t_takedown else now
        total_survival_hours = round(max(0.0, (end_time - t_reported).total_seconds() / 3600.0), 1)

    meta_lag_hours = None
    if verdict == "仍存活 (Meta未下架)" and t_verified:
        meta_lag_hours = round(max(0.0, (now - t_verified).total_seconds() / 3600.0), 1)

    latest_stage_desc = stages[-1]["desc"] if stages else "無進度"

    return {
        "dept": dept,
        "is_notified_meta": is_notified_meta,
        "is_high_risk": is_high_risk,
        "is_not_fraud": is_not_fraud,
        "verdict": verdict,
        "latest_stage_desc": latest_stage_desc,
        "official_processing_hours": official_processing_hours,
        "meta_takedown_hours": meta_takedown_hours,
        "total_survival_hours": total_survival_hours,
        "meta_lag_hours": meta_lag_hours,
        "t_reported": t_reported
    }

def main():
    records = []
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                records = list(data.values())
        except Exception:
            records = []

    total = len(records)
    now = datetime.now()

    verdict_counts = {}
    threads_counts = {}
    notified_meta_count = 0
    official_resp_hours = []
    meta_resp_hours = []

    aging_buckets = {
        "24小時內": {"total": 0, "removed": 0},
        "24~48小時": {"total": 0, "removed": 0},
        "48~72小時": {"total": 0, "removed": 0},
        "超過72小時": {"total": 0, "removed": 0},
    }

    analyzed_data = []

    for r in records:
        analysis = analyze_case(r, now)
        analyzed_data.append((r, analysis))

        verdict = analysis["verdict"]
        th_status = r.get("threads_actual_status", "未知")

        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        threads_counts[th_status] = threads_counts.get(th_status, 0) + 1
        if analysis["is_notified_meta"]:
            notified_meta_count += 1

        t_rep = analysis["t_reported"]
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

        if analysis["official_processing_hours"] is not None:
            official_resp_hours.append(analysis["official_processing_hours"])

        if analysis["meta_takedown_hours"] is not None:
            meta_resp_hours.append(analysis["meta_takedown_hours"])

    removed = threads_counts.get("Removed", 0)
    takedown_rate = (removed / total * 100) if total > 0 else 0.0
    confirmed_meta = verdict_counts.get("已確認下架 (已通知Meta)", 0)
    meta_execution_rate = (confirmed_meta / notified_meta_count * 100) if notified_meta_count > 0 else 0.0
    
    avg_official = f"{statistics.mean(official_resp_hours):.1f}h" if official_resp_hours else "尚無"
    avg_meta = f"{statistics.mean(meta_resp_hours):.1f}h" if meta_resp_hours else "尚無"

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>打詐通報與 Threads 下架成效分析</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen p-4 md:p-8">
  <div class="max-w-7xl mx-auto space-y-6">
    <!-- 頂部標題 -->
    <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-700 pb-4">
      <div>
        <h1 class="text-2xl md:text-3xl font-bold text-white tracking-wide">🛡️ 打詐通報與 Threads 下架成效追蹤</h1>
        <p class="text-sm text-slate-400 mt-1">更新時間：{now.strftime("%Y-%m-%d %H:%M:%S")}</p>
      </div>
      <div class="mt-4 md:mt-0 bg-slate-800 border border-slate-700 px-4 py-2 rounded-lg text-sm">
        累積監測案件：<span class="text-emerald-400 font-bold">{total}</span> 筆
      </div>
    </div>

    <!-- 指標卡片 -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <div class="text-slate-400 text-xs font-medium uppercase">總體下架成功率</div>
        <div class="text-3xl font-extrabold text-emerald-400 mt-2">{takedown_rate:.1f}%</div>
        <div class="text-xs text-slate-400 mt-1">已下架 {removed} / 總數 {total}</div>
      </div>
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <div class="text-slate-400 text-xs font-medium uppercase">Meta 處置執行率 (已通知)</div>
        <div class="text-3xl font-extrabold text-blue-400 mt-2">{meta_execution_rate:.1f}%</div>
        <div class="text-xs text-slate-400 mt-1">已下架 {confirmed_meta} / 已通知 {notified_meta_count}</div>
      </div>
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <div class="text-slate-400 text-xs font-medium uppercase">官方平均審查耗時</div>
        <div class="text-3xl font-extrabold text-purple-400 mt-2">{avg_official}</div>
        <div class="text-xs text-slate-400 mt-1">通報 ➔ 官方確認發函</div>
      </div>
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <div class="text-slate-400 text-xs font-medium uppercase">Meta 平均下架耗時</div>
        <div class="text-3xl font-extrabold text-amber-400 mt-2">{avg_meta}</div>
        <div class="text-xs text-slate-400 mt-1">通知 Meta ➔ 偵測到下架</div>
      </div>
    </div>

    <!-- 圖表區 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <h3 class="font-semibold text-slate-200 mb-4">案件處置分類分佈</h3>
        <div class="h-64 flex justify-center items-center"><canvas id="verdictChart"></canvas></div>
      </div>
      <div class="bg-slate-800 border border-slate-700 p-5 rounded-xl">
        <h3 class="font-semibold text-slate-200 mb-4">通報經過時間 (Aging) vs 下架率</h3>
        <div class="h-64"><canvas id="agingChart"></canvas></div>
      </div>
    </div>

    <!-- 案件詳細清單 -->
    <div class="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden shadow-lg">
      <div class="p-4 border-b border-slate-700 flex flex-col md:flex-row justify-between gap-3">
        <h3 class="font-semibold text-slate-200 self-center">案件清單 (點擊展開各階段完整歷程與偵測紀錄)</h3>
        <input type="text" id="tableSearch" placeholder="搜尋帳號、部會、Case ID 或狀態..." class="bg-slate-900 border border-slate-700 px-3 py-1.5 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-emerald-500 w-full md:w-72">
      </div>
      <div class="overflow-x-auto max-h-[600px]">
        <table class="w-full text-left text-sm text-slate-300" id="casesTable">
          <thead class="bg-slate-900/80 text-xs uppercase text-slate-400 sticky top-0 backdrop-blur">
            <tr>
              <th class="p-3">帳號</th>
              <th class="p-3">受理部會</th>
              <th class="p-3">通報時間</th>
              <th class="p-3">最新進度摘要</th>
              <th class="p-3">Threads 實況</th>
              <th class="p-3">綜合判決</th>
              <th class="p-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-700" id="tableBody">
"""

    if total == 0:
        html_content += """
            <tr>
              <td colspan="7" class="p-8 text-center text-slate-400">
                ⏳ 尚無資料（案件通報滿 2 天後系統將自動開始追蹤）
              </td>
            </tr>"""
    else:
        for idx, (r, analysis) in enumerate(analyzed_data):
            v = analysis["verdict"]
            if "已確認下架" in v or "已下架" in v:
                badge_class = "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
            elif "仍存活" in v or "存活" in v:
                badge_class = "bg-rose-500/10 text-rose-400 border border-rose-500/20"
            elif "非詐騙" in v:
                badge_class = "bg-blue-500/10 text-blue-400 border border-blue-500/20"
            else:
                badge_class = "bg-amber-500/10 text-amber-400 border border-amber-500/20"

            th_status = r.get("threads_actual_status", "")
            th_badge = "text-emerald-400" if th_status == "Removed" else "text-rose-400"

            # 產生各階段歷程 HTML
            stages_html = ""
            for s in r.get("timeline_stages", []):
                stages_html += f"""
                  <div class="flex items-start space-x-2 text-xs py-1">
                    <span class="text-emerald-400 font-semibold">【第 {s.get('stage_num', '?')} 階段】</span>
                    <span class="text-slate-500 font-mono">[{s.get('time','')}]</span>
                    <span class="text-slate-300">{s.get('desc','')}</span>
                  </div>"""

            # 產生歷次偵測紀錄 HTML
            checks_html = ""
            for c in r.get("check_history", []):
                c_status = c.get("threads_status", "")
                c_badge = "text-emerald-400" if c_status == "Removed" else "text-rose-400"
                checks_html += f"""
                  <span class="inline-block bg-slate-900 border border-slate-800 px-2 py-1 rounded text-xs mr-2 mb-1">
                    第 {c.get('check_index')} 次 ({c.get('checked_at')}): <strong class="{c_badge}">{c_status}</strong>
                  </span>"""

            html_content += f"""
            <tr class="hover:bg-slate-750 transition cursor-pointer" onclick="toggleDetail('detail_{idx}')">
              <td class="p-3 font-medium text-slate-100">@{r.get('username','')}</td>
              <td class="p-3 text-slate-300">{analysis['dept']}</td>
              <td class="p-3 text-slate-400">{r.get('reported_at','')}</td>
              <td class="p-3 max-w-xs truncate" title="{analysis['latest_stage_desc']}">{analysis['latest_stage_desc']}</td>
              <td class="p-3 font-semibold {th_badge}">{th_status}</td>
              <td class="p-3"><span class="px-2 py-0.5 rounded-full text-xs {badge_class}">{v}</span></td>
              <td class="p-3 text-right space-x-2" onclick="event.stopPropagation()">
                <a href="{r.get('threads_url','')}" target="_blank" class="text-blue-400 hover:underline">Threads</a>
                <a href="{r.get('fraudbuster_url','')}" target="_blank" class="text-purple-400 hover:underline">通報網</a>
              </td>
            </tr>
            <tr id="detail_{idx}" class="hidden bg-slate-900/50">
              <td colspan="7" class="p-4 border-l-2 border-emerald-500 space-y-3">
                <div>
                  <div class="text-xs font-semibold text-slate-400 uppercase mb-1">📋 數發部完整處理歷程</div>
                  <div class="bg-slate-950 p-3 rounded border border-slate-800 space-y-1">
                    {stages_html if stages_html else '<div class="text-xs text-slate-500">尚無歷程資料</div>'}
                  </div>
                </div>
                <div>
                  <div class="text-xs font-semibold text-slate-400 uppercase mb-1">⏱️ 系統輪詢偵測紀錄 (共 {r.get('check_count', 1)} 次)</div>
                  <div class="p-2 bg-slate-950/60 rounded border border-slate-800">
                    {checks_html}
                  </div>
                </div>
              </td>
            </tr>"""

    aging_labels = list(aging_buckets.keys())
    aging_rates = [
        round((b["removed"] / b["total"] * 100) if b["total"] > 0 else 0, 1)
        for b in aging_buckets.values()
    ]
    verdict_labels = list(verdict_counts.keys()) if verdict_counts else ["無資料"]
    verdict_data = list(verdict_counts.values()) if verdict_counts else [1]
    verdict_colors = ['#10b981', '#f43f5e', '#3b82f6', '#f59e0b', '#8b5cf6', '#06b6d4'] if verdict_counts else ['#475569']

    html_content += f"""
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    function toggleDetail(id) {{
      const el = document.getElementById(id);
      if (el) el.classList.toggle('hidden');
    }}

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

    document.getElementById('tableSearch').addEventListener('input', function(e) {{
      const val = e.target.value.toLowerCase();
      document.querySelectorAll('#tableBody tr').forEach(tr => {{
        if (!tr.id.startsWith('detail_')) {{
          tr.style.display = tr.innerText.toLowerCase().includes(val) ? '' : 'none';
        }}
      }});
    }});
  </script>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✅ 儀表板網頁已成功產出至：{OUTPUT_HTML}")

if __name__ == "__main__":
    main()
