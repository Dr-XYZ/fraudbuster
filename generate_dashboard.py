import json
import os
import re
import statistics
from datetime import datetime

DB_FILE = "tracking_db.json"
OUTPUT_DIR = "public"
OUTPUT_HTML = os.path.join(OUTPUT_DIR, "index.html")

def parse_dt(dt_str):
    """強健解析多種日期時間格式（自動處理多餘空白與換行）"""
    if not dt_str or str(dt_str).strip() in ["-", "", "None"]:
        return None
    clean_str = re.sub(r"\s+", " ", str(dt_str).strip())
    if "T" in clean_str:
        clean_str = clean_str.split(".")[0].replace("T", " ")
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d"]:
        try:
            return datetime.strptime(clean_str, fmt)
        except ValueError:
            continue
    return None

def format_hours_readable(hours: float) -> str:
    """轉換小時為易讀格式（例如：2天 4.5小時）"""
    if hours < 24:
        return f"{hours:.1f} 小時"
    days = int(hours // 24)
    rem_h = hours % 24
    return f"{days}天 {rem_h:.1f}小時 ({hours:.1f}h)"

def main():
    records = []
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                records = list(data.values())
        except Exception:
            records = []

    total_all = len(records)
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # 專門收集已通知 Meta 的案件
    meta_cases = []
    
    # 滯留時長分桶
    lag_buckets = {
        "< 24小時": 0,
        "24 ~ 48小時 (1~2天)": 0,
        "48 ~ 72小時 (2~3天)": 0,
        "72 ~ 120小時 (3~5天)": 0,
        "> 120小時 (> 5天)": 0
    }

    cohort_dates = {}
    takedown_duration_list = []   # 已下架耗時
    active_lag_duration_list = [] # 未下架滯留耗時

    for r in records:
        stages = r.get("timeline_stages", [])
        th_status = r.get("threads_actual_status", "未知")
        reported_dt = parse_dt(r.get("reported_at", ""))
        
        # 抓取官方通知 Meta 的階段與精確時間
        meta_notified_dt = None
        meta_stage_text = ""
        for s in stages:
            desc = s.get("desc", "")
            if "通知Meta移除" in desc or "通知 Meta 移除" in desc:
                meta_notified_dt = parse_dt(s.get("time", ""))
                meta_stage_text = desc
                break

        if not meta_notified_dt:
            continue

        date_key = meta_notified_dt.strftime("%Y-%m-%d")
        if date_key not in cohort_dates:
            cohort_dates[date_key] = {"removed": 0, "active": 0}

        t_takedown = parse_dt(r.get("takedown_detected_at", ""))
        first_checked_dt = parse_dt(r.get("first_checked_at", ""))
        last_checked_dt = parse_dt(r.get("last_checked_at", ""))

        # 審查耗時：通報 ➔ 通知 Meta
        official_review_hours = None
        if reported_dt and meta_notified_dt and meta_notified_dt >= reported_dt:
            official_review_hours = round((meta_notified_dt - reported_dt).total_seconds() / 3600.0, 2)

        # 案件時效判定與註記
        is_removed = (th_status == "Removed")
        if is_removed:
            cohort_dates[date_key]["removed"] += 1
            end_t = t_takedown or last_checked_dt or now
            if end_t >= meta_notified_dt:
                meta_handling_hours = round((end_t - meta_notified_dt).total_seconds() / 3600.0, 1)
                takedown_duration_list.append(meta_handling_hours)
                time_note = f"已處置下架 (耗時 {format_hours_readable(meta_handling_hours)})"
            else:
                meta_handling_hours = 0.0
                time_note = "已處置下架"
            lag_hours = 0.0
        else:
            cohort_dates[date_key]["active"] += 1
            lag_hours = round(max(0.0, (now - meta_notified_dt).total_seconds() / 3600.0), 1)
            active_lag_duration_list.append(lag_hours)
            meta_handling_hours = None
            time_note = f"滯留未下架 {format_hours_readable(lag_hours)} (統計至 {now.strftime('%m/%d %H:%M')})"

            if lag_hours < 24:
                lag_buckets["< 24小時"] += 1
            elif lag_hours < 48:
                lag_buckets["24 ~ 48小時 (1~2天)"] += 1
            elif lag_hours < 72:
                lag_buckets["48 ~ 72小時 (2~3天)"] += 1
            elif lag_hours < 120:
                lag_buckets["72 ~ 120小時 (3~5天)"] += 1
            else:
                lag_buckets["> 120小時 (> 5天)"] += 1

        # 格式化階段歷程
        stages_data = []
        for s in r.get("timeline_stages", []):
            s_dt = parse_dt(s.get("time", ""))
            offset_str = ""
            if s_dt and reported_dt and s_dt >= reported_dt:
                diff_h = (s_dt - reported_dt).total_seconds() / 3600.0
                offset_str = f"(+ {diff_h:.1f}h)"
            stages_data.append({
                "num": s.get("stage_num", "?"),
                "time": s.get("time", ""),
                "offset": offset_str,
                "desc": s.get("desc", "")
            })

        # 格式化歷次檢查紀錄
        checks_data = []
        for chk in r.get("check_history", []):
            checks_data.append({
                "idx": chk.get("check_index"),
                "time": chk.get("checked_at"),
                "status": chk.get("threads_status", "")
            })

        username = r.get("username") or ""
        if not username and r.get("threads_url") and "/@" in r.get("threads_url"):
            username = r.get("threads_url").split("/@")[-1].split("?")[0].split("/")[0].strip()

        meta_cases.append({
            "u": username,
            "case_id": r.get("case_id", ""),
            "r_dt": reported_dt.strftime("%Y-%m-%d %H:%M:%S") if reported_dt else "-",
            "m_dt": meta_notified_dt.strftime("%Y-%m-%d %H:%M") if meta_notified_dt else "-",
            "is_rem": is_removed,
            "th_st": "已下架 (Removed)" if is_removed else "仍存活 (Active)",
            "lag_h": lag_hours,
            "time_note": time_note,
            "last_chk": last_checked_dt.strftime("%Y-%m-%d %H:%M:%S") if last_checked_dt else "-",
            "th_url": r.get("threads_url", ""),
            "fb_url": r.get("fraudbuster_url", ""),
            "off_rev": official_review_hours,
            "t_tk": t_takedown.strftime("%Y-%m-%d %H:%M:%S") if t_takedown else "尚未下架",
            "chk_cnt": r.get("check_count", 1),
            "stages": stages_data,
            "checks": checks_data
        })

    # 彙整指標
    total_meta_notified = len(meta_cases)
    total_removed = sum(1 for c in meta_cases if c["is_rem"])
    total_unremoved = total_meta_notified - total_removed
    
    inaction_rate = (total_unremoved / total_meta_notified * 100) if total_meta_notified > 0 else 0.0
    takedown_rate = (total_removed / total_meta_notified * 100) if total_meta_notified > 0 else 0.0

    avg_lag_hours = f"{statistics.mean(active_lag_duration_list):.1f} 小時" if active_lag_duration_list else "0 小時"
    max_lag_hours = f"{max(active_lag_duration_list):.1f} 小時" if active_lag_duration_list else "0 小時"
    avg_takedown_hours = f"{statistics.mean(takedown_duration_list):.1f} 小時" if takedown_duration_list else "尚無數據"

    sorted_cohort_dates = sorted(cohort_dates.keys())
    cohort_removed_data = [cohort_dates[d]["removed"] for d in sorted_cohort_dates]
    cohort_active_data = [cohort_dates[d]["active"] for d in sorted_cohort_dates]

    # 預先排序：尚未移除（Active）且滯留最久的置頂
    meta_cases.sort(key=lambda x: (x["is_rem"], -x["lag_h"]))

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    cases_json_str = json.dumps(meta_cases, ensure_ascii=False)

    html_content = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Meta 通報與尚未移除時間關係觀測站</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    .custom-scroll::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    .custom-scroll::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 3px; }}
    .custom-scroll::-webkit-scrollbar-track {{ background: #0f172a; }}
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-3 md:p-8 font-sans">
  <div class="max-w-7xl mx-auto space-y-6">
    
    <!-- 頂部標題與全站統計時間註記 -->
    <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-800 pb-5">
      <div>
        <div class="flex items-center space-x-3">
          <span class="text-3xl">⏱️</span>
          <h1 class="text-2xl md:text-3xl font-extrabold text-white tracking-tight">Meta 通報與尚未移除時間關係觀測站</h1>
        </div>
        <div class="flex flex-wrap items-center gap-2 mt-2 text-xs">
          <span class="bg-blue-500/10 text-blue-400 border border-blue-500/20 px-2.5 py-1 rounded-md font-mono">
            🕒 統計基準時間：{now_str} (UTC+8)
          </span>
          <span class="text-slate-400">所有案件滯留時長均以此時間點為計算依據</span>
        </div>
      </div>
      <div class="mt-4 md:mt-0 flex items-center gap-3">
        <div class="bg-slate-900 border border-slate-800 px-4 py-2 rounded-xl text-xs text-slate-300">
          已通知 Meta 總數：<span class="text-blue-400 font-bold text-base">{total_meta_notified}</span> 筆
        </div>
      </div>
    </div>

    <!-- 關鍵指標卡片 -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-slate-900/90 border border-rose-500/30 p-5 rounded-xl shadow-lg">
        <div class="text-rose-400 text-xs font-bold uppercase tracking-wider">目前尚未移除 (Meta 怠慢)</div>
        <div class="text-3xl font-black text-rose-400 mt-1">{total_unremoved} <span class="text-xs font-normal text-slate-400">筆</span></div>
        <div class="text-xs text-slate-400 mt-1">未移除率：<span class="text-rose-400 font-bold">{inaction_rate:.1f}%</span></div>
      </div>

      <div class="bg-slate-900/90 border border-slate-800 p-5 rounded-xl">
        <div class="text-slate-400 text-xs font-semibold uppercase">尚未移除平均滯留時間</div>
        <div class="text-2xl font-black text-amber-400 mt-1">{avg_lag_hours}</div>
        <div class="text-xs text-slate-500 mt-1">最長已滯留：{max_lag_hours}</div>
      </div>

      <div class="bg-slate-900/90 border border-emerald-500/20 p-5 rounded-xl">
        <div class="text-emerald-400 text-xs font-bold uppercase tracking-wider">已成功下架數量</div>
        <div class="text-3xl font-black text-emerald-400 mt-1">{total_removed} <span class="text-xs font-normal text-slate-400">筆</span></div>
        <div class="text-xs text-slate-400 mt-1">下架執行率：<span class="text-emerald-400 font-bold">{takedown_rate:.1f}%</span></div>
      </div>

      <div class="bg-slate-900/90 border border-slate-800 p-5 rounded-xl">
        <div class="text-slate-400 text-xs font-semibold uppercase">Meta 平均下架處置時效</div>
        <div class="text-2xl font-black text-blue-400 mt-1">{avg_takedown_hours}</div>
        <div class="text-xs text-slate-500 mt-1">已下架案件之平均處理速度</div>
      </div>
    </div>

    <!-- 圖表區 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <!-- 滯留時長階梯圖 -->
      <div class="bg-slate-900/90 border border-slate-800 p-5 rounded-xl shadow-xl">
        <div class="flex justify-between items-center mb-3">
          <div>
            <h3 class="text-sm font-bold text-slate-200">📊 尚未移除帳號之「被通知後滯留時間」分佈</h3>
            <p class="text-xs text-slate-500">呈現 {total_unremoved} 筆存活帳號已被官方通報多久未處置</p>
          </div>
          <span class="px-2 py-0.5 rounded text-xs bg-rose-500/10 text-rose-400 border border-rose-500/20">滯留階梯</span>
        </div>
        <div class="h-64"><canvas id="lagBucketChart"></canvas></div>
      </div>

      <!-- 每日批次處置情況 -->
      <div class="bg-slate-900/90 border border-slate-800 p-5 rounded-xl shadow-xl">
        <div class="flex justify-between items-center mb-3">
          <div>
            <h3 class="text-sm font-bold text-slate-200">📅 各通報日期批次：已下架 vs 尚未移除</h3>
            <p class="text-xs text-slate-500">依「官方通知 Meta 之日期」追蹤各批次下架進度</p>
          </div>
          <span class="px-2 py-0.5 rounded text-xs bg-blue-500/10 text-blue-400 border border-blue-500/20">批次追蹤</span>
        </div>
        <div class="h-64"><canvas id="cohortChart"></canvas></div>
      </div>
    </div>

    <!-- 詳細清單表格（高效能分頁 & 即時篩選） -->
    <div class="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-2xl">
      <!-- 控制列 -->
      <div class="p-4 border-b border-slate-800 flex flex-col md:flex-row justify-between items-stretch md:items-center gap-3">
        <div>
          <h3 class="font-bold text-slate-100 flex items-center gap-2">
            <span>📋 案件詳細時間註記清單</span>
            <span class="bg-slate-800 text-slate-300 text-xs px-2.5 py-0.5 rounded-full" id="matchedCountTag">{total_meta_notified} 筆</span>
          </h3>
          <p class="text-xs text-slate-400 mt-0.5">點擊任一行可即時展開「通報各階段時間軸」與「輪詢偵測紀錄」</p>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <!-- 狀態篩選 -->
          <select id="statusFilter" class="bg-slate-950 border border-slate-700 px-3 py-1.5 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500">
            <option value="ALL">全部狀態</option>
            <option value="ACTIVE" selected>僅看尚未移除 (Active)</option>
            <option value="REMOVED">僅看已下架 (Removed)</option>
          </select>

          <!-- 搜尋欄 -->
          <input type="text" id="tableSearch" placeholder="搜尋帳號、Case ID..." class="bg-slate-950 border border-slate-700 px-3 py-1.5 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 w-full sm:w-60">
        </div>
      </div>

      <!-- 表格內容 -->
      <div class="overflow-x-auto max-h-[620px] custom-scroll">
        <table class="w-full text-left text-sm text-slate-300">
          <thead class="bg-slate-950 text-xs uppercase text-slate-400 sticky top-0 backdrop-blur z-10">
            <tr>
              <th class="p-3">帳號</th>
              <th class="p-3">民眾通報時間</th>
              <th class="p-3">官方通知 Meta 時間</th>
              <th class="p-3">Threads 實況</th>
              <th class="p-3">時效關係註記 (耗時 / 滯留)</th>
              <th class="p-3">最新偵測時間</th>
              <th class="p-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800/80" id="tableBody">
            <!-- 由前端極速渲染當前頁 -->
          </tbody>
        </table>
      </div>

      <!-- 分頁控制項 -->
      <div class="p-4 bg-slate-950/80 border-t border-slate-800 flex flex-col sm:flex-row justify-between items-center gap-3 text-xs text-slate-400">
        <div id="pageInfo">顯示第 1 到 50 筆</div>
        <div class="flex items-center gap-1.5" id="paginationBtns">
          <!-- 分頁按鈕 -->
        </div>
      </div>
    </div>

  </div>

  <script>
    // 原始精簡資料集 (In-Memory Array)
    const ALL_CASES = {cases_json_str};
    let filteredCases = [...ALL_CASES];
    let currentPage = 1;
    const PAGE_SIZE = 50;
    const expandedRows = new Set();

    // 1. 滯留時長階梯圖
    new Chart(document.getElementById('lagBucketChart'), {{
      type: 'bar',
      data: {{
        labels: {json.dumps(list(lag_buckets.keys()), ensure_ascii=False)},
        datasets: [{{
          label: '尚未移除案件數',
          data: {json.dumps(list(lag_buckets.values()))},
          backgroundColor: ['#fbbf24', '#f97316', '#ef4444', '#b91c1c', '#7f1d1d'],
          borderRadius: 6
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          y: {{ ticks: {{ color: '#94a3b8', precision: 0 }}, grid: {{ color: '#1e293b' }} }},
          x: {{ ticks: {{ color: '#94a3b8', font: {{ size: 11 }} }}, grid: {{ display: false }} }}
        }},
        plugins: {{
          legend: {{ display: false }},
          tooltip: {{
            callbacks: {{
              label: (ctx) => ` 滯留案件: ${{ctx.raw}} 筆`
            }}
          }}
        }}
      }}
    }});

    // 2. 各日期批次處置長條圖
    new Chart(document.getElementById('cohortChart'), {{
      type: 'bar',
      data: {{
        labels: {json.dumps(sorted_cohort_dates)},
        datasets: [
          {{
            label: '已下架 (Removed)',
            data: {json.dumps(cohort_removed_data)},
            backgroundColor: '#10b981',
            borderRadius: 4
          }},
          {{
            label: '尚未移除 (Active)',
            data: {json.dumps(cohort_active_data)},
            backgroundColor: '#f43f5e',
            borderRadius: 4
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ stacked: true, ticks: {{ color: '#94a3b8' }}, grid: {{ display: false }} }},
          y: {{ stacked: true, ticks: {{ color: '#94a3b8', precision: 0 }}, grid: {{ color: '#1e293b' }} }}
        }},
        plugins: {{
          legend: {{ position: 'bottom', labels: {{ color: '#94a3b8' }} }}
        }}
      }}
    }});

    // 切換展開/收合案件歷程
    function toggleDetail(idx) {{
      if (expandedRows.has(idx)) {{
        expandedRows.delete(idx);
      }} else {{
        expandedRows.add(idx);
      }}
      renderTable();
    }}

    // 高效能分頁與表格渲染
    function renderTable() {{
      const tbody = document.getElementById('tableBody');
      const totalItems = filteredCases.length;
      const totalPages = Math.ceil(totalItems / PAGE_SIZE) || 1;

      if (currentPage > totalPages) currentPage = totalPages;
      if (currentPage < 1) currentPage = 1;

      const start = (currentPage - 1) * PAGE_SIZE;
      const end = Math.min(start + PAGE_SIZE, totalItems);
      const pageData = filteredCases.slice(start, end);

      document.getElementById('matchedCountTag').innerText = `${{totalItems}} 筆`;
      document.getElementById('pageInfo').innerText = totalItems > 0 
        ? `顯示第 ${{start + 1}} 到 ${{end}} 筆 (共 ${{totalItems}} 筆)`
        : '無符合條件之案件';

      if (pageData.length === 0) {{
        tbody.innerHTML = `<tr><td colspan="7" class="p-12 text-center text-slate-500">🔍 沒有找到符合條件的案件</td></tr>`;
        renderPagination(totalPages);
        return;
      }}

      let rowsHtml = '';
      pageData.forEach((c, i) => {{
        const globalIdx = start + i;
        const isRem = c.is_rem;
        const thBadge = isRem ? 'text-emerald-400' : 'text-rose-400 font-bold';
        
        let timeBadge = '';
        if (isRem) {{
          timeBadge = 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded text-xs font-mono';
        }} else {{
          if (c.lag_h >= 48) {{
            timeBadge = 'bg-rose-500/20 text-rose-300 border border-rose-500/40 px-2 py-0.5 rounded text-xs font-mono font-bold';
          }} else {{
            timeBadge = 'bg-amber-500/20 text-amber-300 border border-amber-500/40 px-2 py-0.5 rounded text-xs font-mono';
          }}
        }}

        const isExpanded = expandedRows.has(globalIdx);

        rowsHtml += `
          <tr class="hover:bg-slate-800/60 transition cursor-pointer ${{isExpanded ? 'bg-slate-800/40' : ''}}" onclick="toggleDetail(${{globalIdx}})">
            <td class="p-3 font-semibold text-slate-100">@${{c.u}}</td>
            <td class="p-3 text-slate-400 font-mono text-xs">${{c.r_dt}}</td>
            <td class="p-3 text-blue-300 font-mono text-xs">${{c.m_dt}}</td>
            <td class="p-3 ${{thBadge}}">${{c.th_st}}</td>
            <td class="p-3"><span class="${{timeBadge}}">${{c.time_note}}</span></td>
            <td class="p-3 text-slate-400 text-xs font-mono">${{c.last_chk}}</td>
            <td class="p-3 text-right space-x-2 text-xs" onclick="event.stopPropagation()">
              ${{c.th_url ? `<a href="${{c.th_url}}" target="_blank" class="text-blue-400 hover:underline">Threads</a>` : ''}}
              ${{c.fb_url ? `<a href="${{c.fb_url}}" target="_blank" class="text-purple-400 hover:underline">通報網</a>` : ''}}
            </td>
          </tr>
        `;

        if (isExpanded) {{
          let stagesHtml = (c.stages || []).map(s => `
            <div class="flex items-start space-x-3 text-xs py-1.5 border-b border-slate-900 last:border-0">
              <span class="bg-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded font-mono font-bold">階段 ${{s.num}}</span>
              <span class="text-slate-400 font-mono">[${{s.time}}]</span>
              <span class="text-slate-500 font-mono text-[11px]">${{s.offset}}</span>
              <span class="text-slate-200 flex-1">${{s.desc}}</span>
            </div>
          `).join('');

          let checksHtml = (c.checks || []).map(chk => {{
            const chkBadge = chk.status === 'Removed' ? 'text-emerald-400' : 'text-rose-400';
            return `
              <span class="inline-block bg-slate-900 border border-slate-800 px-2.5 py-1 rounded text-xs mr-2 mb-1">
                第 ${{chk.idx}} 次偵測 ➔ 時間: <span class="font-mono text-slate-300">${{chk.time}}</span> | 實況: <strong class="${{chkBadge}}">${{chk.status}}</strong>
              </span>
            `;
          }}).join('');

          rowsHtml += `
            <tr class="bg-slate-900/60">
              <td colspan="7" class="p-5 border-l-4 border-blue-500 space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-3 gap-3 bg-slate-950 p-3 rounded-lg border border-slate-800 text-xs">
                  <div>• 通報至官方審畢: <strong class="text-purple-400 font-mono">${{c.off_rev !== null ? c.off_rev + ' 小時' : '-'}}</strong></div>
                  <div>• 下架確認時間: <strong class="text-emerald-400 font-mono">${{c.t_tk}}</strong></div>
                  <div>• 累計偵測次數: <strong class="text-slate-300 font-mono">${{c.chk_cnt}} 次</strong></div>
                </div>
                <div>
                  <div class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">📋 數發部通報各階段歷程與時間差</div>
                  <div class="bg-slate-950 p-3 rounded-lg border border-slate-800">${{stagesHtml || '<div class="text-xs text-slate-500">尚無歷程資料</div>'}}</div>
                </div>
                <div>
                  <div class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">⏱️ 系統輪詢檢查時間紀錄</div>
                  <div class="p-3 bg-slate-950/80 rounded-lg border border-slate-800">${{checksHtml || '<div class="text-xs text-slate-500">尚無偵測紀錄</div>'}}</div>
                </div>
              </td>
            </tr>
          `;
        }}
      }});

      tbody.innerHTML = rowsHtml;
      renderPagination(totalPages);
    }}

    function renderPagination(totalPages) {{
      const container = document.getElementById('paginationBtns');
      let html = `
        <button onclick="goToPage(1)" class="px-2.5 py-1 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800 disabled:opacity-30" ${{currentPage === 1 ? 'disabled' : ''}}>«</button>
        <button onclick="goToPage(${{currentPage - 1}})" class="px-2.5 py-1 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800 disabled:opacity-30" ${{currentPage === 1 ? 'disabled' : ''}}>‹</button>
      `;

      let startP = Math.max(1, currentPage - 2);
      let endP = Math.min(totalPages, currentPage + 2);

      for (let p = startP; p <= endP; p++) {{
        html += `
          <button onclick="goToPage(${{p}})" class="px-3 py-1 rounded font-mono text-xs ${{p === currentPage ? 'bg-blue-600 text-white font-bold' : 'bg-slate-900 border border-slate-800 text-slate-300 hover:bg-slate-800'}}">${{p}}</button>
        `;
      }}

      html += `
        <button onclick="goToPage(${{currentPage + 1}})" class="px-2.5 py-1 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800 disabled:opacity-30" ${{currentPage >= totalPages ? 'disabled' : ''}}>›</button>
        <button onclick="goToPage(${{totalPages}})" class="px-2.5 py-1 rounded bg-slate-900 border border-slate-800 hover:bg-slate-800 disabled:opacity-30" ${{currentPage >= totalPages ? 'disabled' : ''}}>»</button>
      `;

      container.innerHTML = html;
    }}

    function goToPage(p) {{
      currentPage = p;
      renderTable();
      document.getElementById('tableBody').scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
    }}

    function applyFilters() {{
      const query = document.getElementById('tableSearch').value.toLowerCase().trim();
      const status = document.getElementById('statusFilter').value;

      filteredCases = ALL_CASES.filter(c => {{
        // 狀態篩選
        if (status === 'ACTIVE' && c.is_rem) return false;
        if (status === 'REMOVED' && !c.is_rem) return false;

        // 關鍵字搜尋
        if (query) {{
          const targetStr = (c.u + ' ' + c.case_id + ' ' + c.time_note).toLowerCase();
          if (!targetStr.includes(query)) return false;
        }}

        return true;
      }});

      currentPage = 1;
      expandedRows.clear();
      renderTable();
    }}

    document.getElementById('tableSearch').addEventListener('input', applyFilters);
    document.getElementById('statusFilter').addEventListener('change', applyFilters);

    // 初始渲染
    applyFilters();
  </script>
</body>
</html>"""

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✅ 極速版儀表板已產出至：{OUTPUT_HTML} 及 index.html")

if __name__ == "__main__":
    main()
