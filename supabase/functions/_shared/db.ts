// MySQL 连接（Supabase Edge Function / Deno 环境直连现有 MySQL）。
//
// 为什么不用 Supabase 自带的 Postgres：这套系统的权威库是 MySQL，
// Supabase 在这里只是「备用业务线路」，必须与主站共用同一份数据。
//
// 为什么用 npm:mysql2 而不是 deno.land/x/mysql：
//   MySQL 8 默认认证插件是 caching_sha2_password，
//   deno.land/x/mysql@v2.12.1 不支持它（表现为 Access denied），
//   而部分环境又无法加载 mysql_native_password 插件。
//   mysql2 完整支持 caching_sha2_password，通过 Deno 的 node: 兼容层可直接使用。
//
// 需要的环境变量（Supabase 后台 Edge Functions → Secrets）：
//   MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE

import mysql from "npm:mysql2@3.24.6/promise";

// 对外暴露的最小接口：三个业务函数只用到 query 与 execute。
export interface DbClient {
  query(sql: string, params?: unknown[]): Promise<unknown[]>;
  execute(sql: string, params?: unknown[]): Promise<unknown>;
  close(): Promise<void>;
}

interface Config {
  host: string;
  port: number;
  user: string;
  password: string;
  database: string;
}

function readConfig(): Config {
  const host = Deno.env.get("MYSQL_HOST") ?? "";
  const user = Deno.env.get("MYSQL_USER") ?? "";
  if (!host || !user) {
    // 配置缺失时给出明确信息，不要让它表现成一句笼统的 Access denied
    throw new Error(
      "MySQL 配置缺失：请检查 Supabase Secrets 里的 MYSQL_HOST / MYSQL_USER 等变量",
    );
  }
  return {
    host,
    port: Number(Deno.env.get("MYSQL_PORT") ?? "3306"),
    user,
    password: Deno.env.get("MYSQL_PASSWORD") ?? "",
    database: Deno.env.get("MYSQL_DATABASE") ?? "license",
  };
}

let _client: DbClient | null = null;

/** 把 mysql2 的连接包装成业务代码使用的 DbClient。 */
function wrap(conn: {
  query: (sql: string, params?: unknown[]) => Promise<[unknown, unknown]>;
  execute: (sql: string, params?: unknown[]) => Promise<[unknown, unknown]>;
  end: () => Promise<void>;
}): DbClient {
  return {
    async query(sql, params) {
      const [rows] = await conn.query(sql, params ?? []);
      // SELECT 拿到数组；其它语句拿到 ResultSetHeader，业务里只在 SELECT 用 query
      return Array.isArray(rows) ? rows : [];
    },
    async execute(sql, params) {
      const [result] = await conn.execute(sql, params ?? []);
      return result;
    },
    async close() {
      await conn.end();
    },
  };
}

/** 取一个已连接的 MySQL 客户端（模块级复用，实例存活期间有效）。 */
export async function getClient(): Promise<DbClient> {
  if (_client) return _client;

  const cfg = readConfig();
  const conn = await mysql.createConnection({
    host: cfg.host,
    port: cfg.port,
    user: cfg.user,
    password: cfg.password,
    database: cfg.database,
    charset: "utf8mb4",
    // 时间和字符集由下面的 SET 语句统一，不依赖驱动默认值
    dateStrings: true,
    supportBigNumbers: true,
    connectTimeout: 10_000,
  });

  // 与主站一致：每条连接建立后统一设置为北京时间。
  // 用 '+08:00' 这种偏移写法，不依赖服务器是否加载了时区表。
  await conn.query("SET time_zone = '+08:00'");

  _client = wrap(conn as never);
  return _client;
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
