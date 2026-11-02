// MySQL 连接池（Supabase Edge Function 用 Deno 环境直连现有 MySQL）。
//
// 为什么不用 Supabase 自带的 Postgres：这套系统的权威库是 MySQL，
// Supabase 在这里只是「备用业务线路」，必须与主站共用同一份数据。
// 因此 Edge Function 通过公网 3306 直连同一台 MySQL。
//
// 需要的环境变量（在 Supabase 后台 Edge Functions → Secrets 里配置）：
//   MYSQL_HOST            e.g. 38.246.253.104
//   MYSQL_PORT            e.g. 3306
//   MYSQL_USER            建议专用只读+写的最小权限账号
//   MYSQL_PASSWORD
//   MYSQL_DATABASE        e.g. license

import { Client } from "https://deno.land/x/mysql@v2.12.1/mod.ts";

let _client: Client | null = null;

function env(name: string, fallback = ""): string {
  return Deno.env.get(name) ?? fallback;
}

/** 取一个已连接的 MySQL 客户端（模块级复用，Edge Function 实例存活期间有效）。 */
export async function getClient(): Promise<Client> {
  if (_client) return _client;

  const client = await new Client().connect({
    hostname: env("MYSQL_HOST"),
    port: Number(env("MYSQL_PORT", "3306")),
    username: env("MYSQL_USER"),
    password: env("MYSQL_PASSWORD"),
    db: env("MYSQL_DATABASE", "license"),
    charset: "utf8mb4",
    timeout: 10_000,
  });

  // 与主站一致：每条连接建立后统一设置为北京时间，
  // 这样 CURRENT_TIMESTAMP 之类的库端默认值落下来就是北京墙上时间。
  // MySQL 的 time_zone 用 '+08:00' 这种偏移写法不需要加载时区表。
  await client.execute("SET time_zone = '+08:00'");

  _client = client;
  return client;
}

/** SQL 执行失败时关闭连接，让下次请求重建，避免复用坏死连接。 */
export async function resetClient(): Promise<void> {
  if (!_client) return;
  try {
    await _client.close();
  } catch {
    // 关闭失败无所谓，重新连接即可
  }
  _client = null;
}
