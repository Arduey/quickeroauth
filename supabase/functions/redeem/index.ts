// POST /api/redeem —— 订单核销
//
// 与主站 app/api.py 的 redeem 完全对齐：
//   - 订单号幂等（已核销 → ORDER_ALREADY_USED）
//   - 服务端自己向发卡平台查单
//   - 订单归属按 type 的前缀比对
//   - 永久 → max(原到期, 现在) + 100 年
//   - user / email 非空即覆盖

import { getClient, resetClient } from "../_shared/db.ts";
import {
  applyContact,
  bootstrapExpiry,
  cleanTypekey,
  computeNewExpiry,
  envelope,
  jsonResponse,
  nowBeijing,
  parseBeijing,
  parseSku,
  PlatformError,
  queryOrder,
  settingValue,
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

  const orderNo = String(payload.ordernumber ?? "").trim();
  if (!orderNo) {
    return jsonResponse(envelope("ORDER_NOT_FOUND", "订单不存在"));
  }

  const prefix = typekey.split("-")[0];

  try {
    const client = await getClient();

    // 幂等：这张单已经核销过
    const used = await client.query(
      "SELECT ordernumber, usetime FROM license_order WHERE ordernumber = ?",
      [orderNo],
    );
    if (used.length > 0) {
      const usedAt = toBeijingText(
        (used[0] as unknown as { usetime: unknown }).usetime,
      ) ?? "";
      return jsonResponse(
        envelope("ORDER_ALREADY_USED", `订单已于${usedAt}使用过`),
      );
    }

    // 平台配置
    const settings = await client.query(
      "SELECT skey, svalue FROM setting WHERE skey IN ('platform_base_url','platform_email')",
    );
    const rows = settings as unknown as Array<{
      skey: string;
      svalue: string | null;
    }>;
    const baseUrl = settingValue(rows, "platform_base_url");
    const shopEmail = settingValue(rows, "platform_email");

    let order;
    try {
      order = await queryOrder(baseUrl, shopEmail, orderNo);
    } catch (error) {
      if (error instanceof PlatformError) {
        if (error.kind === "unavailable") {
          return jsonResponse(envelope("PLATFORM_UNAVAILABLE", "订单查询失败"));
        }
        if (String(error.message ?? "").includes("商户")) {
          return jsonResponse(
            envelope("PLATFORM_CONFIG", "发卡平台的商户邮箱配置有误"),
          );
        }
        return jsonResponse(envelope("ORDER_NOT_FOUND", "订单不存在"));
      }
      throw error;
    }

    // 订单归属：平台的 type 是完整商品名（如「亚马逊-Quicker授权」），
    // typekey 的前缀是产品线名（「亚马逊」），两边都取第一段来比。
    const orderType = String(order.type ?? "").trim();
    const orderSeries = orderType.split("-")[0];
    if (orderSeries !== prefix) {
      return jsonResponse(
        envelope("PRODUCT_MISMATCH", `输入的订单非${prefix}系列订单`, {
          order_type: orderType,
          expected_prefix: prefix,
        }),
      );
    }

    const rawSku = String(order.sku ?? "");
    const [months, isPermanent] = parseSku(rawSku);
    if (months === null) {
      // 归属是对的，卡在规格上：把原样值带出来
      return jsonResponse(
        envelope("PRODUCT_MISMATCH", `输入的订单非${prefix}系列订单`, {
          order_type: orderType,
          sku: rawSku,
          reason: "sku_unrecognized",
        }),
      );
    }

    const now = nowBeijing();

    const found = await client.query(
      "SELECT typekey, addtime, exptime, status, last_time, `user`, email, `count` " +
        "FROM license_user WHERE typekey = ?",
      [typekey],
    );

    let row: LicenseUserRow;
    let created = false;
    let baseExpiry: string;

    if (found.length === 0) {
      // 订单比账户先到：先建号给 1 天，再在这 1 天之上叠加
      created = true;
      baseExpiry = bootstrapExpiry(now);
      await client.execute(
        "INSERT INTO license_user " +
          "(typekey, addtime, exptime, status, `user`, email, `count`) " +
          "VALUES (?, ?, ?, 'active', NULL, NULL, 0)",
        [typekey, now, baseExpiry],
      );
      row = {
        typekey,
        addtime: now,
        exptime: baseExpiry,
        status: "active",
        last_time: null,
        user: null,
        email: null,
        count: 0,
      };
    } else {
      row = found[0] as unknown as LicenseUserRow;
      baseExpiry = toBeijingText(row.exptime) ?? now;
    }

    const newExpiry = computeNewExpiry(baseExpiry, now, months, isPermanent);

    // 联系方式：传了非空就覆盖，无论是新建还是续期的关联账户
    applyContact(row, payload.user, payload.email);

    await client.execute(
      "UPDATE license_user SET exptime = ?, `user` = ?, email = ? WHERE typekey = ?",
      [newExpiry, row.user, row.email, typekey],
    );

    const orderTime = String(order.creat_time ?? "");
    try {
      await client.execute(
        "INSERT INTO license_order " +
          "(ordernumber, typekey, ordertime, usetime, money, name, sku) " +
          "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
          orderNo,
          typekey,
          parseBeijing(orderTime) ? orderTime : null,
          now,
          order.total !== undefined && order.total !== null
            ? String(order.total)
            : null,
          orderType || null,
          rawSku || null,
        ],
      );
    } catch (error) {
      // 两个请求同时核销同一单，靠主键冲突兜底
      const message = String(error);
      if (message.includes("Duplicate entry") || message.includes("ER_DUP_ENTRY")) {
        const again = await client.query(
          "SELECT usetime FROM license_order WHERE ordernumber = ?",
          [orderNo],
        );
        const usedAt = again.length > 0
          ? toBeijingText((again[0] as unknown as { usetime: unknown }).usetime) ?? ""
          : "";
        return jsonResponse(
          envelope("ORDER_ALREADY_USED", `订单已于${usedAt}使用过`),
        );
      }
      throw error;
    }

    return jsonResponse(
      envelope("REDEEMED", `订单使用成功，有效期至 ${newExpiry}`, {
        typekey,
        ordernumber: orderNo,
        exptime: newExpiry,
        permanent: isPermanent,
        added_months: isPermanent ? null : months,
        // 本次是否顺带新建了账户
        created,
      }),
    );
  } catch (error) {
    console.error("redeem failed:", error);
    await resetClient();
    return jsonResponse(envelope("SERVER_ERROR", "服务暂时不可用"), 500);
  }
});
