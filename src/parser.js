// src/parser.js - HTML Parser & Scraping logic for Workers

const BROWSER_HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
};

const SPIDER_HEADERS = {
  "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.html)",
  "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
};

const GENERIC_LOGIN_TITLES = ["Threads • 登入", "Threads • Log in", "Threads - 登入", "Threads", "Instagram"];
const GENERIC_LOGIN_DESCS = ["加入 Threads 即可分享意見", "Say more on Threads", "使用你的 Instagram 登入"];
const TERMINAL_KEYWORDS = [
  "通知Meta移除", "通知 Meta 移除", "高風險訊息", "非屬詐騙", "非詐騙", "未通過", "重複通報", "已結案"
];

/**
 * 強健解析多種日期時間格式
 */
export function parseDt(dtStr) {
  if (!dtStr || String(dtStr).trim() === "-" || String(dtStr).trim() === "" || String(dtStr).trim() === "None") {
    return null;
  }
  const cleanStr = String(dtStr).trim().replace(/\s+/g, " ");
  // ISO or Standard YYYY-MM-DD HH:mm:ss
  const norm = cleanStr.replace(/\//g, "-");
  const d = new Date(norm.includes("T") ? norm : norm.replace(" ", "T") + "+08:00");
  return isNaN(d.getTime()) ? null : d;
}

/**
 * 格式化日期為 YYYY-MM-DD HH:mm:ss
 */
export function formatDateStr(dateObj) {
  if (!dateObj) return "";
  const d = new Date(dateObj.getTime() + (8 * 3600 * 1000)); // UTC+8
  return d.toISOString().replace("T", " ").substring(0, 19);
}

/**
 * 抓取打詐通報網各階段原始時間與文字
 */
export async function fetchFraudbusterStages(caseId) {
  const url = `https://fraudbuster.digiat.org.tw/accessibility/detail?listType=N&id=${caseId}`;
  const result = {
    fb_url: url,
    timeline_stages: [],
    fb_is_final: false
  };

  try {
    const response = await fetch(url, { headers: BROWSER_HEADERS });
    if (response.status === 200) {
      const html = await response.text();
      
      // 提取 timeline <li> 區塊
      const stages = [];
      const liMatches = [...html.matchAll(/<li[^>]*>(.*?)<\/li>/gs)];
      let stageNum = 1;

      for (const match of liMatches) {
        const liContent = match[0];
        const timeMatch = liContent.match(/<time[^>]*>(.*?)<\/time>/i);
        const pMatch = liContent.match(/<p[^>]*>(.*?)<\/p>/i);

        if (pMatch) {
          const timeText = timeMatch ? timeMatch[1].replace(/<[^>]+>/g, '').trim() : '';
          const pText = pMatch[1].replace(/<[^>]+>/g, '').trim();
          const isComplete = liContent.includes('complete');

          stages.push({
            stage_num: stageNum++,
            time: timeText,
            desc: pText,
            is_complete: isComplete
          });
        }
      }

      result.timeline_stages = stages;
      const allDesc = stages.map(s => s.desc).join(" ");

      if (stages.length >= 3 || TERMINAL_KEYWORDS.some(kw => allDesc.includes(kw)) || (stages.length > 0 && stages[stages.length - 1].is_complete)) {
        result.fb_is_final = true;
      }
    }
  } catch (err) {
    console.error(`Error fetching fraudbuster detail for ${caseId}:`, err);
  }

  return result;
}

/**
 * 檢查 Threads 帳號實際存活狀態
 */
export async function checkThreadsStatus(url, username) {
  try {
    const response = await fetch(url, { headers: SPIDER_HEADERS, redirect: "follow" });

    if (response.status === 404) {
      return "Removed";
    } else if (response.status === 429) {
      return "Rate Limited";
    } else if (response.status !== 200) {
      return `HTTP_${response.status}`;
    }

    const html = await response.text();

    // 提取 title, og:title, og:description
    const titleMatch = html.match(/<title[^>]*>(.*?)<\/title>/i);
    const title = titleMatch ? titleMatch[1].trim() : "";

    const ogTitleMatch = html.match(/<meta[^>]*property=["']og:title["'][^>]*content=["']([^"']*)["']/i) ||
                         html.match(/<meta[^>]*content=["']([^"']*)["'][^>]*property=["']og:title["']/i);
    const ogTitle = ogTitleMatch ? ogTitleMatch[1].trim() : "";

    const ogDescMatch = html.match(/<meta[^>]*property=["']og:description["'][^>]*content=["']([^"']*)["']/i) ||
                        html.match(/<meta[^>]*content=["']([^"']*)["'][^>]*property=["']og:description["']/i);
    const ogDesc = ogDescMatch ? ogDescMatch[1].trim() : "";

    if (GENERIC_LOGIN_TITLES.includes(ogTitle) || GENERIC_LOGIN_TITLES.includes(title) || GENERIC_LOGIN_DESCS.some(kw => ogDesc.includes(kw))) {
      return "Removed";
    }

    const cleanUser = (username || "").toLowerCase().trim().replace(/^@/, "");
    if (cleanUser && (ogTitle.toLowerCase().includes(cleanUser) || title.toLowerCase().includes(cleanUser))) {
      return "Active";
    }

    return "Removed";
  } catch (err) {
    console.error(`Error checking threads status for ${url}:`, err);
    return "Error";
  }
}
