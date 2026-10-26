-- ============================================================================
-- 02_vehicle_tables.sql — 车联网第二业务域 6 大核心业务表
-- 由 MySQL 容器首次启动 (entrypoint) 或服务启动自愈 (mock_server.app) 自动执行
-- ============================================================================

-- 1. 车辆事件上报（多域事件总线：动力/电池/智驾/座舱/充电/OTA）
CREATE TABLE IF NOT EXISTS vehicle_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    vin VARCHAR(50) NOT NULL,
    domain VARCHAR(20) NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    risk_level VARCHAR(20) NOT NULL,
    payload JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_vehicle_events_vin_time (vin, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 远程指令下发（车端指令状态机: PENDING -> EXECUTED）
CREATE TABLE IF NOT EXISTS vehicle_commands (
    id INT AUTO_INCREMENT PRIMARY KEY,
    vin VARCHAR(50) NOT NULL,
    command_type VARCHAR(50) NOT NULL,
    params JSON,
    status VARCHAR(20) DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_vehicle_commands_vin_status (vin, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. BMS 电池告警（热失控预警链路）
CREATE TABLE IF NOT EXISTS bms_alerts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    vin VARCHAR(50) NOT NULL,
    alert_level VARCHAR(20) NOT NULL,
    highest_temp FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_bms_alerts_vin_time (vin, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. 座舱偏好（VIN 主键 UPSERT 幂等写入）
CREATE TABLE IF NOT EXISTS cabin_settings (
    vin VARCHAR(50) PRIMARY KEY,
    profile_data JSON,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 充电订单（扩展位：充电桩对接与计费链路规划中）
CREATE TABLE IF NOT EXISTS charging_orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    vin VARCHAR(50) NOT NULL,
    station_id VARCHAR(50) NOT NULL,
    kwh DECIMAL(10, 2) NOT NULL DEFAULT 0,
    status VARCHAR(20) DEFAULT 'CHARGING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_charging_orders_vin_time (vin, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6. OTA 升级任务（扩展位：整车 OTA 灰度发布链路规划中）
CREATE TABLE IF NOT EXISTS ota_tasks (
    id INT AUTO_INCREMENT PRIMARY KEY,
    vin VARCHAR(50) NOT NULL,
    target_version VARCHAR(50) NOT NULL,
    status VARCHAR(20) DEFAULT 'QUEUED',
    progress TINYINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ota_tasks_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
