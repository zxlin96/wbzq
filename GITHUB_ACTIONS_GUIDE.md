# GitHub Actions 部署指南

本文档指导你如何将股票策略回测系统部署到 GitHub Actions 实现自动定时运行。

## ⚠️ 重要安全提示

**Token 安全**：本项目已移除所有硬编码的 Token，必须通过环境变量设置。请勿将 Token 提交到 Git！

## 目录

1. [当前项目运行与触发](#当前项目运行与触发)
2. [快速开始](#快速开始)
3. [详细步骤](#详细步骤)
4. [本地开发配置](#本地开发配置)
5. [配置说明](#配置说明)
6. [查看结果](#查看结果)
7. [常见问题](#常见问题)
8. [Token 安全指南](#token-安全指南)

---

## 当前项目运行与触发

### 2026-11-02 邮件发送验证记录

| 项目 | 已验证信息 |
|------|------------|
| 仓库与分支 | `zxlin96/wbzq`，`main` |
| 工作流 | 股票策略定时执行，`.github/workflows/stock-strategy.yml` |
| 触发方式 | GitHub CLI 手动触发，事件为 `workflow_dispatch` |
| 运行编号 | `41373604658` |
| 使用代码 | `845aa4156d680d865d1a505c4e911d33c89614f0`，含 ETF 名称与代码展示 |
| 触发时间 | 北京时间 2026-11-02 09:39:20 |
| 完成时间 | 北京时间 2026-11-02 09:46:47 |
| 执行结果 | 工作流 `success`；生成、上传邮件报告及发送邮件步骤均成功 |
| 收件验证 | 用户已确认邮件正常收到 |

[查看本次运行及日志](https://github.com/zxlin96/wbzq/actions/runs/41373604658)。

这次上午运行验证了执行流程与邮件送达；盘中情绪信号存在行情日期校验缺口，
已按下方 2026-11-03 修复记录纠正，不能将当次输出视为有效收盘信号。

### 2026-11-03 盘中情绪信号修复记录

- 11 月 2 日上午运行使用 10 月 30 日的 J13 数量 3,036，却按 11 月 2 日记录了第 2 级、4,000 元买入。
- 当晚真实 J13 数量为 1,832，策略阈值为 2,576.8，本应不触发买入；原同日去重逻辑使早间错误状态被保留。
- 用户确认没有实际执行该笔买入。基于最后正确状态和已存档的 11 月 2 日行情重算，移除这笔错误记录，其余 23 笔历史记录保持不变。
- 修正后为 9 个记账单位，倍投级别重置到第 1 级（下一次有效触发从 2,000 元开始），红柱 1 根、绿柱 0 根，已处理行情日期为 2026-11-02。
- 新的实时入口仅在北京时间 15:00 收盘后、J13 与 ETF 行情均对应目标日期、收盘价有效时更新状态；数据未确认只生成预览，不写入仓位、柱数或已处理日期。
- 盘中运行不会占用当日去重标记，晚间完整行情仍可处理；已确认的同日重复运行继续保持幂等。行情缺失时不再仅凭旧 J13 数量落账。
- [修正审计记录](results/sentiment_correction_20261102/audit.json)保留了错误交易、修正前后字段和两次运行的来源链接。

### 当前机器的 CLI 环境

- 系统：Deepin 23.1，x86_64。
- GitHub CLI：`gh 2.103.3`，安装于 `/home/zxlin/.local/bin/gh`，已在 `PATH` 中。
- 安装来源：GitHub 官方 Debian 软件包，SHA256 与官方发布校验文件一致；无需管理员权限。
- GitHub 账号：`zxlin96`，已通过网页授权登录，凭据由系统 keyring 保存；Git 协议为 SSH。
- 用户要求手动触发时，优先使用已登录的 `gh`，无需依赖浏览器会话或重复上传 SSH 密钥。登录状态以 `gh auth status` 的当前输出为准。

手动触发一次并查看运行列表：

```bash
gh auth status
gh workflow run stock-strategy.yml --repo zxlin96/wbzq --ref main
gh run list --repo zxlin96/wbzq --workflow stock-strategy.yml --limit 5
```

`gh workflow run` 会返回本次运行链接。后续查询使用该次运行编号，不要再次触发来查询状态：

```bash
gh run view 41373604658 --repo zxlin96/wbzq
```

仅在登录失效时重新进行网页授权：

```bash
gh auth login --hostname github.com --git-protocol ssh --web --skip-ssh-key
```

终端显示的一次性验证码填写到 GitHub 授权网页，完成后再执行 `gh auth status`。
本文只记录运行信息与操作命令，不记录访问令牌、邮件授权码或一次性验证码。

### 已验证的邮件与触发配置

- 本次操作、持仓简表、估值定投和本周复核均显示 ETF 名称及交易代码（如 `513030.SH`）。
- 周日顶部保留周五已确认信号，注明供下周操作准备；本周操作汇总／复核位于邮件最后。
- 周线提醒窗口为北京时间周五和周日；日线卖出检查继续由策略执行，情绪反弹及估值定投独立检查。
- 发邮件使用仓库既有的 `EMAIL_USERNAME`、`EMAIL_PASSWORD`、`EMAIL_RECIPIENTS` Secrets，行情使用 `TUSHARE_TOKEN`。
- 定时执行和手动触发均使用同一工作流；具体定时配置见下方“定时执行时间”。

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

当前配置（以 `.github/workflows/stock-strategy.yml` 为准）：

```yaml
schedule:
  - cron: '0 12 * * 1-5'
  - cron: '0 10 * * 0'
```

含义：

- `0 12 * * 1-5`：周一至周五 UTC 12:00，即北京时间 20:00。
- `0 10 * * 0`：周日 UTC 10:00，即北京时间 18:00。
- 以上为配置触发时间，实际启动可能因 GitHub 排队而延后。

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
