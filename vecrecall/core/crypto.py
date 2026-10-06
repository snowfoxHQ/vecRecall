"""
VecRecall — 静态加密（at-rest encryption）

记忆内容在磁盘上加密存储，防止数据库文件被直接读取时泄露原文。

实现：
  - 用 `cryptography` 库的 Fernet（AES-128-CBC + HMAC-SHA256 认证加密）。
  - 密钥来源：直接给 Fernet 密钥（urlsafe base64 44 字符），或给密码用
    PBKDF2-HMAC-SHA256 派生（盐持久化在 base_dir/salt.key）。

零依赖原则：
  - `cryptography` 是可选依赖（`pip install vecrecall[crypto]`）。
  - 未安装时实例化 Cipher 会抛出带安装提示的异常，绝不静默降级为明文。
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

__all__ = ["Cipher", "derive_key"]

# Fernet 密文固定以 gAAAAA 开头（0x80 版本字节 + 时间戳的 base64 前缀）
_FERNET_PREFIX = "gAAAAA"


def _fernet():
    try:
        from cryptography.fernet import Fernet
        return Fernet
    except ImportError as e:
        raise RuntimeError(
            "静态加密需要 cryptography 库，请先安装：pip install vecrecall[crypto]"
        ) from e


def _looks_like_fernet_key(s: str) -> bool:
    """判断字符串是否是有效的 Fernet 密钥（urlsafe base64、44 字符、解码 32 字节）。"""
    if len(s) != 44:
        return False
    try:
        return len(base64.urlsafe_b64decode(s.encode("ascii"))) == 32
    except Exception:
        return False


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """从密码 + 盐派生 32 字节 Fernet 密钥（PBKDF2-HMAC-SHA256）。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


class Cipher:
    """对称加密器：encrypt / decrypt，密钥固定、盐内嵌（Fernet 每次加密随机 IV）。"""

    def __init__(self, key_b64: str | bytes):
        # Fernet 接受 urlsafe base64 形式的密钥（内部自行 decode 为 32 字节）
        self._f = _fernet()(key_b64)

    # ── 构造 ────────────────────────────────

    @staticmethod
    def generate_key() -> str:
        """生成一个随机 Fernet 密钥（urlsafe base64 字符串）。"""
        return _fernet().generate_key().decode("ascii")

    @classmethod
    def from_key(cls, key_b64: str) -> "Cipher":
        """从 Fernet 密钥（urlsafe base64 44 字符）构建。"""
        s = key_b64.strip()
        if not _looks_like_fernet_key(s):
            raise ValueError("无效的加密密钥（应为 44 字符 urlsafe base64）")
        return cls(s)

    @classmethod
    def from_passphrase(cls, passphrase: str, salt: bytes) -> "Cipher":
        """从密码 + 盐派生密钥构建。"""
        return cls(derive_key(passphrase, salt))

    @classmethod
    def resolve(cls, secret: str, base_dir: Path) -> "Cipher":
        """从用户提供的秘密材料构建 Cipher。

        - secret 是 44 字符 urlsafe base64 → 当作 Fernet 密钥直接用；
        - 否则当作密码，盐持久化在 base_dir/salt.key（不存在则生成）。
        """
        s = secret.strip()
        if not s:
            raise ValueError("加密密钥/密码不能为空")
        if _looks_like_fernet_key(s):
            return cls.from_key(s)

        salt_path = Path(base_dir) / "salt.key"
        if salt_path.exists():
            salt = salt_path.read_bytes()
        else:
            salt = os.urandom(16)
            salt_path.write_bytes(salt)
        return cls.from_passphrase(s, salt)

    # ── 加解密 ────────────────────────────────

    def encrypt(self, plaintext: str) -> str:
        """加密为 ASCII 安全字符串（Fernet token），可直接存 SQLite TEXT。"""
        return self._f.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        """解密；遇到明文（无 Fernet 前缀）则原样返回，兼容加密前旧数据。"""
        if not token.startswith(_FERNET_PREFIX):
            return token
        return self._f.decrypt(token.encode("ascii")).decode("utf-8")
