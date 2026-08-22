// src/worker.js - Cloudflare Worker Entry Point (Cloudflare D1 Database 版)

import { parseDt, formatDateStr, fetchFraudbusterStages, checkThreadsStatus } from './parser.js';
import { renderDashboardHTML } from './dashboard.js';

const BATCH_SIZE = 20; // 每次 Cron 執行最多處理 20 筆
const FIRST_CHECK_DELAY_DAYS = 1; // 通報滿 2 天 (48h)
const RECHECK_INTERVAL_DAYS = 1;  // 間隔滿 1 天 (24h)

/**
 * 核心增量追蹤執行邏輯 (使用 Cloudflare D1)
 */
async function runTrackingBatch(env) {
  // 從 D1 讀取所有待查詢的 reports 與對應的 cases 歷程紀錄
  const { results } = await env.DB.prepare(`
    SELECT r.url, r.username, r.case_id, r.reported_at,
           c.fraudbuster_url, c.fb_is_final, c.timeline_stages,
           c.threads_actual_status, c.first_checked_at, c.last_checked_at,
           c.takedown_detected_at, c.check_count, c.check_history
    FROM reports r
    LEFT JOIN cases c ON r.url = c.url
    WHERE c.threads_actual_status IS NULL OR c.threads_actual_status != 'Removed'
  `).all();

  const now = new Date();
  const eligibleItems = [];

  for (const row of results) {
    // 已下架者不再重複爬取
    if (row.threads_actual_status === "Removed") {
      continue;
    }

    const reportedDt = parseDt(row.reported_at);

    if (!row.last_checked_at) {
      // 首次檢查門檻 (滿 2 天)
      if (reportedDt && (now.getTime() - reportedDt.getTime()) < FIRST_CHECK_DELAY_DAYS * 24 * 3600 * 1000) {
        continue;
      }
      eligibleItems.push(row);
    } else {
      // 複查門檻 (間隔滿 1 天)
      const lastCheckedDt = parseDt(row.last_checked_at);
      if (lastCheckedDt && (now.getTime() - lastCheckedDt.getTime()) < RECHECK_INTERVAL_DAYS * 24 * 3600 * 1000) {
        continue;
      }
      eligibleItems.push(row);
    }
  }

  const batch = eligibleItems.slice(0, BATCH_SIZE);
  console.log(`⏰ [Cron Trigger] 當前時段處理 ${batch.length} 筆案件 (總合格數: ${eligibleItems.length})`);

  if (batch.length === 0) {
    return { processed: 0, message: "目前無達到檢查時間點的案件" };
  }

  const nowStr = formatDateStr(now);
  const statements = [];

  for (const row of batch) {
    const url = row.url;
    const username = row.username || "";
    const caseId = row.case_id || "";
    const reportedAt = row.reported_at || "";

    let timelineStages = [];
    if (row.timeline_stages) {
      try { timelineStages = typeof row.timeline_stages === "string" ? JSON.parse(row.timeline_stages) : row.timeline_stages; } catch (e) {}
    }

    let checkHistory = [];
    if (row.check_history) {
      try { checkHistory = typeof row.check_history === "string" ? JSON.parse(row.check_history) : row.check_history; } catch (e) {}
    }

    // 1. 數發部歷程獲取
    let fbData = { fb_url: "", timeline_stages: [], fb_is_final: false };
    if (row.fb_is_final && timelineStages.length > 0) {
      fbData = {
        fb_url: row.fraudbuster_url || "",
        timeline_stages: timelineStages,
        fb_is_final: true
      };
    } else if (caseId) {
      fbData = await fetchFraudbusterStages(caseId);
    }

    // 2. Threads 狀態檢測
    const thStatus = await checkThreadsStatus(url, username);

    const firstCheckedAt = row.first_checked_at || nowStr;
    const checkCount = (row.check_count || 0) + 1;

    checkHistory.push({
      check_index: checkCount,
      checked_at: nowStr,
      threads_status: thStatus
    });

    let takedownDetectedAt = row.takedown_detected_at;
    if (thStatus === "Removed" && !takedownDetectedAt) {
      takedownDetectedAt = nowStr;
    }

    // 3. 準備 D1 SQL Batch Upsert 語句
    const stmt = env.DB.prepare(`
      INSERT INTO cases (
        url, username, case_id, reported_at, fraudbuster_url, fb_is_final,
        timeline_stages, threads_actual_status, first_checked_at, last_checked_at,
        takedown_detected_at, check_count, check_history, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(url) DO UPDATE SET
        fraudbuster_url = excluded.fraudbuster_url,
        fb_is_final = excluded.fb_is_final,
        timeline_stages = excluded.timeline_stages,
        threads_actual_status = excluded.threads_actual_status,
        first_checked_at = excluded.first_checked_at,
        last_checked_at = excluded.last_checked_at,
        takedown_detected_at = excluded.takedown_detected_at,
        check_count = excluded.check_count,
        check_history = excluded.check_history,
        updated_at = excluded.updated_at
    `).bind(
      url,
      username,
      caseId,
      reportedAt,
      fbData.fb_url,
      fbData.fb_is_final ? 1 : 0,
      JSON.stringify(fbData.timeline_stages),
      thStatus,
      firstCheckedAt,
      nowStr,
      takedownDetectedAt || "",
      checkCount,
      JSON.stringify(checkHistory),
      nowStr
    );

    statements.push(stmt);
  }

  // 4. 批次寫入 D1 資料庫
  if (statements.length > 0) {
    await env.DB.batch(statements);
  }

  return { processed: batch.length, remaining: eligibleItems.length - batch.length };
}

