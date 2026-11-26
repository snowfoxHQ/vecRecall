"""pytest 适配：tests/ 下的脚本既能 `python tests/test_x.py` 单独跑，也能 `python -m pytest`。

每个测试文件都有自己的 test(name, cond, detail) 检查函数，失败时只计数、不抛异常。
这里在每个 test_* 用例执行期间替换掉模块里的 test()：用例里有任何一项检查失败，
就让这个用例在调用阶段失败（报告为 failed，而不是 teardown error），并列出失败的检查名和细节。
检查函数本身叫 test，pyproject 里把收集规则设为 test_*，它不会被当成用例。
"""

from __future__ import annotations

import pytest


@pytest.hookimpl(wrapper=True)
def pytest_pyfunc_call(pyfuncitem):
    mod = pyfuncitem.module
    orig = getattr(mod, "test", None)
    if not callable(orig):
        return (yield)

    failed: list[str] = []

    def wrapped(name, cond, *args, **kwargs):
        if not cond:
            detail = args[0] if args else kwargs.get("detail", "")
            failed.append(f"{name}  {detail}".rstrip())
        return orig(name, cond, *args, **kwargs)

    mod.test = wrapped
    try:
        result = yield
    finally:
        mod.test = orig
    if failed:
        pytest.fail(f"{len(failed)} 项检查失败：\n  " + "\n  ".join(failed), pytrace=False)
    return result
