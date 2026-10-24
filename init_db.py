import pymysql

# 车企 6 大核心业务表
table_sqls = [
    """
    CREATE TABLE IF NOT EXISTS vehicle_events (
        id INT AUTO_INCREMENT PRIMARY KEY,
        vin VARCHAR(50) NOT NULL,
        domain VARCHAR(20) NOT NULL,
        event_type VARCHAR(50) NOT NULL,
        risk_level VARCHAR(20) NOT NULL,
        payload JSON,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS vehicle_commands (
        id INT AUTO_INCREMENT PRIMARY KEY,
        vin VARCHAR(50) NOT NULL,
        command_type VARCHAR(50) NOT NULL,
        params JSON,
        status VARCHAR(20) DEFAULT 'PENDING',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bms_alerts (
        id INT AUTO_INCREMENT PRIMARY KEY,
        vin VARCHAR(50) NOT NULL,
        alert_level VARCHAR(20) NOT NULL,
        highest_temp FLOAT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cabin_settings (
        vin VARCHAR(50) PRIMARY KEY,
        profile_data JSON,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )
    """
]

def init_database():
    print("正在连接 Docker MySQL...")
    try:
        conn = pymysql.connect(
            host='127.0.0.1', port=3306, user='qa_user', password='qa_pass', database='automation_shop'
        )
        with conn.cursor() as cursor:
            for i, sql in enumerate(table_sqls, 1):
                cursor.execute(sql)
                print(f"第 {i} 张表同步完成！")
        conn.commit()
        conn.close()
        print("所有车联网基础核心表已全部创建完毕！")
    except Exception as e:
        print(f"数据库连接或执行失败: {e}")

if __name__ == "__main__":
    init_database()