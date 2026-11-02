// POST /api/trial —— 试用申请
//
// 与主站 app/api.py 的 trial 完全对齐：
//   - 一个 typekey 终身只能领一次
//   - 已存在时不重发试用期，但非空 user/email 会被覆盖
//   - 并发靠主键冲突兜底

import { getClient, resetClient } from "../_shared/db.ts";
import {
  applyContact,
  cleanOptional,
  cleanTypekey,
  envelope,
  jsonResponse,
  nowBeijing,
  trialExpiry,
  userSummary,
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

    // 一个 typekey 终身只能领一次——这是试用的全部规则，不做 IP 限流
    const existing = await client.query(
      "SELECT typekey, addtime, exptime, status, last_time, `user`, email, `count` " +
        "FROM license_user WHERE typekey = ?",
      [typekey],
    );

    if (existing.length > 0) {
      const row = existing[0] as unknown as LicenseUserRow;
      // 试用期不重发，但 user / email 传了非空值就顺手覆盖
      const changed = applyContact(row, payload.user, payload.email);
      if (changed) {
        await client.execute(
          "UPDATE license_user SET `user` = ?, email = ? WHERE typekey = ?",
          [row.user, row.email, typekey],
        );
        // 回读，保证返回的是库里的真实值
        const again = await client.query(
          "SELECT typekey, addtime, exptime FROM license_user WHERE typekey = ?",
          [typekey],
        );
        if (again.length > 0) {
          return jsonResponse(
            envelope(
              "TRIAL_ALREADY_USED",
              "不支持多次试用",
              userSummary(again[0] as unknown as LicenseUserRow),
            ),
          );
        }
      }
      return jsonResponse(
        envelope("TRIAL_ALREADY_USED", "不支持多次试用", userSummary(row)),
      );
    }

    // 读试用天数（缺省 1 天）
    const settings = await client.query(
      "SELECT skey, svalue FROM setting WHERE skey = 'trial_days'",
    );
    let trialDays = 1;
    if (settings.length > 0) {
      const raw = (settings[0] as unknown as { svalue: string | null }).svalue;
      const parsed = parseInt(String(raw ?? ""), 10);
      if (!Number.isNaN(parsed) && parsed > 0) trialDays = parsed;
    }

    const now = nowBeijing();
    const exptime = trialExpiry(now, trialDays);

    try {
      await client.execute(
        "INSERT INTO license_user " +
          "(typekey, addtime, exptime, status, `user`, email, `count`) " +
          "VALUES (?, ?, ?, 'active', ?, ?, 0)",
        [
          typekey,
          now,
          exptime,
          cleanOptional(payload.user),
          cleanOptional(payload.email),
        ],
      );
    } catch (error) {
      // 并发下另一个请求抢先建了号，退回到「已领过」
      const message = String(error);
      if (message.includes("Duplicate entry") || message.includes("ER_DUP_ENTRY")) {
        const again = await client.query(
          "SELECT typekey, addtime, exptime, status, last_time, `user`, email, `count` " +
            "FROM license_user WHERE typekey = ?",
          [typekey],
        );
        if (again.length > 0) {
          const row = again[0] as unknown as LicenseUserRow;
          if (applyContact(row, payload.user, payload.email)) {
            await client.execute(
              "UPDATE license_user SET `user` = ?, email = ? WHERE typekey = ?",
              [row.user, row.email, typekey],
            );
          }
          return jsonResponse(
            envelope("TRIAL_ALREADY_USED", "不支持多次试用", userSummary(row)),
          );
        }
      }
      throw error;
    }

    return jsonResponse(
      envelope("TRIAL_GRANTED", "申请已通过", {
        typekey,
        addtime: now,
        exptime,
      }),
    );
  } catch (error) {
    console.error("trial failed:", error);
    await resetClient();
    return jsonResponse(envelope("SERVER_ERROR", "服务暂时不可用"), 500);
  }
});
