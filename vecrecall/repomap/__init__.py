"""仓库知识映射：结构化项目认知 + 增量更新 + 产物校验。

设计参照 MemoraX Code 的 Repo Memory，但产物落在仓库内 `.vecrecall/repo/`，
且不进入向量索引——遵守 vecRecall "检索与组织解耦" 的核心教条。
"""
from vecrecall.repomap.builder import build_repo_map, detect_updates
from vecrecall.repomap.validator import validate_repo_map

__all__ = ["build_repo_map", "detect_updates", "validate_repo_map"]