export default {
  /**
   * HTTP 請求處理器 (提供儀表板頁面與 API)
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. 首頁：從 D1 讀取所有 cases 並渲染視覺化儀表板
    if (url.pathname === "/" || url.pathname === "/index.html") {
      const { results } = await env.DB.prepare("SELECT * FROM cases").all();
      
      const dbMap = {};
      for (const row of (results || [])) {
        let stages = [];
        let checks = [];
        try { stages = typeof row.timeline_stages === "string" ? JSON.parse(row.timeline_stages) : (row.timeline_stages || []); } catch (e) {}
        try { checks = typeof row.check_history === "string" ? JSON.parse(row.check_history) : (row.check_history || []); } catch (e) {}

        dbMap[row.url] = {
          username: row.username,
          case_id: row.case_id,
          reported_at: row.reported_at,
          fraudbuster_url: row.fraudbuster_url,
          fb_is_final: Boolean(row.fb_is_final),
          timeline_stages: stages,
          threads_actual_status: row.threads_actual_status,
          first_checked_at: row.first_checked_at,
          last_checked_at: row.last_checked_at,
          takedown_detected_at: row.takedown_detected_at,
          check_count: row.check_count,
          check_history: checks,
          threads_url: row.url
        };
      }

      const html = renderDashboardHTML(dbMap);
      return new Response(html, {
        headers: { "Content-Type": "text/html; charset=UTF-8" }
      });
    }

    // 2. API: 獲取 D1 中的 cases JSON
    if (url.pathname === "/api/db") {
      const { results } = await env.DB.prepare("SELECT * FROM cases").all();
      return new Response(JSON.stringify(results, null, 2), {
        headers: { "Content-Type": "application/json; charset=UTF-8" }
      });
    }

    // 3. API: 手動觸發一次增量追蹤
    if (url.pathname === "/api/trigger" && request.method === "POST") {
      const authHeader = request.headers.get("Authorization");
      if (env.ADMIN_KEY && authHeader !== `Bearer ${env.ADMIN_KEY}`) {
        return new Response(JSON.stringify({ error: "Unauthorized" }), { status: 401 });
      }

      ctx.waitUntil(runTrackingBatch(env));

      return new Response(JSON.stringify({ message: "Tracking batch triggered in background" }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // 4. API: 匯入 Initial Seed Reports / Cases 至 D1
    if (url.pathname === "/api/seed" && request.method === "POST") {
      const authHeader = request.headers.get("Authorization");
      if (env.ADMIN_KEY && authHeader !== `Bearer ${env.ADMIN_KEY}`) {
        return new Response(JSON.stringify({ error: "Unauthorized" }), { status: 401 });
      }

      try {
        const body = await request.json();
        const statements = [];

        if (body.reports) {
          const reportList = Array.isArray(body.reports) ? body.reports : Object.entries(body.reports).map(([url, info]) => ({ url, ...info }));
          for (const item of reportList) {
            statements.push(env.DB.prepare(`
              INSERT INTO reports (url, username, case_id, reported_at)
              VALUES (?, ?, ?, ?)
              ON CONFLICT(url) DO UPDATE SET
                username = excluded.username,
                case_id = excluded.case_id,
                reported_at = excluded.reported_at
            `).bind(item.url, item.username || '', item.case_id || '', item.reported_at || ''));
          }
        }

        if (body.tracking_db) {
          const caseList = Array.isArray(body.tracking_db) ? body.tracking_db : Object.entries(body.tracking_db).map(([url, info]) => ({ url, ...info }));
          for (const r of caseList) {
            statements.push(env.DB.prepare(`
              INSERT INTO cases (
                url, username, case_id, reported_at, fraudbuster_url, fb_is_final,
                timeline_stages, threads_actual_status, first_checked_at, last_checked_at,
                takedown_detected_at, check_count, check_history, updated_at
              ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(url) DO UPDATE SET
                username = excluded.username,
                case_id = excluded.case_id,
                reported_at = excluded.reported_at,
                fraudbuster_url = excluded.fraudbuster_url,
                fb_is_final = excluded.fb_is_final,
                timeline_stages = excluded.timeline_stages,
                threads_actual_status = excluded.threads_actual_status,
                first_checked_at = excluded.first_checked_at,
                last_checked_at = excluded.last_checked_at,
                takedown_detected_at = excluded.takedown_detected_at,
                check_count = excluded.check_count,
                check_history = excluded.check_history,
                updated_at = excluded.updated_at
            `).bind(
              r.threads_url || r.url,
              r.username || '',
              r.case_id || '',
              r.reported_at || '',
              r.fraudbuster_url || '',
              r.fb_is_final ? 1 : 0,
              typeof r.timeline_stages === "string" ? r.timeline_stages : JSON.stringify(r.timeline_stages || []),
              r.threads_actual_status || 'Active',
              r.first_checked_at || '',
              r.last_checked_at || '',
              r.takedown_detected_at || '',
              r.check_count || 1,
              typeof r.check_history === "string" ? r.check_history : JSON.stringify(r.check_history || []),
              r.last_checked_at || ''
            ));
          }
        }

        // 分批執行（D1 批次上限預設為 128 條）
        for (let i = 0; i < statements.length; i += 100) {
          const chunk = statements.slice(i, i + 100);
          await env.DB.batch(chunk);
        }

        return new Response(JSON.stringify({ message: `Data successfully seeded to D1 (${statements.length} queries)` }), {
          headers: { "Content-Type": "application/json" }
        });
      } catch (e) {
        return new Response(JSON.stringify({ error: e.message }), { status: 400 });
      }
    }

    return new Response("Not Found", { status: 404 });
  },

  /**
   * Cron Scheduled 處理器
   */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runTrackingBatch(env));
  }
};
