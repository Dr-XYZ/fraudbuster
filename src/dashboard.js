// src/dashboard.js - Generates the Web Dashboard HTML from tracking DB records

import { parseDt, formatDateStr } from './parser.js';

function formatHoursReadable(hours) {
  if (hours < 24) {
    return `${hours.toFixed(1)} 小時`;
  }
  const days = Math.floor(hours / 24);
  const remH = hours % 24;
  return `${days}天 ${remH.toFixed(1)}小時 (${hours.toFixed(1)}h)`;
}

export function renderDashboardHTML(dbData) {
  const records = dbData ? Object.values(dbData) : [];
  const now = new Date();
  const nowStr = formatDateStr(now);

  const lagBuckets = {
    "< 24小時": 0,
    "24 ~ 48小時 (1~2天)": 0,
    "48 ~ 72小時 (2~3天)": 0,
    "72 ~ 120小時 (3~5天)": 0,
    "> 120小時 (> 5天)": 0
  };

  const cohortDates = {};
  const takedownDurationList = [];
  const activeLagDurationList = [];
  const metaCases = [];

  for (const r of records) {
    const stages = r.timeline_stages || [];
    const thStatus = r.threads_actual_status || "未知";
    const reportedDt = parseDt(r.reported_at);

    let metaNotifiedDt = null;
    let metaStageText = "";
    for (const s of stages) {
      const desc = s.desc || "";
      if (desc.includes("通知Meta移除") || desc.includes("通知 Meta 移除")) {
        metaNotifiedDt = parseDt(s.time);
        metaStageText = desc;
        break;
      }
    }

    if (!metaNotifiedDt) continue;

    const dateKey = formatDateStr(metaNotifiedDt).substring(0, 10);
    if (!cohortDates[dateKey]) {
      cohortDates[dateKey] = { removed: 0, active: 0 };
    }

    const tTakedown = parseDt(r.takedown_detected_at);
    const lastCheckedDt = parseDt(r.last_checked_at);

    let officialReviewHours = null;
    if (reportedDt && metaNotifiedDt && metaNotifiedDt >= reportedDt) {
      officialReviewHours = Number(((metaNotifiedDt.getTime() - reportedDt.getTime()) / (3600 * 1000)).toFixed(2));
    }

    const isRemoved = (thStatus === "Removed");
    let lagHours = 0.0;
    let metaHandlingHours = null;
    let timeNote = "";

    if (isRemoved) {
      cohortDates[dateKey].removed += 1;
      const endT = tTakedown || lastCheckedDt || now;
      if (endT >= metaNotifiedDt) {
        metaHandlingHours = Number(((endT.getTime() - metaNotifiedDt.getTime()) / (3600 * 1000)).toFixed(1));
        takedownDurationList.push(metaHandlingHours);
        timeNote = `已處置下架 (耗時 ${formatHoursReadable(metaHandlingHours)})`;
      } else {
        metaHandlingHours = 0.0;
        timeNote = "已處置下架";
      }
    } else {
      cohortDates[dateKey].active += 1;
      lagHours = Number((Math.max(0, (now.getTime() - metaNotifiedDt.getTime()) / (3600 * 1000))).toFixed(1));
      activeLagDurationList.push(lagHours);
      const mm = String(now.getMonth() + 1).padStart(2, '0');
      const dd = String(now.getDate()).padStart(2, '0');
      const hh = String(now.getHours()).padStart(2, '0');
      const min = String(now.getMinutes()).padStart(2, '0');
      timeNote = `滯留未下架 ${formatHoursReadable(lagHours)} (統計至 ${mm}/${dd} ${hh}:${min})`;

      if (lagHours < 24) lagBuckets["< 24小時"] += 1;
      else if (lagHours < 48) lagBuckets["24 ~ 48小時 (1~2天)"] += 1;
      else if (lagHours < 72) lagBuckets["48 ~ 72小時 (2~3天)"] += 1;
      else if (lagHours < 120) lagBuckets["72 ~ 120小時 (3~5天)"] += 1;
      else lagBuckets["> 120小時 (> 5天)"] += 1;
    }

    metaCases.push({
      raw: r,
      reportedDt,
      metaNotifiedDt,
      tTakedown,
      lastCheckedDt,
      thStatus,
      isRemoved,
      lagHours,
      metaHandlingHours,
      officialReviewHours,
      timeNote
    });
  }

  const totalMetaNotified = metaCases.length;
  const totalRemoved = metaCases.filter(c => c.isRemoved).length;
  const totalUnremoved = totalMetaNotified - totalRemoved;

  const inactionRate = totalMetaNotified > 0 ? ((totalUnremoved / totalMetaNotified) * 100).toFixed(1) : "0.0";
  const takedownRate = totalMetaNotified > 0 ? ((totalRemoved / totalMetaNotified) * 100).toFixed(1) : "0.0";

  const avgLag = activeLagDurationList.length > 0
    ? (activeLagDurationList.reduce((a, b) => a + b, 0) / activeLagDurationList.length).toFixed(1) + " 小時"
    : "0 小時";

  const maxLag = activeLagDurationList.length > 0
    ? Math.max(...activeLagDurationList).toFixed(1) + " 小時"
    : "0 小時";

  const avgTakedown = takedownDurationList.length > 0
    ? (takedownDurationList.reduce((a, b) => a + b, 0) / takedownDurationList.length).toFixed(1) + " 小時"
    : "尚無數據";

  const sortedCohortDates = Object.keys(cohortDates).sort();
  const cohortRemovedData = sortedCohortDates.map(d => cohortDates[d].removed);
  const cohortActiveData = sortedCohortDates.map(d => cohortDates[d].active);

  // 排序：尚未移除 (Active) 且滯留最久者置頂
  metaCases.sort((a, b) => {
    if (a.isRemoved !== b.isRemoved) return a.isRemoved ? 1 : -1;
    return b.lagHours - a.lagHours;
  });

  let rowsHtml = "";
  if (metaCases.length === 0) {
    rowsHtml = `
      <tr>
        <td colspan="7" class="p-12 text-center text-slate-500">
          ⏳ 目前尚無已通知 Meta 的案件資料
        </td>
      </tr>`;
  } else {
    metaCases.forEach((c, idx) => {
      const r = c.raw;
      const isRem = c.isRemoved;
      const thBadge = isRem ? "text-emerald-400" : "text-rose-400 font-bold";
      const thStatusText = isRem ? "已下架 (Removed)" : "仍存活 (Active)";

      const reportedStr = c.reportedDt ? formatDateStr(c.reportedDt) : "-";
      const notifiedStr = c.metaNotifiedDt ? formatDateStr(c.metaNotifiedDt).substring(0, 16) : "-";
      const lastCheckStr = c.lastCheckedDt ? formatDateStr(c.lastCheckedDt) : "-";
      const takedownStr = c.tTakedown ? formatDateStr(c.tTakedown) : "尚未下架";

      let timeBadge = "";
      if (isRem) {
        timeBadge = "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded text-xs font-mono";
      } else {
        if (c.lagHours >= 48) {
          timeBadge = "bg-rose-500/20 text-rose-300 border border-rose-500/40 px-2 py-0.5 rounded text-xs font-mono font-bold";
        } else {
          timeBadge = "bg-amber-500/20 text-amber-300 border border-amber-500/40 px-2 py-0.5 rounded text-xs font-mono";
        }
      }

      let stagesHtml = "";
      (r.timeline_stages || []).forEach(s => {
        const sDt = parseDt(s.time);
        let offsetStr = "";
        if (sDt && c.reportedDt && sDt >= c.reportedDt) {
          const diffH = ((sDt.getTime() - c.reportedDt.getTime()) / (3600 * 1000)).toFixed(1);
          offsetStr = `(+ ${diffH}h)`;
        }
        stagesHtml += `
          <div class="flex items-start space-x-3 text-xs py-1.5 border-b border-slate-900 last:border-0">
            <span class="bg-blue-500/20 text-blue-400 px-1.5 py-0.5 rounded font-mono font-bold">階段 ${s.stage_num || '?'}</span>
            <span class="text-slate-400 font-mono">[${s.time || ''}]</span>
            <span class="text-slate-500 font-mono text-[11px]">${offsetStr}</span>
            <span class="text-slate-200 flex-1">${s.desc || ''}</span>
          </div>`;
      });

      let checksHtml = "";
      (r.check_history || []).forEach(chk => {
        const chkStatus = chk.threads_status || "";
        const cBadge = chkStatus === "Removed" ? "text-emerald-400" : "text-rose-400";
        checksHtml += `
          <span class="inline-block bg-slate-900 border border-slate-800 px-2.5 py-1 rounded text-xs mr-2 mb-1">
            第 ${chk.check_index} 次偵測 ➔ 時間: <span class="font-mono text-slate-300">${chk.checked_at}</span> | 實況: <strong class="${cBadge}">${chkStatus}</strong>
          </span>`;
      });

      rowsHtml += `
        <tr class="case-row hover:bg-slate-850 transition cursor-pointer" onclick="toggleDetail('detail_${idx}')">
          <td class="p-3 font-semibold text-slate-100">@${r.username || ''}</td>
          <td class="p-3 text-slate-400 font-mono text-xs">${reportedStr}</td>
          <td class="p-3 text-blue-300 font-mono text-xs">${notifiedStr}</td>
          <td class="p-3 ${thBadge}">${thStatusText}</td>
          <td class="p-3"><span class="${timeBadge}">${c.timeNote}</span></td>
          <td class="p-3 text-slate-400 text-xs font-mono">${lastCheckStr}</td>
          <td class="p-3 text-right space-x-2 text-xs" onclick="event.stopPropagation()">
            <a href="${r.threads_url || ''}" target="_blank" class="text-blue-400 hover:underline">Threads</a>
            <a href="${r.fraudbuster_url || ''}" target="_blank" class="text-purple-400 hover:underline">通報網</a>
          </td>
        </tr>
        <tr id="detail_${idx}" class="hidden bg-slate-900/40">
          <td colspan="7" class="p-5 border-l-4 border-blue-500 space-y-4">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3 bg-slate-950 p-3 rounded-lg border border-slate-800 text-xs">
              <div>• 通報至官方審畢: <strong class="text-purple-400 font-mono">${c.officialReviewHours !== null ? c.officialReviewHours : '-'} 小時</strong></div>
              <div>• 下架確認時間: <strong class="text-emerald-400 font-mono">${takedownStr}</strong></div>
              <div>• 累計偵測次數: <strong class="text-slate-300 font-mono">${r.check_count || 1} 次</strong></div>
            </div>

            <div>
              <div class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">📋 數發部通報各階段歷程與時間差</div>
              <div class="bg-slate-950 p-3 rounded-lg border border-slate-800">
                ${stagesHtml || '<div class="text-xs text-slate-500">尚無歷程資料</div>'}
              </div>
            </div>

            <div>
              <div class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">⏱️ 系統輪詢檢查時間紀錄</div>
              <div class="p-3 bg-slate-950/80 rounded-lg border border-slate-800">
                ${checksHtml}
              </div>
            </div>
          </td>
        </tr>`;
    });
  }

  return `<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Meta 通報後下架時效與尚未移除時間關係圖 (Cloudflare Workers版)</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-4 md:p-8 font-sans">
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
            🕒 統計計算基準時間：${nowStr} (台北時間 UTC+8)
          </span>
          <span class="text-slate-400">⚡ 由 Cloudflare Workers 即時運算提供</span>
        </div>
      </div>
      <div class="mt-4 md:mt-0 flex gap-2">
        <div class="bg-slate-900 border border-slate-800 px-4 py-2 rounded-xl text-xs text-slate-300">
          已通知 Meta 總數：<span class="text-blue-400 font-bold text-base">${totalMetaNotified}</span> 筆
        </div>
      </div>
    </div>

    <!-- 關鍵指標卡片 -->
    <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <div class="bg-slate-900/90 border border-rose-500/30 p-5 rounded-xl shadow-lg">
        <div class="text-rose-400 text-xs font-bold uppercase tracking-wider">目前尚未移除 (Meta 怠慢)</div>
        <div class="text-3xl font-black text-rose-400 mt-1">${totalUnremoved} <span class="text-xs font-normal text-slate-400">筆</span></div>
        <div class="text-xs text-slate-400 mt-1">未移除率：<span class="text-rose-400 font-bold">${inactionRate}%</span></div>
      </div>

      <div class="bg-slate-900/90 border border-slate-800 p-5 rounded-xl">
        <div class="text-slate-400 text-xs font-semibold uppercase">尚未移除平均滯留時間</div>
        <div class="text-2xl font-black text-amber-400 mt-1">${avgLag}</div>
        <div class="text-xs text-slate-500 mt-1">最長已滯留：${maxLag}</div>
      </div>

      <div class="bg-slate-900/90 border border-emerald-500/20 p-5 rounded-xl">
        <div class="text-emerald-400 text-xs font-bold uppercase tracking-wider">已成功下架數量</div>
        <div class="text-3xl font-black text-emerald-400 mt-1">${totalRemoved} <span class="text-xs font-normal text-slate-400">筆</span></div>
        <div class="text-xs text-slate-400 mt-1">下架執行率：<span class="text-emerald-400 font-bold">${takedownRate}%</span></div>
      </div>

      <div class="bg-slate-900/90 border border-slate-800 p-5 rounded-xl">
        <div class="text-slate-400 text-xs font-semibold uppercase">Meta 平均下架處置時效</div>
        <div class="text-2xl font-black text-blue-400 mt-1">${avgTakedown}</div>
        <div class="text-xs text-slate-500 mt-1">已下架案件之平均處理速度</div>
      </div>
    </div>

    <!-- 圖表區 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <div class="bg-slate-900/90 border border-slate-800 p-5 rounded-xl shadow-xl">
        <div class="flex justify-between items-center mb-3">
          <div>
            <h3 class="text-sm font-bold text-slate-200">📊 尚未移除帳號之「被通知後滯留時間」分佈</h3>
            <p class="text-xs text-slate-500">呈現 ${totalUnremoved} 筆存活帳號已被官方通報多久未處置</p>
          </div>
          <span class="px-2 py-0.5 rounded text-xs bg-rose-500/10 text-rose-400 border border-rose-500/20">滯留階梯分析</span>
        </div>
        <div class="h-64"><canvas id="lagBucketChart"></canvas></div>
      </div>

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

    <!-- 詳細清單表格 -->
    <div class="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-2xl">
      <div class="p-4 border-b border-slate-800 flex flex-col md:flex-row justify-between items-start md:items-center gap-3">
        <div>
          <h3 class="font-bold text-slate-100">📋 案件詳細時間註記清單 (共 ${totalMetaNotified} 筆)</h3>
          <p class="text-xs text-slate-400">點擊任一行可展開「通報各階段歷程時間軸」與「系統輪詢偵測時間點」</p>
        </div>
        <input type="text" id="tableSearch" placeholder="搜尋帳號、Case ID..." class="bg-slate-950 border border-slate-700 px-3 py-1.5 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-rose-500 w-full md:w-72">
      </div>

      <div class="overflow-x-auto max-h-[650px]">
        <table class="w-full text-left text-sm text-slate-300" id="casesTable">
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
            ${rowsHtml}
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <script>
    function toggleDetail(id) {
      const el = document.getElementById(id);
      if (el) el.classList.toggle('hidden');
    }

    new Chart(document.getElementById('lagBucketChart'), {
      type: 'bar',
      data: {
        labels: ${JSON.stringify(Object.keys(lagBuckets))},
        datasets: [{
          label: '尚未移除案件數',
          data: ${JSON.stringify(Object.values(lagBuckets))},
          backgroundColor: ['#fbbf24', '#f97316', '#ef4444', '#b91c1c', '#7f1d1d'],
          borderRadius: 6
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { ticks: { color: '#94a3b8', precision: 0 }, grid: { color: '#1e293b' } },
          x: { ticks: { color: '#94a3b8', font: { size: 11 } }, grid: { display: false } }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => \` 滯留案件: \${ctx.raw} 筆\`
            }
          }
        }
      }
    });

    new Chart(document.getElementById('cohortChart'), {
      type: 'bar',
      data: {
        labels: ${JSON.stringify(sortedCohortDates)},
        datasets: [
          {
            label: '已下架 (Removed)',
            data: ${JSON.stringify(cohortRemovedData)},
            backgroundColor: '#10b981',
            borderRadius: 4
          },
          {
            label: '尚未移除 (Active)',
            data: ${JSON.stringify(cohortActiveData)},
            backgroundColor: '#f43f5e',
            borderRadius: 4
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true, ticks: { color: '#94a3b8' }, grid: { display: false } },
          y: { stacked: true, ticks: { color: '#94a3b8', precision: 0 }, grid: { color: '#1e293b' } }
        },
        plugins: {
          legend: { position: 'bottom', labels: { color: '#94a3b8' } }
        }
      }
    });

    document.getElementById('tableSearch').addEventListener('input', function(e) {
      const val = e.target.value.toLowerCase();
      document.querySelectorAll('#tableBody tr.case-row').forEach(row => {
        const isMatch = row.innerText.toLowerCase().includes(val);
        row.style.display = isMatch ? '' : 'none';
        
        const onclickAttr = row.getAttribute('onclick');
        if (onclickAttr) {
          const m = onclickAttr.match(/'([^']+)'/);
          if (m) {
            const detailRow = document.getElementById(m[1]);
            if (!isMatch && detailRow) {
              detailRow.classList.add('hidden');
            }
          }
        }
      });
    });
  </script>
</body>
</html>`;
}
