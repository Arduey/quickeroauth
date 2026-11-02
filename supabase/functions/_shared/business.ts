// 三个业务接口的共享逻辑，逐条对齐 app/api.py。
//
// 目标：响应包、code、message、字段名、业务规则与主站完全一致，
// 客户端不需要为「备用线路」做任何区分。

import {
  addDays,
  addMonths,
  addYears,
  formatPseudo,
  laterOf,
  nowBeijing,
  parseBeijing,
  PERMANENT_YEARS,
} from "./timeutil.ts";

export const TYPEKEY_MAX_LEN = 64;

// ---------------------------------------------------------------- 响应包

export function envelope(code: string, message: string, data: unknown = null) {
  return { code, message, data };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

// ---------------------------------------------------------------- 入参清洗

export function cleanTypekey(raw: unknown): string | null {
  const value = String(raw ?? "").trim();
  if (!value || value.length > TYPEKEY_MAX_LEN) return null;
  return value;
}

/** 空字符串按未传处理——不覆盖库里已有的值。 */
export function cleanOptional(raw: unknown): string | null {
  const value = String(raw ?? "").trim();
  return value || null;
}

export interface ContactRow {
  user: string | null;
  email: string | null;
}

/**
 * 把非空的 user / email 覆盖到账户上，空值不覆盖。返回是否真的改了。
 * 对应 app/api.py 的 _apply_contact。
 */
export function applyContact(
  row: ContactRow,
  user: unknown,
  email: unknown,
): boolean {
  const newUser = cleanOptional(user);
  const newEmail = cleanOptional(email);
  let changed = false;
  if (newUser) {
    changed = row.user !== newUser;
    row.user = newUser;
  }
  if (newEmail) {
    changed = changed || row.email !== newEmail;
    row.email = newEmail;
  }
  return changed;
}

// ---------------------------------------------------------------- 规格解析

/**
 * 把规格文本解析成 [月数, 是否永久]。对应 app/api.py 的 parse_sku。
 *   含「永久」→ 永久
 *   含「年」  → 数字 × 12
 *   含「月」  → 数字
 *   都不含 → 无法识别，返回 [null, false]
 */
export function parseSku(sku: string): [number | null, boolean] {
  const text = String(sku ?? "").trim();
  if (!text) return [null, false];

  const digits = text.replace(/[^0-9]/g, "");
  const chinese = text.replace(/[0-9A-Za-z]/g, "");
  const number = digits ? parseInt(digits, 10) : 0;

  if (chinese.includes("永久")) return [0, true];
  if (chinese.includes("年")) return [number * 12, false];
  if (chinese.includes("月")) return [number, false];
  return [null, false];
}

// ---------------------------------------------------------------- 数据库行

export interface LicenseUserRow extends ContactRow {
  typekey: string;
  addtime: string | null;
  exptime: string;
  status: string | null;
  last_time: string | null;
  count: number | null;
}

export function userSummary(row: LicenseUserRow) {
  return {
    typekey: row.typekey,
    addtime: row.addtime,
    exptime: row.exptime,
  };
}

/** 数据库返回的 DATETIME 可能是 Date 或字符串，统一成北京墙钟字符串。 */
export function toBeijingText(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return formatPseudo(value);
  return String(value);
}

// ---------------------------------------------------------------- 设置项

/** 与 app/settings.py 的 DEFAULTS 保持一致。 */
const SETTING_DEFAULTS: Record<string, string> = {
  site_domain: "",
  trial_days: "1",
  platform_base_url: "https://test.arduey.top",
  platform_email: "",
};

export function settingValue(
  rows: Array<{ skey: string; svalue: string | null }>,
  key: string,
): string {
  const found = rows.find((row) => row.skey === key);
  if (found && found.svalue !== null && found.svalue !== "") {
    return found.svalue;
  }
  return SETTING_DEFAULTS[key] ?? "";
}

// ---------------------------------------------------------------- 发卡平台

export interface PlatformOrder {
  order_id: string;
  creat_time?: string;
  type?: string;
  title?: string;
  sku?: string;
  total?: number | string;
  seller?: string;
}

export class PlatformError extends Error {
  kind: "unavailable" | "not_found";
  constructor(kind: "unavailable" | "not_found", message: string) {
    super(message);
    this.kind = kind;
  }
}

/**
 * 向发卡平台查单。对应 app/platform.py 的 query_order。
 * GET {base_url}/api/order/query?email={商户邮箱}&order_no={订单号}
 */
export async function queryOrder(
  baseUrl: string,
  email: string,
  orderNo: string,
): Promise<PlatformOrder> {
  if (!baseUrl || !email) {
    throw new PlatformError("unavailable", "平台地址或商户邮箱未配置");
  }

  const url = new URL(baseUrl.replace(/\/+$/, "") + "/api/order/query");
  url.searchParams.set("email", email);
  url.searchParams.set("order_no", orderNo);

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method: "GET",
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    // 超时或网络异常都可重试
    throw new PlatformError("unavailable", "订单查询失败");
  }

  if (response.status >= 500) {
    throw new PlatformError("unavailable", "订单查询失败");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new PlatformError("not_found", "订单不存在");
  }

  if (typeof payload !== "object" || payload === null) {
    throw new PlatformError("not_found", "订单不存在");
  }

  const body = payload as Record<string, unknown>;
  if (response.status !== 200 || !body.ok) {
    const detail = String(body.message ?? "").trim();
    throw new PlatformError("not_found", detail || "订单不存在");
  }

  const data = body.data;
  if (typeof data !== "object" || data === null) {
    throw new PlatformError("not_found", "订单不存在");
  }
  const order = data as PlatformOrder;
  if (!order.order_id) {
    throw new PlatformError("not_found", "订单不存在");
  }
  return order;
}

// ---------------------------------------------------------------- 到期时间

/**
 * 算出新的到期时间，与 app/api.py 的 redeem 保持同一套规则：
 *   永久 → max(原到期, 现在) + 100 年
 *   其余 → max(原到期, 现在) + 月数
 */
export function computeNewExpiry(
  baseExpiryText: string,
  nowText: string,
  months: number | null,
  isPermanent: boolean,
): string {
  const now = parseBeijing(nowText)!;
  const base = parseBeijing(baseExpiryText) ?? now;
  const start = laterOf(base, now);

  if (isPermanent) {
    return formatPseudo(addYears(start, PERMANENT_YEARS));
  }
  return formatPseudo(addMonths(start, months ?? 0));
}

/** 试用起算：现在 + trial_days 天。 */
export function trialExpiry(nowText: string, days: number): string {
  return formatPseudo(addDays(parseBeijing(nowText)!, days));
}

/** 建号垫底：现在 + 1 天（订单比账户先到时用）。 */
export function bootstrapExpiry(nowText: string): string {
  return formatPseudo(addDays(parseBeijing(nowText)!, 1));
}

export { nowBeijing, parseBeijing };
