# 授权管理系统

FastAPI + MySQL 的授权服务。客户端凭 `typekey` 在线校验授权；用户购买后凭订单号
核销，服务端向发卡平台核对订单后把有效期叠加到账户上。

部署方式：Debian + 宝塔面板，全程鼠标操作，不需要命令行。详见 [docs/deploy.md](docs/deploy.md)。

## 目录结构

```
main.py                 应用入口（uvicorn main:app）
requirements.txt        全部依赖
config.json             安装向导生成，不进版本库
sql/schema.sql          建表脚本，安装向导执行的就是它
app/
  config.py             config.json 读写
  db.py                 数据库引擎与会话（统一 SET time_zone='+08:00'）
  models.py             ORM 模型（表结构以 schema.sql 为准）
  timeutil.py           北京时间工具
  settings.py           setting 表读写（可在后台改的业务配置）
  platform.py           向发卡平台查单
  security.py           bcrypt 密码哈希 + 登录失败限流
  api.py                /api 三个业务接口
  admin.py              sqladmin 管理后台
  install.py            安装向导
  templates/
    install.html        安装向导页面
docs/
  api.md                接口契约
  admin.md              管理后台设计
  install.md            安装向导设计
  deploy.md             部署文档
```

## 运行

服务器上（宝塔「网站 → Python 项目」）：

```
uvicorn main:app --host 127.0.0.1 --port 8000
```

只监听 `127.0.0.1`，外部流量经宝塔生成的站点进来，8000 端口不对外开放。

本地调试：

```
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## 约定

**时间一律北京时间。** 所有时间列都是 `DATETIME`（不带时区），存北京墙上时间。
不依赖服务器时区——每条数据库连接建立后都会执行一次 `SET time_zone = '+08:00'`。
**不要用 MySQL 的 `TIMESTAMP` 类型**，它会按会话时区自动转 UTC，还有 2038 上限。

**配置分两处。**

| 位置 | 放什么 | 改完要做什么 |
|---|---|---|
| `config.json` | 会话密钥、数据库连接、后台路径、会话时长 | 重启进程 |
| `setting` 表 | 平台地址、商户邮箱、试用天数、站点域名 | 立即生效，在后台改 |

**表结构以 `sql/schema.sql` 为唯一真相。** SQLAlchemy 模型只做映射，不用
`metadata.create_all` 建表。改表结构时两边都要动。

**`typekey` 由客户端生成。** 服务端不生成、不校验前缀合法性，只校验格式
（非空、长度 ≤ 64）。它的前缀在核销时跟平台返回的 `type` 比对。

## 接口

| 接口 | 说明 |
|---|---|
| `POST /api/trial` | 试用申请，一个 `typekey` 终身只能领一次 |
| `POST /api/verify` | 在线校验；只有未过期才计数、才更新 `last_time` |
| `POST /api/redeem` | 订单核销，服务端自己向平台查单 |

统一响应包 `{code, message, data}`，业务结果一律 HTTP 200。详细契约见 [docs/api.md](docs/api.md)。
