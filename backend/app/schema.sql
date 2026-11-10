-- AI 堡垒机 V1 数据模型

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL,               -- operator | security_admin | auditor
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT NOT NULL DEFAULT '',
    env_tag      TEXT NOT NULL DEFAULT 'test',      -- production | staging | test
    executor     TEXT NOT NULL DEFAULT 'mock',      -- mock | ssh | local
    host         TEXT NOT NULL DEFAULT '',
    port         INTEGER NOT NULL DEFAULT 22,
    ssh_user     TEXT NOT NULL DEFAULT 'root',
    auth_type    TEXT NOT NULL DEFAULT 'password',  -- password | key
    credential_ref TEXT NOT NULL DEFAULT '',        -- 环境变量名 / 私钥路径，绝不存明文口令
    tags         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'online',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT 'rule-based-planner',
    system_prompt TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'enabled',  -- enabled | disabled
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_assets (
    agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    PRIMARY KEY (agent_id, asset_id)
);

CREATE TABLE IF NOT EXISTS agent_tools (
    agent_id  INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    PRIMARY KEY (agent_id, tool_name)
);

CREATE TABLE IF NOT EXISTS user_agents (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, agent_id)
);

CREATE TABLE IF NOT EXISTS user_assets (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, asset_id)
);

CREATE TABLE IF NOT EXISTS tools (
    name        TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    params      TEXT NOT NULL DEFAULT '{}',   -- JSON Schema
    category    TEXT NOT NULL DEFAULT 'read',  -- read | write | destructive | data
    risk_hint   TEXT NOT NULL DEFAULT 'LOW',
    command_template TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS policies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    scope_type  TEXT NOT NULL DEFAULT 'env',   -- env | asset | agent | global
    scope_value TEXT NOT NULL DEFAULT '*',
    priority    INTEGER NOT NULL DEFAULT 100,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_rules (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    tool      TEXT NOT NULL,                  -- 工具名，* 表示全部
    action    TEXT NOT NULL,                  -- ALLOW | APPROVAL | DENY
    reason    TEXT NOT NULL DEFAULT '',
    max_risk  TEXT NOT NULL DEFAULT ''        -- 可选：仅当风险 <= max_risk 时才适用
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    agent_id    INTEGER NOT NULL REFERENCES agents(id),
    asset_id    INTEGER NOT NULL REFERENCES assets(id),
    title       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',  -- active | waiting_approval | finished | terminated | blocked
    risk_level  TEXT NOT NULL DEFAULT 'LOW',
    summary     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    ended_at    TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    role       TEXT NOT NULL,      -- user | assistant | system | tool
    content    TEXT NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    tool         TEXT NOT NULL,
    arguments    TEXT NOT NULL DEFAULT '{}',
    reason       TEXT NOT NULL DEFAULT '',
    risk_level   TEXT NOT NULL DEFAULT 'LOW',
    risk_reasons TEXT NOT NULL DEFAULT '[]',
    decision     TEXT NOT NULL DEFAULT 'PENDING',  -- ALLOW | APPROVAL | DENY
    policy_name  TEXT NOT NULL DEFAULT '',
    command      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | running | success | failed | denied | blocked
    result       TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    finished_at  TEXT,
    duration_ms  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    tool_call_id INTEGER NOT NULL REFERENCES tool_calls(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | expired
    requested_by TEXT NOT NULL DEFAULT 'ai-agent',
    decided_by   TEXT NOT NULL DEFAULT '',
    comment      TEXT NOT NULL DEFAULT '',
    requested_at TEXT NOT NULL,
    decided_at   TEXT,
    expires_at   TEXT
);

CREATE TABLE IF NOT EXISTS risk_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    tool_call_id INTEGER REFERENCES tool_calls(id) ON DELETE CASCADE,
    level      TEXT NOT NULL,
    category   TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    seq        INTEGER NOT NULL DEFAULT 0,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT 'system',
    event_type TEXT NOT NULL,
    payload    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_toolcall_session ON tool_calls(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_risk_created ON risk_events(created_at);

