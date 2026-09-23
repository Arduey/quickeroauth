# 安装向导页设计

## 一句话

整个「一键部署」里，人唯一需要动手的地方就是这个页面。宝塔负责让进程跑起来，剩下的——数据库、建表、管理员、站点设置——全在这里用浏览器点完。

## 触发与锁定

| 状态 | 判定 | 行为 |
|---|---|---|
| 未安装 | `config.json` 不存在 | 除 `/install` 和静态资源外，所有请求 `302 → /install` |
| 已安装 | `config.json` 存在 | `/install` 一律拒绝，提示「已安装」 |

`config.json` 的存在本身就是安装锁。

## 向导步骤

### 1 · 环境自检

- Python 版本 ≥ 3.10
- 项目目录可写（要落 `config.json`）
- 依赖可导入：`fastapi` / `sqlalchemy` / `aiomysql` / `bcrypt` / `itsdangerous` / `jinja2` / `httpx`
- 不通过就**逐条列出缺什么**，而不是抛一个 traceback

### 2 · 数据库

- 表单：主机（默认 `127.0.0.1`）、端口（`3306`）、用户名、密码、库名（默认 `license`）
- 「测试连接」按钮 → 真的连一次，成功后回显 MySQL 版本号
- 失败要区分三种情况：连不上端口 / 账号密码错 / 库不存在
- 勾选「库不存在时自动创建」→ `CREATE DATABASE IF NOT EXISTS ... CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci`
- **这一步只校验，不落盘**

### 3 · 初始化表结构

- 页面展示 `sql/schema.sql` 的完整内容（透明，看得见要执行什么）
- 「执行」→ 按顺序执行
- 全是 `CREATE TABLE IF NOT EXISTS`，重复执行安全
- 如果检测到 `license_user` 已有数据，明确提示「检测到已有数据，本次不会清空」

### 4 · 管理员账号

- 用户名、密码、确认密码
- 密码长度 ≥ 8，两次必须一致
- 写入 `admin_user`，`password_hash` 用 bcrypt

### 5 · 站点设置

配置分两处存，分界线是「改完要不要重启进程」：

**写进 `config.json`（启动必需，改完要重启）**
- 后台路径（默认 `/admin`）—— 路由前缀，进程启动时就注册好了
- 会话有效期（默认 8 小时）

**写进数据库 `setting` 表（装完可在管理后台随时改，即时生效）**
- 站点域名
- 默认试用天数（默认 `1`）
- 平台地址（爱发电铺，测试 `https://test.arduey.top`）
- 商户邮箱（在该平台注册/登录用的邮箱，查单要用）

### 6 · 完成

- 生成 `config.json`，文件权限 `600`
- 显示后台地址，并提示「回宝塔确认 SSL 已开启」
- 之后 `/install` 自动失效

## config.json

```json
{
  "installed_at": "2026-03-01 13:05:22",
  "secret_key": "<secrets.token_urlsafe(48)>",
  "database": {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "license",
    "password": "***",
    "database": "license"
  },
  "app": {
    "admin_path": "/admin",
    "session_hours": 8
  }
}
```

其余业务配置（**平台地址、商户邮箱、试用天数、站点域名**）存在数据库 `setting` 表里，不在这里——这样它们在管理后台就能直接改，不用碰文件、也不用重启进程。

`secret_key` 用于会话签名，**绝不允许有硬编码默认值**，必须在这里随机生成。

## 实现要点

- FastAPI + Jinja2 服务端渲染，**不引入任何前端框架、不引任何 CDN**（内网环境也能用）
- 每步已填的内容暂存内存（安装是单人的一次性操作，不需要持久化，也不需要处理并发）
- 表单走普通 POST + 服务端校验，不做 AJAX，减少出错面
- 安装期间用临时数据库连接，不预先建连接池（未安装时根本不知道连哪）
- 所有错误都要翻译成人话：`Access denied for user` 要变成「数据库用户名或密码不对」
