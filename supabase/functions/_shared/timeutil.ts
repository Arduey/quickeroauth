// 时间工具：与 app/timeutil.py 保持完全一致的语义。
//
// 全系统统一北京时间，库里所有时间列都是 DATETIME（不带时区），
// 存的就是北京墙上时间。这里一律用「UTC+8 偏移后的墙钟字符串」处理，
// 不掺任何本地时区（Deno 运行在 UTC，不能依赖 new Date() 的本地时间）。

export const BEIJING_OFFSET_MINUTES = 8 * 60;

// 「永久」= 在 max(原到期时间, 现在) 的基础上再加 100 年。
// 这个常量必须与 app/timeutil.py 的 PERMANENT_YEARS 一致。
export const PERMANENT_YEARS = 100;

// 年份越过 9999 时的兜底上限（对应 Python 的 PERMANENT）
export const MAX_DATETIME = "9999-12-31 23:59:59";

/** 当前北京时间，格式 "YYYY-MM-DD HH:MM:SS"。 */
export function nowBeijing(): string {
  return formatBeijing(new Date());
}

/** 把 Date 转成北京墙钟字符串，精确到秒。 */
export function formatBeijing(date: Date): string {
  const shifted = new Date(date.getTime() + BEIJING_OFFSET_MINUTES * 60_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-` +
    `${pad(shifted.getUTCDate())} ${pad(shifted.getUTCHours())}:` +
    `${pad(shifted.getUTCMinutes())}:${pad(shifted.getUTCSeconds())}`
  );
}

/** 把北京墙钟字符串解析成「伪 Date」——用 UTC 字段承载北京墙钟，便于比较与计算。 */
export function parseBeijing(text: string | null | undefined): Date | null {
  if (!text) return null;
  const raw = String(text).trim();
  // 兼容 "YYYY-MM-DD HH:MM:SS"、"YYYY-MM-DDTHH:MM:SS"、"YYYY-MM-DD"
  const m = raw.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}):(\d{2}))?/,
  );
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  const date = new Date(
    Date.UTC(
      Number(y),
      Number(mo) - 1,
      Number(d),
      Number(h ?? 0),
      Number(mi ?? 0),
      Number(s ?? 0),
    ),
  );
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 把「伪 Date」格式化回墙钟字符串。 */
export function formatPseudo(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-` +
    `${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:` +
    `${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`
  );
}

function daysInMonth(year: number, month1: number): number {
  // month1 是 1-12
  return new Date(Date.UTC(year, month1, 0)).getUTCDate();
}

/**
 * 加月份，处理月末溢出（1月31日 + 1个月 = 2月28/29日）。
 * 与 Python 的 timeutil.add_months 完全一致。
 */
export function addMonths(value: Date, months: number): Date {
  if (months === 0) return value;
  const total = value.getUTCMonth() + months;
  const year = value.getUTCFullYear() + Math.floor(total / 12);
  const month0 = ((total % 12) + 12) % 12; // 0-11
  if (year > 9999) return parseBeijing(MAX_DATETIME)!;
  const day = Math.min(value.getUTCDate(), daysInMonth(year, month0 + 1));
  return new Date(
    Date.UTC(
      year,
      month0,
      day,
      value.getUTCHours(),
      value.getUTCMinutes(),
      value.getUTCSeconds(),
    ),
  );
}

/** 加年份，内部折成月份，与 app/timeutil.py 的 add_years 一致。 */
export function addYears(value: Date, years: number): Date {
  return addMonths(value, years * 12);
}

/** 加天数。 */
export function addDays(value: Date, days: number): Date {
  return new Date(value.getTime() + days * 86_400_000);
}

/** 取两个时间中较晚的那个（续期时用：过期就从现在起算）。 */
export function laterOf(a: Date, b: Date): Date {
  return a > b ? a : b;
}
