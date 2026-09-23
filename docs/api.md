# 授权管理系统 · 接口契约

基础路径 `/api`，全部 `POST`，请求与响应均为 `application/json; charset=utf-8`。

## 通用约定

**统一响应包**

```json
{ "code": "ACTIVE", "message": "未过期", "data": { } }
```

- `code`：字符串常量，供客户端做逻辑判断。
- `message`：中文提示，直接展示给用户。**沿用你现有的文案**（「申请已通过」「不支持多次试用」「已过期」等），这样客户端不用改。
- `data`：结构化数据，失败时为 `null`。

**时间格式**：`"YYYY-MM-DD HH:MM:SS"`，北京时间，不带时区后缀。与你现在客户端拿到的字符串格式一致。

**字段命名**：与数据库列名保持一致（`typekey` / `ordernumber` / `addtime` / `exptime` / `last_time` / `count`），少一层映射就少一个出错点。

**凭证**：`typekey` 本身即凭证，放在请求体里（也接受 `X-License-Key` 请求头，二选一）。

**`typekey` 格式**：`{产品线前缀}-{账户标识}`，例如 `Pro-8f3a91c2`。**该值由客户端生成**，服务端不生成、也不校验前缀的合法性，只校验整体格式（非空、长度 ≤ 64）。前缀是**动态的**，不固定为某一个值，系统不维护产品线白名单。它唯一的作用是核销时参与比对：商品名必须等于 `{前缀}-Quicker授权`。

**HTTP 状态码**：业务结果一律返回 `200`，靠 `code` 区分；只有服务端自身出错（500）或请求格式非法（400）才用非 200。

---

## 1. 试用申请

给一个新 `typekey` 建账户，有效期 1 天。

```
POST /api/trial
```

```json
{
  "typekey": "Pro-8f3a91c2",
  "user": "张三",
  "email": "a@b.com"
}
```

`user` / `email` 可为空或省略。**空字符串一律按未传处理**，不写进数据库（保持字段干净）。

**响应**

```json
{
  "code": "TRIAL_GRANTED",
  "message": "申请已通过",
  "data": {
    "typekey": "Pro-8f3a91c2",
    "addtime": "2026-03-01 13:05:22",
    "exptime": "2026-03-02 13:05:22"
  }
}
```

| code | message | 说明 |
|---|---|---|
| `TRIAL_GRANTED` | 申请已通过 | 新建账户成功，`exptime = 现在 + 1 天` |
| `TRIAL_ALREADY_USED` | 不支持多次试用 | `typekey` 已存在，**不改动任何字段**，原样返回现有 `addtime` / `exptime` |
| `INVALID_KEY` | 授权标识无效 | `typekey` 为空或超长 |

**逻辑**

1. 校验 `typekey` 非空、长度 ≤ 64
2. 查 `license_user`：
   - 存在 → 直接返回 `TRIAL_ALREADY_USED` + 原有 `addtime` / `exptime`
   - 不存在 → `INSERT`，`addtime` / `exptime` 由数据库默认值与 `现在 + 1 天` 写入，而后返回 `TRIAL_GRANTED`
3. 并发下依赖 `typekey` 主键兜底：插入冲突则回退到第 2 步的「已存在」分支

**试用的唯一规则**：一个 `typekey` 终身只能领一次。首次访问建号并给 1 天，之后再访问一律返回 `TRIAL_ALREADY_USED`，不改动任何字段。不做 IP 限流。

---

## 2. 在线校验

```
POST /api/verify
```

```json
{
  "typekey": "Pro-8f3a91c2",
  "user": "张三",
  "email": "a@b.com"
}
```

`user` / `email` 可选，**仅当传了非空值时才覆盖**数据库里的原值（空值不覆盖、不清空）。

**响应**

```json
{
  "code": "ACTIVE",
  "message": "未过期",
  "data": {
    "typekey": "Pro-8f3a91c2",
    "addtime": "2026-03-01 13:05:22",
    "exptime": "2027-03-01 13:05:22",
    "last_time": "2026-03-02 09:12:00",
    "count": 13
  }
}
```

| code | message | 说明 |
|---|---|---|
| `ACTIVE` | 未过期 | `exptime` 晚于当前北京时间 |
| `EXPIRED` | 已过期 | `exptime` 早于或等于当前北京时间 |
| `NOT_FOUND` | 不存在 | 账户不存在 |
| `INVALID_KEY` | 授权标识无效 | `typekey` 为空或超长 |

**逻辑**

1. 查 `license_user`：不存在 → `NOT_FOUND`，**不写库**
2. 用 `exptime` 与当前北京时间比较 → `ACTIVE` / `EXPIRED`
3. **只有 `ACTIVE` 才写库**：`last_time = 当前北京时间`，`count = count + 1`，`user` / `email` 按非空规则覆盖
4. 返回 `addtime` / `exptime` / `last_time` / `count`

**`EXPIRED` 和 `NOT_FOUND` 一律不碰数据库**——不计数、不更新 `last_time`、也不接受 `user` / `email` 的覆盖。

---

## 3. 订单核销

```
POST /api/redeem
```

### 3.1 客户端请求

```json
{
  "typekey": "Pro-8f3a91c2",
  "ordernumber": "AFD2026021400001"
}
```

客户端**只传订单号**，不传任何订单内容。订单真伪由服务端自己去平台核对。

### 3.2 服务端查单

```
GET {platform_base}/api/order/query?email={商户邮箱}&order_no={ordernumber}
```

平台（爱发电铺）的成功返回：

