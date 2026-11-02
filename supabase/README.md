# Supabase 备用业务线路

主站（FastAPI + MySQL）之外的**备用业务接口**。只提供服务端接口，
不涉及网站页面和管理后台。

```
supabase/functions/
  _shared/
    timeutil.ts     时间工具（与 app/timeutil.py 逐条对齐）
    business.ts     业务规则（与 app/api.py 逐条对齐）
    db.ts           MySQL 连接池
    deno-shim.d.ts  仅本地 tsc 检查用，不部署
  trial/index.ts    POST /api/trial
  verify/index.ts   POST /api/verify
  redeem/index.ts   POST /api/redeem
```

## 核心设计

**两实例共用一个 MySQL。** Edge Function 通过公网 3306 直连主站同一个库，
所以数据只有一份，不存在同步与一致性问题。

为什么不用 Supabase 自带的 Postgres：Supabase 的 HTTP API 与 Edge Functions
都走 Cloudflare，对「连不上 CF」的地区同样不可达；而且这套系统的权威库是
MySQL，迁到 Postgres 意味着重写全部 DDL、SQL 方言和驱动。直连 MySQL 是
「共用一份数据」的唯一轻量解。

## 接口契约

与主站**完全一致**：字段名、`code`、`message`、响应包 `{code, message, data}`、
HTTP 200 语义全部相同。客户端不需要为备用线路做任何区分。

| 接口 | 说明 |
|---|---|
| `POST /functions/v1/trial` | 试用申请 |
| `POST /functions/v1/verify` | 在线校验 |
| `POST /functions/v1/redeem` | 订单核销 |

请求体与主站相同：

```json
{ "typekey": "亚马逊-c19c4d0a...", "user": "张三", "email": "a@b.com" }
```

redeem 额外需要 `ordernumber`。

## 部署

### 1. 配置 Secrets

Supabase 控制台 → Project Settings → Edge Functions → Secrets：

| 变量 | 说明 |
|---|---|
| `MYSQL_HOST` | MySQL 公网地址 |
| `MYSQL_PORT` | 默认 3306 |
| `MYSQL_USER` | 专用账号（见下方安全建议） |
| `MYSQL_PASSWORD` | 该账号密码 |
| `MYSQL_DATABASE` | 库名，如 `license` |

### 2. 部署函数

```bash
npx supabase login
npx supabase link --project-ref <你的项目ref>
npx supabase functions deploy trial verify redeem --no-verify-jwt
```

`--no-verify-jwt` 是必须的：业务接口用 `typekey` 作为凭证，不使用 Supabase 的 JWT。

### 3. 验证

```bash
curl -X POST "https://<ref>.supabase.co/functions/v1/verify" \
  -H "Content-Type: application/json" \
  -d '{"typekey":"亚马逊-c19c4d0a7674d33d3271cf5cb9bbe12e"}'
```

期望返回与主站相同的 `{"code":"ACTIVE",...}`。

## 安全要求（重要）

开公网 3306 是本方案的必要代价，必须做最小化：

1. **专用数据库账号**，只授予这一个库的 `SELECT / INSERT / UPDATE`，
   **不要**给 `DROP / DELETE / ALTER / GRANT`，更不要用 root：

   ```sql
   CREATE USER 'qoa_edge'@'%' IDENTIFIED BY '<强密码>';
   GRANT SELECT, INSERT, UPDATE ON license.* TO 'qoa_edge'@'%';
   FLUSH PRIVILEGES;
   ```

2. **云防火墙白名单**：优先只放行 Supabase 的出口 IP；做不到再退而求其次
   限制来源国家/地区。绝不要对 `0.0.0.0/0` 全开。

3. **强制 TLS**：MySQL 开启 `require_secure_transport=ON`，避免凭证与数据明文过公网。
   开启后需要在上面的连接配置里加 `ssl` 参数。

4. 定期更换 `MYSQL_PASSWORD`。

## 已知限制

- **计数非原子**：`verify` 的 `count = count + 1` 是读-改-写，高并发下可能丢更新。
  与主站行为一致（同样的实现），不是本次引入的问题。
- **连接复用**：连接池在函数实例内存里，实例被回收后重连。冷启动第一个请求会慢一些。
- **不处理后端与页面**：这里只有 3 个业务接口，管理后台仍只在主站提供。
