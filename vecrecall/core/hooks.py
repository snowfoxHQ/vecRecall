"""扩展钩子：让外部扩展包（如企业版）在不修改核心代码的前提下挂载行为。

核心只定义调用点，不包含任何授权、合规或业务逻辑。默认没有注册任何钩子，
行为与未引入钩子时完全一致。

四个调用点：
  before_write(fields)        写入前。fields 是将要写入的字段 dict
                              （content / topic / wing / owner / visibility /
                              memory_type / retention_policy / topic_key / metadata ...）。
                              可原地修改或返回新 dict；抛异常即拒绝写入。
  after_write(memory, merged) 写入后（含去重合并，merged=True）。用于审计 sink、同步等。
  recall_filter(memories, viewer)
                              检索结果返回前。接收 Memory 列表，返回允许返回的子集。
                              用于授权、租户隔离等；只能删减，不应新增。
  on_delete(memory_id)        删除后。

注册方式：
  palace.hooks.register("before_write", fn)
  或实现带 ``register(registry)`` 方法的插件对象：palace.hooks.install(plugin)
  或通过 entry point 自动发现（组名 ``vecrecall.plugins``），需显式
  ``VecRecall(load_plugins=True)``。
"""

from __future__ import annotations

from typing import Callable

HOOK_POINTS = ("before_write", "after_write", "recall_filter", "on_delete")
ENTRY_POINT_GROUP = "vecrecall.plugins"


class HookRegistry:
    """按调用点保存回调，按注册顺序执行。"""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable]] = {p: [] for p in HOOK_POINTS}

    def register(self, point: str, fn: Callable) -> Callable:
        if point not in self._hooks:
            raise ValueError(f"未知钩子: {point}（可用: {', '.join(HOOK_POINTS)}）")
        self._hooks[point].append(fn)
        return fn

    def unregister(self, point: str, fn: Callable) -> None:
        if fn in self._hooks.get(point, []):
            self._hooks[point].remove(fn)

    def install(self, plugin) -> None:
        """安装插件对象：插件需实现 register(registry)。"""
        register = getattr(plugin, "register", None)
        if not callable(register):
            raise TypeError("插件必须实现 register(registry) 方法")
        register(self)

    def has(self, point: str) -> bool:
        return bool(self._hooks.get(point))

    # ── 调用点 ──

    def run_before_write(self, fields: dict) -> dict:
        for fn in self._hooks["before_write"]:
            out = fn(fields)
            if out is not None:
                fields = out
        return fields

    def run_after_write(self, memory, merged: bool = False) -> None:
        for fn in self._hooks["after_write"]:
            fn(memory, merged)

    def run_recall_filter(self, memories: list, viewer: str | None) -> list:
        for fn in self._hooks["recall_filter"]:
            allowed_ids = {m.id for m in memories}
            out = fn(list(memories), viewer)
            # 只允许删减：过滤器返回的新记忆一律丢弃
            memories = [m for m in (out or []) if m.id in allowed_ids]
        return memories

    def run_on_delete(self, memory_id: str) -> None:
        for fn in self._hooks["on_delete"]:
            fn(memory_id)


def load_entry_point_plugins(registry: HookRegistry) -> list[str]:
    """安装通过 ``vecrecall.plugins`` entry point 注册的插件，返回已加载的名字。"""
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return []
    eps = entry_points()
    group = eps.select(group=ENTRY_POINT_GROUP) if hasattr(eps, "select") \
        else eps.get(ENTRY_POINT_GROUP, [])
    loaded = []
    for ep in group:
        obj = ep.load()
        plugin = obj() if isinstance(obj, type) else obj
        registry.install(plugin)
        loaded.append(ep.name)
    return loaded