```json
{
  "ok": true,
  "data": {
    "order_id": "AFD2026021400001",
    "creat_time": "2026-02-14 15:30:00",
    "type": "图标素材",
    "title": "Morph 线性图标包",
    "sku": "SVG全量版",
    "total": 18,
    "seller": "afdian_pixellab_88"
  }
}
```

失败时 HTTP 状态码非 2xx，body 为 `{"ok": false, "message": "…"}`。

这个接口是**公开的、没有鉴权**——凭证就是「商户邮箱 + 订单号」，而且**只返回已支付的订单**。所以下面两项要进 `config.json`：

| 配置 | 值 |
|---|---|
| `platform.base_url` | 测试环境 `https://test.arduey.top`，正式环境换成生产域名 |
| `platform.email` | 你在该平台注册/登录用的**商户邮箱** |

### 3.3 字段映射

| 现有系统 | 平台返回 | 处理 |
|---|---|---|
| `status == "True"` | `ok == true` | 平台只吐已支付订单 |
| `time` | `creat_time` | **付款时间**，写入 `license_order.ordertime` |
| `name` | **`type`** | **就是 `typekey` 的前缀**，用来校验订单归属 |
| `sku` | `sku` | **增加多少有效期**（年 / 月 / 永久） |
| `money`（字符串） | `total`（数字） | 金额，`18` → 存 `"18"` |
| `count` | 无 | 平台不提供数量，一律按 1 份算 |
| `ordernumber` | `order_id` | 订单号 |
| — | `title` / `seller` | **不用管**，忽略 |

### 3.4 响应与错误码

```json
{
  "code": "REDEEMED",
  "message": "订单使用成功，有效期至 2027-03-01 13:05:22",
  "data": {
    "typekey": "Pro-8f3a91c2",
    "ordernumber": "AFD2026021400001",
    "exptime": "2027-03-01 13:05:22",
    "added_months": 12
  }
}
```

| code | message | 触发条件 |
|---|---|---|
| `REDEEMED` | 订单使用成功，有效期至 … | 核销成功 |
| `ORDER_ALREADY_USED` | 订单已于 YYYY-MM-DD HH:MM:SS 使用过 | `ordernumber` 已存在于 `license_order` |
| `ORDER_NOT_FOUND` | 订单不存在 | 平台返回非 2xx，或 `ok != true` |
| `PLATFORM_UNAVAILABLE` | 订单查询失败 | 请求超时 / 网络异常 / 平台 5xx（客户端可重试） |
| `PRODUCT_MISMATCH` | 输入的订单非{type}系列订单 | `title` 不匹配，或 `sku` 解析不出时长 |
| `INVALID_KEY` | 授权标识无效 | `typekey` 为空或超长 |

**逻辑**

1. `type = typekey.split("-")[0]`
2. 查 `license_order`：`ordernumber` 已存在 → `ORDER_ALREADY_USED`，带上已有的 `usetime`
3. 请求 `GET /api/order/query`，超时 5 秒；超时或平台 5xx → `PLATFORM_UNAVAILABLE`
4. 非 2xx 或 `ok != true` → `ORDER_NOT_FOUND`
5. **`data.type` 必须等于 `typekey` 的前缀**（`typekey.split("-")[0]`），否则 `PRODUCT_MISMATCH`。这是订单归属的唯一校验
6. 解析 `sku`：取数字部分为月数；中文部分含「永久」→ `9999` 个月、含「年」→ `数字 × 12`、含「月」→ `数字`；都不含则 `PRODUCT_MISMATCH`
7. 保证 `license_user` 存在：不存在则以 1 天有效期建号
8. 算新到期时间：原 `exptime` 未过期 → 从原值叠加；已过期 → 从现在起算
9. 事务内：`UPDATE license_user.exptime` + `INSERT license_order`（`ordertime` 存 `creat_time`、`money` 存 `total` 的字符串形式、`count` 存 `1`）
10. 返回成功与新到期时间

**并发**：两个请求同时核销同一单，靠 `ordernumber` 主键冲突兜底，后到者转为 `ORDER_ALREADY_USED`（而不是抛 500）。

### 3.5 字段语义（已确认）

| 平台字段 | 含义 | 用途 |
|---|---|---|
| `order_id` | 订单号 | 主键 / 幂等判断 |
| `creat_time` | **付款时间** | 写入 `ordertime` |
| `type` | **`typekey` 的前缀** | 校验订单归属，必须与 `typekey` 前缀一致 |
| `sku` | **增加多少有效期**（年 / 月 / 永久） | 解析出月数，决定加多长 |
| `total` | 金额 | 写入 `money` |
| `title` | — | **忽略** |
| `seller` | — | **忽略** |

平台不提供「数量」，所以**不存在倍数**：一笔订单的 `sku` 决定加多长，就加多长。相应地 `license_order.count` 这一列没有数据来源，一律写 `1`（或者干脆删掉这一列，你定）。

---

## 待定项

| # | 项 | 状态 |
|---|---|---|
| 1 | 发卡平台的查单接口 | **已定**：`GET /api/order/query`，公开接口，参数为「商户邮箱 + 订单号」，只返回已支付订单 |
| 2 | `EXPIRED` 时是否计数 / 更新 `last_time`、`user`、`email` | **已定**：一律不写库 |
| 3 | 产品线前缀的实现方式 | 前缀确认**不固定**；是否维护产品线表待定 |
| 4 | 试用申请的限流 | **已定**：不做 IP 限流，一个 `typekey` 终身只能领一次 |
| 5 | 订单归属校验 | **已定**：平台返回的 `type` 与 `typekey` 前缀比对 |
| 6 | `license_order.count` 列 | 平台无数量来源，写 `1` 或删列，待定 |
| 7 | 平台地址与商户邮箱 | 待提供，进 `config.json` |
