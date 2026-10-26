# tests/test_security.py — 安全专项测试套件（测开岗位安全测试能力展示）
#
# 覆盖四类安全防线：
#   1. 鉴权缺失/伪造 Token 拦截（越权防护）；
#   2. Webhook 签名篡改拦截（数据完整性）；
#   3. Webhook 时间戳重放攻击拦截（防重放）；
#   4. 批量造数工具越权防护。
import hashlib
import hmac
import time

import requests

from config import settings

BASE_URL = f"{settings.BASE_URL}/api/v1"


def build_signature(order_id: str, amount: float, timestamp: str, secret: str = None) -> str:
    secret = secret or settings.WEBHOOK_SECRET
    message = f"{order_id}|{amount}|{timestamp}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class TestAuthSecurity:
    """鉴权与越权防护"""

    def test_create_order_without_token_blocked(self):
        resp = requests.post(f"{BASE_URL}/orders", json={"item_name": "越权测试", "qty": 1}, timeout=3)
        assert resp.status_code == 401

    def test_create_order_with_forged_token_blocked(self):
        headers = {"Authorization": "Bearer forged_token_666"}
        resp = requests.post(f"{BASE_URL}/orders", json={"item_name": "越权测试", "qty": 1}, headers=headers, timeout=3)
        assert resp.status_code == 401

    def test_vehicle_command_without_token_blocked(self):
        resp = requests.post(
            f"{BASE_URL}/vehicle/commands",
            json={"vin": "LSVAM4187C2184847", "command_type": "REMOTE_UNLOCK"},
            timeout=3,
        )
        assert resp.status_code == 401


class TestWebhookSecurity:
    """支付回调签名与重放防护"""

    def test_tampered_amount_signature_blocked(self):
        """金额被篡改后签名不再匹配，必须 403"""
        order_id = f"SEC_TAMPER_{int(time.time())}"
        ts = str(int(time.time()))
        tampered = build_signature(order_id, 100.0, ts)
        resp = requests.post(
            f"{BASE_URL}/webhook/pay_callback",
            params={"order_id": order_id, "amount": 0.01, "signature": tampered, "timestamp": ts},
            timeout=3,
        )
        assert resp.status_code == 200 and resp.json()["code"] == 403

    def test_wrong_secret_signature_blocked(self):
        """使用错误密钥伪造签名，必须 403"""
        order_id = f"SEC_KEYS_{int(time.time())}"
        ts = str(int(time.time()))
        forged = build_signature(order_id, 10.0, ts, secret="attacker_guessed_secret")
        resp = requests.post(
            f"{BASE_URL}/webhook/pay_callback",
            params={"order_id": order_id, "amount": 10.0, "signature": forged, "timestamp": ts},
            timeout=3,
        )
        assert resp.status_code == 200 and resp.json()["code"] == 403

    def test_expired_timestamp_replay_blocked(self):
        """一小时前的历史回调重放，必须 403"""
        order_id = f"SEC_REPLAY_{int(time.time())}"
        old_ts = str(int(time.time()) - 3600)
        valid_old_sign = build_signature(order_id, 10.0, old_ts)
        resp = requests.post(
            f"{BASE_URL}/webhook/pay_callback",
            params={"order_id": order_id, "amount": 10.0, "signature": valid_old_sign, "timestamp": old_ts},
            timeout=3,
        )
        assert resp.status_code == 200 and resp.json()["code"] == 403

    def test_duplicate_callback_idempotency_blocked(self):
        """同签名同时间戳的重复回调：第一次受理，第二次幂等拦截 403"""
        requests.post(f"{BASE_URL}/tools/reset-test-state", timeout=3)
        order_id = f"SEC_DUP_{int(time.time())}"
        ts = str(int(time.time()))
        sign = build_signature(order_id, 50.0, ts)
        params = {"order_id": order_id, "amount": 50.0, "signature": sign, "timestamp": ts}

        first = requests.post(f"{BASE_URL}/webhook/pay_callback", params=params, timeout=3)
        assert first.json()["code"] == 200, f"首次合法回调应受理: {first.json()}"

        second = requests.post(f"{BASE_URL}/webhook/pay_callback", params=params, timeout=3)
        assert second.json()["code"] == 403, f"重复回调应被幂等拦截: {second.json()}"


class TestParameterInjection:
    """参数注入与边界防护"""

    def test_sql_injection_in_item_name_is_harmless(self, auth_session, db_connection, cleanup_order_db):
        """item_name 携带 SQL 注入串：参数化查询保证只被当作普通字符串处理"""
        payload = {"item_name": "QA_AUTO_' OR '1'='1'; DROP TABLE orders;--", "qty": 1}
        resp = auth_session.post(f"{BASE_URL}/orders", json=payload, timeout=3)
        assert resp.status_code == 200, "合法字符串商品名应正常下单（参数化查询防注入）"

        # 白盒佐证：orders 表依然健在，注入串被当作普通商品名落库
        with db_connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS cnt FROM orders")
            cnt = cursor.fetchone()["cnt"]
        assert cnt > 0, "orders 表被删除了？SQL 注入穿透了参数化查询！"
        print("\n[安全验证通过] SQL 注入串被参数化查询无害化处理，数据表完好")
