# GitHub Actions 部署指南

本文档指导你如何将股票策略回测系统部署到 GitHub Actions 实现自动定时运行。

## ⚠️ 重要安全提示

**Token 安全**：本项目已移除所有硬编码的 Token，必须通过环境变量设置。请勿将 Token 提交到 Git！

## 目录

1. [快速开始](#快速开始)
2. [详细步骤](#详细步骤)
3. [本地开发配置](#本地开发配置)
4. [配置说明](#配置说明)
5. [查看结果](#查看结果)
6. [常见问题](#常见问题)
7. [Token 安全指南](#token-安全指南)

---

## 快速开始

### 步骤概览

```
1. 创建 GitHub 仓库并上传代码
2. 设置 Tushare API Token
3. 等待自动执行或手动触发
4. 下载执行结果
```

---

## 详细步骤

### 第一步：创建 GitHub 仓库

1. 访问 [GitHub](https://github.com) 并登录
2. 点击右上角 `+` → `New repository`
3. 填写仓库信息：
   - Repository name: `stock-strategy`（或其他名称）
   - Description: `股票策略回测系统`
   - 选择 `Private`（私有仓库，保护你的代码）
   - 勾选 `Add a README file`
4. 点击 `Create repository`

### 第二步：配置本地环境（重要！）

在推送代码之前，必须先配置本地环境变量：

#### 1. 创建 .env 文件

在项目根目录创建 `.env` 文件：

```bash
# Windows PowerShell
New-Item .env -ItemType File

# 或者使用记事本
notepad .env
```

文件内容：
```
# Tushare API Token（必填）
TUSHARE_TOKEN=你的实际token

# 可选配置
DB_PATH=stock_strategy.db
CACHE_DIR=data_cache
```

⚠️ **注意**：`.env` 文件已添加到 `.gitignore`，不会被提交到 Git。

#### 2. 验证配置

```bash
python -c "from config import APIConfig; print(APIConfig.get_token())"
```

如果显示你的 Token，说明配置成功。

### 第三步：上传代码到 GitHub

#### 方式A：使用 Git 命令行

```bash
# 进入项目目录
cd c:\Users\zxlin\Desktop\大富翁\wbzq

# 初始化 Git 仓库
git init

# 添加所有文件（.env 会被自动忽略）
git add .

# 提交
git commit -m "Initial commit: 股票策略回测系统"

# 添加远程仓库（替换为你的仓库地址）
git remote add origin https://github.com/你的用户名/stock-strategy.git

# 推送代码
git branch -M main
git push -u origin main
```

#### 方式B：使用 GitHub Desktop（推荐新手）

1. 下载 [GitHub Desktop](https://desktop.github.com/)
2. 登录你的 GitHub 账号
3. `File` → `Add local repository`
4. 选择 `c:\Users\zxlin\Desktop\大富翁\wbzq` 目录
5. 填写提交信息后点击 `Publish repository`

⚠️ **确认 .env 文件未被选中提交**

### 第四步：设置 GitHub Secrets（重要！）

**必须设置 Tushare API Token，否则无法获取股票数据**

1. 进入你的 GitHub 仓库页面
2. 点击 `Settings` 标签
3. 左侧菜单选择 `Secrets and variables` → `Actions`
4. 点击 `New repository secret`
5. 填写：
   - Name: `TUSHARE_TOKEN`
   - Secret: 你的 Tushare API Token
6. 点击 `Add secret`

![设置 Secrets 示意图](https://docs.github.com/assets/cb-11477/images/repository-secrets.png)

### 第五步：验证配置

1. 进入仓库的 `Actions` 标签
2. 你应该看到 `股票策略定时执行` 工作流
3. 点击 `Run workflow` → 选择分支 → 点击 `Run workflow` 手动触发测试

---

## 配置说明

### 定时执行时间

当前配置：
```yaml
schedule:
  - cron: '30 7 * * 1-5'
```

含义：
- `30 7` = UTC 时间 07:30（北京时间 15:30，夏令时）
- `* *` = 每月每天
- `1-5` = 周一到周五

**修改执行时间**：

编辑 `.github/workflows/stock-strategy.yml` 文件：

```yaml
# 北京时间 9:00 执行（UTC 01:00）
- cron: '0 1 * * 1-5'

# 北京时间 15:00 执行（UTC 07:00）
- cron: '0 7 * * 1-5'

# 每天执行一次
- cron: '0 7 * * *'
```

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `TUSHARE_TOKEN` | Tushare API Token | 必须设置 |
| `DB_PATH` | 数据库文件路径 | `stock_strategy.db` |
| `CACHE_DIR` | 缓存目录 | `data_cache` |

### 执行参数

在 `.github/workflows/stock-strategy.yml` 中修改：

```yaml
# 当前配置
python main_par2.py --date ${{ steps.date.outputs.today }} --backtest --hold-days 3

# 可选参数：
# --date YYYYMMDD    指定回测日期
# --days N           历史数据天数（默认60）
# --backtest         执行回测
# --hold-days N      持有天数（默认3）
# --detailed         打印详细持仓数据
```

---

## 查看结果

### 方式一：GitHub 网页下载

1. 进入仓库的 `Actions` 标签
2. 点击最新的工作流运行记录
3. 滚动到底部的 `Artifacts` 区域
4. 下载 `strategy-results-YYYYMMDD` 文件

### 方式二：自动发送到邮箱/钉钉（高级）

可以配置通知功能，将结果自动发送：

```yaml
# 在 workflow 中添加
- name: 发送邮件通知
  uses: dawidd6/action-send-mail@v3
  with:
    server_address: smtp.gmail.com
    username: ${{ secrets.EMAIL_USERNAME }}
    password: ${{ secrets.EMAIL_PASSWORD }}
    subject: 股票策略执行结果 ${{ steps.date.outputs.today }}
    to: your-email@example.com
    attachments: result.csv
```

---

## 常见问题

### Q1: 工作流运行失败，提示 "TUSHARE_TOKEN not found"

**原因**：没有设置 Secrets

**解决**：
1. 检查 Settings → Secrets → Actions 中是否有 `TUSHARE_TOKEN`
2. 确认 Token 值正确且没有多余空格
3. 重新运行工作流

### Q2: 定时任务没有按时执行

**原因**：
- GitHub Actions 的定时任务可能有 5-15 分钟延迟
- 仓库 60 天无活动会被暂停

**解决**：
1. 手动触发测试是否正常
2. 定期推送代码保持仓库活跃

### Q3: 如何查看执行日志？

1. 进入 Actions 标签
2. 点击工作流运行记录
3. 点击 `run-strategy` 任务
4. 展开各个步骤查看详细日志

### Q4: 结果文件在哪里？

每次运行后，结果文件会自动打包为 Artifact：
- 文件名：`strategy-results-YYYYMMDD`
- 包含：`result.csv`, `*.csv`, `*.html`, `stock_strategy.db`
- 保留时间：30天

### Q5: 如何修改 Python 版本？

编辑 `.github/workflows/stock-strategy.yml`：

```yaml
- name: 设置Python环境
  uses: actions/setup-python@v5
  with:
    python-version: '3.11'  # 修改为 3.11 或其他版本
```

### Q6: 免费额度有限制吗？

GitHub Actions 免费额度：
- 公共仓库：无限使用
- 私有仓库：每月 2000 分钟

你的策略每天运行一次，每次约 5-10 分钟，完全够用。

---

## 进阶配置

### 添加钉钉通知

```yaml
- name: 发送钉钉通知
  if: always()
  run: |
    curl -X POST \
      -H "Content-Type: application/json" \
      -d '{
        "msgtype": "text",
        "text": {
          "content": "股票策略执行完成，状态：${{ job.status }}"
        }
      }' \
      ${{ secrets.DINGTALK_WEBHOOK }}
```

### 只在工作日执行（A股交易日）

```yaml
schedule:
  - cron: '30 7 * * 1-5'  # 周一到周五
```

### 保存结果到 GitHub Releases

```yaml
- name: 创建 Release
  uses: softprops/action-gh-release@v1
  with:
    tag_name: result-${{ steps.date.outputs.today }}
    files: result.csv
```

---

## 需要帮助？

1. 查看 GitHub Actions 文档：[docs.github.com/actions](https://docs.github.com/actions)
2. 检查工作流运行日志中的错误信息
3. 在本地先测试代码是否能正常运行

---

## Token 安全指南

### 为什么 Token 安全很重要？

Tushare Token 是你的身份凭证，如果泄露：
- 他人可以使用你的 API 额度
- 可能导致账号被封禁
- 数据被恶意获取

### 本项目如何保护 Token？

1. **无硬编码 Token**：代码中不再包含任何默认 Token
2. **环境变量读取**：必须从环境变量或 `.env` 文件读取
3. **.gitignore 保护**：`.env` 文件已添加到忽略列表
4. **错误提示**：未设置 Token 时会给出明确的错误提示

### 本地开发配置

#### 方式1：使用 .env 文件（推荐）

```bash
# 创建 .env 文件
echo "TUSHARE_TOKEN=你的token" > .env

# 验证
python -c "from config import APIConfig; print('配置成功')"
```

#### 方式2：系统环境变量

**Windows:**
```powershell
# 临时设置（当前窗口有效）
$env:TUSHARE_TOKEN="你的token"

# 永久设置
[Environment]::SetEnvironmentVariable("TUSHARE_TOKEN", "你的token", "User")
```

**Linux/Mac:**
```bash
# 临时设置
export TUSHARE_TOKEN="你的token"

# 永久设置（添加到 ~/.bashrc 或 ~/.zshrc）
echo 'export TUSHARE_TOKEN="你的token"' >> ~/.bashrc
source ~/.bashrc
```

### 如果 Token 已泄露

1. **立即重置 Token**
   - 访问 [Tushare 官网](https://tushare.pro/)
   - 进入个人中心 → API Token → 重置

2. **清理 Git 历史**（如果已提交）
   ```bash
   # 安装 git-filter-repo
   pip install git-filter-repo
   
   # 清理历史中的 Token
   git filter-repo --replace-text <(echo '原Token==>新Token')
   
   # 强制推送
   git push --force
   ```

3. **更新所有使用处**
   - 本地 `.env` 文件
   - GitHub Secrets
   - 其他部署环境

### 使用 clean_token.py 工具

项目已包含 Token 安全扫描工具，可以帮助你检查和清理敏感信息。

#### 扫描项目

```bash
# 扫描并显示详细信息
python clean_token.py

# 仅检查，隐藏 Token 内容（适合分享截图）
python clean_token.py --check
```

#### 清理 Token 文件

```bash
# 交互式清理，将 Token 文件移动到备份目录
python clean_token.py --clean
```

#### 扫描结果示例

```
======================================================================
🔍 Token 安全扫描工具
======================================================================

📋 检查 .gitignore 配置...
  ✅ .gitignore 配置正确

📁 扫描 Token 文件...
  ⚠️  发现 2 个 Token 文件：
    📄 2000_token
    📄 5000_token

💻 扫描代码文件中的硬编码 Token...
  ✅ 未发现硬编码 Token

======================================================================
📊 扫描结果总结
======================================================================

⚠️  发现以下问题：
  - 发现 2 个 Token 文件

📋 建议操作：
  1. 确保所有 Token 文件已添加到 .gitignore
  2. 将 Token 转移到 .env 文件
  ...
```

### 安全检查清单

在推送到 GitHub 之前，请确认：

- [ ] `.env` 文件在 `.gitignore` 中
- [ ] `*_token` 文件在 `.gitignore` 中
- [ ] 运行 `python clean_token.py --check` 无警告
- [ ] 代码中无硬编码 Token
- [ ] GitHub Secrets 已设置
- [ ] 本地使用 `.env` 文件
- [ ] 定期更换 Token（建议每 3-6 个月）

---

**祝使用愉快！🚀**
