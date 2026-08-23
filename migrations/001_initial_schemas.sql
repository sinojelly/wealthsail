-- ==============================================================================
-- WealthSail Migration 001: Initial Schemas & Extensions
-- Version: v2.0.0
-- Description: 创建领域驱动的 PostgreSQL Schemas 与通用扩展
-- ==============================================================================

-- 1. 启用必要扩展 (UUID与密码学哈希)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 2. 创建各业务领域隔离 Schema
CREATE SCHEMA IF NOT EXISTS identity;   -- 租户、用户、账户与身份绑定
CREATE SCHEMA IF NOT EXISTS reference;  -- 标的主数据、交易日历与数据源元数据
CREATE SCHEMA IF NOT EXISTS ledger;     -- 不可变投资事件、成交事实、批次与持仓/净值投影
CREATE SCHEMA IF NOT EXISTS market;     -- 数据源抓取快照、市场指标观察与公司行动
CREATE SCHEMA IF NOT EXISTS research;   -- 投资论点、模型执行记录、建议与证据引用
CREATE SCHEMA IF NOT EXISTS reporting;  -- 四时段报告、条目、用户确认/纠正决策
CREATE SCHEMA IF NOT EXISTS risk;       -- 账户投资政策、风控告警规则与触点事件
CREATE SCHEMA IF NOT EXISTS workflow;   -- 定时任务调度、JobRuns 与事务性 Outbox
CREATE SCHEMA IF NOT EXISTS audit;      -- 统一不可变审计日志
