# 发版流程

发布到 PyPI 由 GitHub Actions 自动完成（`.github/workflows/publish.yml`），使用 PyPI Trusted Publishing，不需要在本地或仓库里保存 API token。

## 一次性设置

1. 打开 https://pypi.org/manage/project/vecrecall/settings/publishing/
2. 在 **Add a new publisher** 中选 **GitHub**，填写：
   - Owner：`snowfoxHQ`
   - Repository name：`vecRecall`
   - Workflow name：`publish.yml`
   - Environment name：`pypi`
3. 在 GitHub 仓库 **Settings → Environments** 新建名为 `pypi` 的环境。建议在 **Deployment protection rules** 里勾选 **Required reviewers** 并加上自己，这样每次发布都要手动点一次批准。

## 每次发版

1. 三处版本号保持一致：`pyproject.toml`、`vecrecall/__init__.py`、`vecrecall/api/server.py` 与 `vecrecall/mcp/server.py` 中的版本字符串。
2. 更新 `CHANGELOG.md`。
3. 确认 CI 在 `main` 上是绿的。
4. 打标签并推送：

   ```bash
   git checkout main
   git pull
   git log --oneline -1          # 确认是要发布的提交
   git tag -a v2.6.1 -m "v2.6.1"
   git push origin v2.6.1
   ```

5. 在 GitHub **Actions** 页面查看 *Publish to PyPI*；如果设置了 Required reviewers，点 **Review deployments**，勾选 `pypi`，**评论框留空**，再点 **Approve and deploy**。评论里贴长段 Markdown 会导致 “There was a problem approving one of the gates”（v2.6.0 遇到过）。

工作流会先检查标签与 `pyproject.toml`、`vecrecall/__init__.py` 中的版本号是否一致，不一致直接失败，不会上传。

## 注意

- PyPI 上同一个版本号只能上传一次，发现问题只能升版本号重新发布。
- 打标签前先 `git pull`，确认本地 `main` 和 `origin/main` 一致，否则标签可能打在旧提交上。
- 不要手动 `twine upload` 与自动发布混用同一个版本号。
