"""可选插件：通过 vecrecall.core.hooks 挂载，不改动核心行为。"""

from vecrecall.plugins.trace import TracePlugin, TraceStore, enable_trace

__all__ = ["TracePlugin", "TraceStore", "enable_trace"]
