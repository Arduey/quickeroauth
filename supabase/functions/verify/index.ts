// POST /api/verify —— 在线校验
//
// 与主站 app/api.py 的 verify 完全对齐：
//   - 只有 active 且未过期才计数、更新 last_time
//   - user / email 非空即覆盖（过期时也覆盖；DISABLED / NOT_FOUND 不写库）

import { getClient, resetClient } from "../_shared/db.ts";
import {
  applyContact,
  cleanTypekey,
  envelope,
  jsonResponse,
  nowBeijing,
  toBeijingText,
  type LicenseUserRow,
} from "../_shared/business.ts";

Deno.serve(async (req: Request) => {
  if (req.method !== "POST") {
    return jsonResponse(envelope("METHOD_NOT_ALLOWED", "只接受 POST"), 405);
  }

  let payload: Record<string, unknown>;
  try {
    payload = await req.json();
  } catch {
    return jsonResponse(envelope("INVALID_KEY", "授权标识无效"));
  }

  const typekey = cleanTypekey(payload.typekey);
  if (typekey === null) {
    return jsonResponse(envelope("INVALID_KEY", "授权标识无效"));
  }

  try {
    const client = await getClient();

    const found = await client.query(
      "SELECT typekey, addtime, exptime, status, last_time, `user`, email, `count` " +
        "FROM license_user WHERE typekey = ?",
      [typekey],
    );

    if (found.length === 0) {
      // 不存在的账户不写库
      return jsonResponse(envelope("NOT_FOUND", "不存在"));
    }

    const row = found[0] as unknown as LicenseUserRow;
    if ((row.status ?? "active") !== "active") {
      // 被封禁的账户即使时间没到也必须拒绝
      return jsonResponse(envelope("DISABLED", "账户已被禁用"));
    }

    const now = nowBeijing();
    const exptimeText = toBeijingText(row.exptime) ?? "";
    // 用字符串比较即可：格式统一为 "YYYY-MM-DD HH:MM:SS"，字典序等于时间序
    const active = exptimeText > now;
    const lastTimeText = toBeijingText(row.last_time);

    // user / email 只要传了非空值就更新，过期与否都接受覆盖
    const changed = applyContact(row, payload.user, payload.email);

    let count = row.count ?? 0;
    let newLastTime = lastTimeText;

    if (active) {
      count = count + 1;
      newLastTime = now;
    }

    // 与主站一致：只在真的有改动时才写库，避免每次校验都产生一次无意义的 UPDATE。
    // active 时同时更新 last_time / count；过期时只覆盖 user / email。
    if (active) {
      await client.execute(
        "UPDATE license_user SET last_time = ?, `count` = ?, `user` = ?, email = ? " +
          "WHERE typekey = ?",
        [newLastTime, count, row.user, row.email, typekey],
      );
    } else if (changed) {
      await client.execute(
        "UPDATE license_user SET `user` = ?, email = ? WHERE typekey = ?",
        [row.user, row.email, typekey],
      );
    }

    const data = {
      typekey: row.typekey,
      addtime: toBeijingText(row.addtime),
      exptime: exptimeText,
      last_time: newLastTime,
      count,
    };

    if (active) {
      return jsonResponse(envelope("ACTIVE", "未过期", data));
    }
    return jsonResponse(envelope("EXPIRED", "已过期", data));
  } catch (error) {
    console.error("verify failed:", error);
    await resetClient();
    return jsonResponse(envelope("SERVER_ERROR", "服务暂时不可用"), 500);
  }
});
