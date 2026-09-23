-- ============================================================
-- 授权管理系统 · MySQL 8.0 表结构
--
-- 全局口径：
--   1) 时间一律北京时间。
--      由连接初始化统一执行 SET time_zone = '+08:00' 保证，
--      不使用 MySQL 的 TIMESTAMP 类型（它会按 UTC 自动转换且有 2038 上限），
--      一律用 DATETIME 存北京墙上时间。
--   2) 除主键 / 唯一键外，除非明确要求非空，一律允许为 NULL。
--   3) 字符集 utf8mb4，引擎 InnoDB。
-- ============================================================

SET NAMES utf8mb4;

-- ------------------------------------------------------------
-- 授权账户表
-- 一行 = 一个被授权的账户，typekey 即客户端持有的标识
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `license_user` (
  `typekey`   VARCHAR(64)  NOT NULL                       COMMENT '账户唯一标识，客户端持有',
  `addtime`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `exptime`   DATETIME     NOT NULL                       COMMENT '过期时间',
  `status`    VARCHAR(16)  DEFAULT 'active'               COMMENT '账户状态：active / disabled',
  `last_time` DATETIME                                    COMMENT '上次使用时间',
  `user`      VARCHAR(64)                                 COMMENT '用户名',
  `email`     VARCHAR(128)                                COMMENT '用户邮箱',
  `count`     BIGINT       DEFAULT 0                      COMMENT '使用次数',
  PRIMARY KEY (`typekey`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '授权账户表';


-- ------------------------------------------------------------
-- 订单核销记录表
-- 一行 = 一次成功的订单核销；ordernumber 唯一，天然幂等
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `license_order` (
  `ordernumber` VARCHAR(64)  NOT NULL                       COMMENT '订单号，来自第三方发卡平台',
  `typekey`     VARCHAR(64)  NOT NULL                       COMMENT '核销到的账户标识',
  `ordertime`   DATETIME                                    COMMENT '订单时间（平台返回）',
  `usetime`     DATETIME     DEFAULT CURRENT_TIMESTAMP      COMMENT '核销时间',
  `money`       VARCHAR(32)                                 COMMENT '金额，保留平台原文',
  `name`        VARCHAR(255)                                COMMENT '商品名称，平台原文',
  `sku`         VARCHAR(255)                                COMMENT '规格原文，如 12个月',
  `count`       INT                                         COMMENT '数量',
  PRIMARY KEY (`ordernumber`),
  KEY `idx_typekey` (`typekey`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '订单核销记录表';


-- ------------------------------------------------------------
-- 管理后台账号表
-- 仅用于 /admin 后台登录，与业务侧的 license_user 完全隔离
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `admin_user` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT           COMMENT '自增主键',
  `username`      VARCHAR(64)  NOT NULL                          COMMENT '登录名',
  `password_hash` VARCHAR(255) NOT NULL                          COMMENT '密码哈希（bcrypt）',
  `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `last_login`    DATETIME                                       COMMENT '上次登录时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_username` (`username`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '管理后台账号表';


-- ------------------------------------------------------------
-- 站点设置表
-- 键值对形式，存「装完还能随时改」的业务配置
-- （平台地址、商户邮箱、试用天数等）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `setting` (
  `skey`       VARCHAR(64)  NOT NULL                            COMMENT '配置项名',
  `svalue`     TEXT                                             COMMENT '配置值',
  `remark`     VARCHAR(255)                                     COMMENT '说明',
  `updatetime` DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`skey`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '站点设置表';
