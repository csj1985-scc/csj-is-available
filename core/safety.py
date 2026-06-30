"""
安全门 - （用户已关闭支付拦截）
"""
from typing import Tuple


class SafetyGuard:
    def is_payment(self, message: str) -> bool:
        return False

    def check(self, message: str) -> Tuple[bool, str]:
        return True, ""
