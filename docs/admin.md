# 管理后台设计

## 选型：sqladmin

**理由**

- 零前端代码，不用装 Node，不需要构建步骤 —— 和一键部署的要求一致
- 把 SQLAlchemy 模型注册进去就有完整的增删改查、搜索、分页、排序、筛选、CSV 导出
- 自带 `AuthenticationBackend` 做登录，不用自己写会话逻辑
- 纯 Python 依赖，`requirements.txt` 装完即用

**代价**

- 界面默认是英文的（可以用它的 `translations` 机制换成中文，需要写一份词条映射）
- 它是「模型级」的表格页，不是「任意表任意字段」的通用数据库浏览器。将来加表就要多注册一个视图
- 改数据是直接改值（比如把 `exptime` 改成某个时间），没有「在现有基础上加 30 天」这种业务操作。如果后面需要，再单独加一个自定义页面

## 身份体系：两套，互不相通

| | 业务侧 | 后台侧 |
|---|---|---|
| 表 | `license_user` | `admin_user` |
| 凭证 | `typekey`（客户端持有） | 用户名 + 密码（人登录） |
| 路径 | `/api/*` | `/admin/*` |
| 用途 | 试用、校验、核销 | 查改数据 |

`admin_user` 表已经加进 `sql/schema.sql`。

**密码哈希用 bcrypt**。注意一个坑：`passlib` 1.7.4 和 `bcrypt` 4.x 存在版本兼容警告（会往日志里刷错误信息，虽然能跑）。建议直接在 `bcrypt` 库上调用，或者用 `pwdlib`，别绕 `passlib`。

## 视图

| 视图 | 展示列 | 可搜索 | 可排序 |
|---|---|---|---|
| **账户** `license_user` | `typekey` `user` `email` `exptime` `last_time` `count` `addtime` | `typekey` `user` `email` | `addtime` `exptime` `last_time` `count` |
| **订单** `license_order` | `ordernumber` `typekey` `name` `sku` `money` `count` `ordertime` `usetime` | `ordernumber` `typekey` `name` | `ordertime` `usetime` |
| **管理员** `admin_user` | `id` `username` `created_at` `last_login` | `username` | `created_at` |
| **站点设置** `setting` | `skey` `svalue` `remark` `updatetime` | `skey` | `updatetime` |

`password_hash` 绝不出现在列表页；新增管理员时用表单输入明文、后端哈希后落库。

四个视图都开放增删改（`can_create` / `can_edit` / `can_delete`）。CSV 导出建议开着——排查问题时很有用。

**站点设置**这张是重点：平台地址、商户邮箱、试用天数这些业务配置都存这里，改完**即时生效**——应用内存里有一份缓存，后台保存时同步刷新，**不需要重启进程**。

## 安全

因为管理后台要「随时随地能打开」，它就是公网可达的，这几条是必须项：

1. **HTTPS 必须开**（宝塔域名管理的 SSL）。否则密码在公网上明文跑。
2. **登录限流**：同一 IP 15 分钟内失败 5 次即锁定，锁定时长递增。做在应用层（用 `slowapi` 或自己写中间件）——不依赖 Nginx 配置，这样宝塔那边改了什么都不会让这层失效。
3. **会话 Cookie**：`HttpOnly` + `Secure` + `SameSite=Lax`。`SameSite=Lax` 顺便挡掉大部分 CSRF。
4. **会话密钥**：由安装向导页随机生成，写进 `config.json`，用来签名会话 Cookie。**这个值不能是硬编码默认值**。
5. **会话过期**：默认 8 小时不活动即失效。
6. **后台路径可配置**：默认 `/admin`，但允许在配置里改成别的（比如 `/x7k2`），能挡掉一批扫路径的机器人。
7. **可选 IP 白名单**：配置项，默认关闭。你固定办公网络时打开，安全等级立刻上一个台阶。

## 实现要点

**依赖**

```
fastapi
uvicorn[standard]
sqlalchemy[asyncio]>=2.0
aiomysql          # 纯 Python 驱动，Debian 上不需要编译，比 asyncmy 稳妥
sqladmin
bcrypt
itsdangerous      # SessionMiddleware 的签名
slowapi           # 限流（可选）
```

**挂载方式**：业务接口挂 `/api`，sqladmin 挂 `/admin`，两者用各自的鉴权；`admin_user` 的密码校验和 `license_user` 的 `typekey` 校验没有任何共用代码。

**建表归属**：`sql/schema.sql` 是唯一的表结构真相，安装向导页执行它。SQLAlchemy 模型只是映射，**不用** `metadata.create_all` 建表。代价是改表结构时两边都要动，好处是建表这件事始终有一份看得见、可审计、能手动导入的 SQL。这个项目规模下不建议引入 Alembic。

## 待你确认

| # | 项 | 默认 |
|---|---|---|
| 1 | 后台路径 | `/admin` |
| 2 | 界面语言 | 英文（要不要我做中文词条） |
| 3 | 登录失败锁定阈值 | 15 分钟 5 次 |
| 4 | 会话有效期 | 8 小时 |
| 5 | IP 白名单 | 关闭 |
