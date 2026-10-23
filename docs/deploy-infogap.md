# 信息差公示处 GitHub Actions 部署指南

## 文件位置

- **工作流源文件**: `.github/workflows-deploy/infogap.yml`
- **目标部署位置**: `.github/workflows/infogap.yml`
- **生成脚本**: `scripts/infogap/generate.py`
- **输出目录**: `site/infogap/`

## 部署步骤（需人工执行一次）

### 1. 启用 Workflows 权限

进入仓库设置：
```
Settings → Actions → General → Workflow permissions
```
勾选：**"Allow GitHub Actions to create and approve pull requests"** 和 **"Allow GitHub Actions to create and approve workflows"**

### 2. 启用 GitHub Pages

```
Settings → Pages → Build and deployment
Source: GitHub Actions
```

### 3. 创建工作流文件

将 `.github/workflows-deploy/infogap.yml` 复制为 `.github/workflows/infogap.yml`：

```bash
cp .github/workflows-deploy/infogap.yml .github/workflows/infogap.yml
git add .github/workflows/infogap.yml
git commit -m "Add infogap GitHub Actions workflow"
git push
```

### 4. 配置 Secrets（可选，用于 LLM 重排序）

```
Settings → Secrets and variables → Actions
```
添加以下任意一项（优先级按顺序）：
- `OPENAI_API_KEY`
- `XAI_API_KEY`
- `OPENROUTER_API_KEY`
- `GOOGLE_API_KEY` / `GOOGLE_GENAI_API_KEY`

不配置时将使用本地评分模型。

## 工作流说明

| 触发方式 | 说明 |
|---|---|
| `schedule: 0 */6 * * *` | 每 6 小时自动运行 |
| `workflow_dispatch` | 手动触发，可指定 topics |

## 产出物

| 文件 | 说明 |
|---|---|
| `site/infogap/index.html` | 交互式报告页 |
| `site/infogap/report.json` | 机器可读结构化数据 |
| `site/infogap/history/YYYY-MM-DD.json` | 历史快照 |

## 本地验证

```bash
# 安装依赖
pip install git+https://github.com/mvanhorn/last30days-skill.git

# 干运行
python scripts/infogap/generate.py --dry-run

# 正式运行（需 API key）
export OPENAI_API_KEY=xxx
python scripts/infogap/generate.py --strict
```

## 故障排查

| 问题 | 解决 |
|---|---|
| `last30days not installed` | 确认 pip install 步骤成功 |
| `Node.js not available` | 确认 setup-node 步骤 |
| `designated coverage 0` | 检查 resources/registry.json 钦定资源映射 |
| Pages 404 | 确认 Pages 部署成功，检查 `site/infogap/` 是否上传 |
