# 迁移到新 Git 仓库指南

这是最简单的解决方案：**创建一个全新的、干净的 Git 仓库**，不包含任何历史记录。

---

## 方案对比

| 方案 | 复杂度 | 时间 | 效果 |
|------|--------|------|------|
| 重置 Token | ⭐ 简单 | 5分钟 | 旧 Token 失效，历史仍在 |
| **新建仓库** | ⭐ 简单 | 10分钟 | **完全干净，无历史** ✅ |
| 清理历史 | ⭐⭐⭐ 复杂 | 30分钟 | 保留历史但重写 |

**推荐：新建仓库** - 最简单、最彻底、最安全！

---

## 方法一：使用 Python 脚本（推荐，跨平台）

### 步骤 1：运行迁移脚本

```bash
# 在项目目录中运行
python migrate_to_new_repo.py
```

脚本会自动：
- ✅ 创建新目录 `wbzq_clean`
- ✅ 复制所有代码文件（排除敏感信息）
- ✅ 创建全新的 `.gitignore`
- ✅ 初始化新的 Git 仓库
- ✅ 创建初始提交

### 步骤 2：添加 Token

```bash
# 进入新目录
cd ../wbzq_clean

# 创建 .env 文件
# Windows:
notepad .env
# Linux/Mac:
nano .env
```

添加内容：
```
TUSHARE_TOKEN=你的Token
```

### 步骤 3：推送到 GitHub

```bash
# 添加远程仓库
git remote add origin https://github.com/你的用户名/仓库名.git

# 推送
git push -u origin main
```

---

### 后续代码更新

当你在 `wbzq` 目录修改代码后，同步到 `wbzq_clean`：

**Python 版本：**
```bash
# 在源目录运行
cd /path/to/wbzq
python migrate_to_new_repo.py --update

# 然后提交更新
cd ../wbzq_clean
git add .
git commit -m "Update: 描述修改内容"
git push
```

**PowerShell 版本：**
```powershell
# 在源目录运行
cd c:\Users\zxlin\Desktop\大富翁\wbzq
.\migrate_to_new_repo.ps1 -Update

# 然后提交更新
cd ..\wbzq_clean
git add .
git commit -m "Update: 描述修改内容"
git push
```

5. 后续代码更新同步：
   在源目录运行：
   ```bash
   # Python 版本
   python migrate_to_new_repo.py --update
   
   # 或 PowerShell 版本
   .\migrate_to_new_repo.ps1 -Update
   ```

---

## 方法二：使用 PowerShell 脚本（Windows）

```powershell
# 在项目目录中运行
.\migrate_to_new_repo.ps1
```

功能和 Python 版本相同，选择你熟悉的即可。

### 步骤 4：设置 GitHub Secrets

1. 访问 GitHub 仓库页面
2. Settings → Secrets → Actions
3. 添加 `TUSHARE_TOKEN`

---

## 方法二：手动操作

### 步骤 1：创建新目录

```powershell
# 在项目父目录中
mkdir wbzq_clean
cd wbzq_clean
```

### 步骤 2：复制文件（排除敏感信息）

```powershell
# 复制 Python 文件和配置
xcopy ..\wbzq\*.py .\ /E /I /Exclude:..\wbzq\.gitignore

# 复制其他必要文件
copy ..\wbzq\requirements.txt .\
copy ..\wbzq\README.md .\
```

### 步骤 3：创建 .gitignore

```powershell
notepad .gitignore
```

添加内容：
```gitignore
# 环境变量文件
.env
.env.local

# Token 文件
*_token
*token*

# 数据库和缓存
*.db
data_cache/

# 生成的文件
*.csv
*.html
*.log

# Python
__pycache__/
*.pyc
```

### 步骤 4：初始化 Git

```powershell
git init
git add .
git commit -m "Initial commit"
```

### 步骤 5：创建 .env 文件

```powershell
notepad .env
```

添加 Token：
```
TUSHARE_TOKEN=你的Token
```

### 步骤 6：推送到 GitHub

```powershell
git remote add origin https://github.com/你的用户名/仓库名.git
git push -u origin main
```

---

## 验证新仓库

```powershell
# 1. 检查 Token 文件是否被忽略
git status

# 2. 运行安全检查
python clean_token.py --check

# 3. 验证配置
python -c "from config import APIConfig; print('✅ 配置正常')"
```

---

## 清理旧仓库（可选）

迁移完成后，你可以：

### 方案 A：保留旧仓库（推荐）
```powershell
# 重命名作为备份
Rename-Item ..\wbzq wbzq_backup
```

### 方案 B：删除旧仓库
```powershell
# 彻底删除
Remove-Item -Recurse -Force ..\wbzq
```

### 方案 C：删除 GitHub 上的旧仓库
1. 访问 GitHub 旧仓库页面
2. Settings → Danger Zone → Delete this repository
3. 确认删除

---

## 常见问题

### Q: 新仓库会丢失历史提交记录吗？

**A**: 是的，但这不是坏事！
- 旧仓库的提交历史包含 Token
- 新仓库是干净的起点
- 代码功能完全保留

### Q: GitHub Actions 配置需要重新设置吗？

**A**: 是的，需要：
1. 在新仓库重新添加 `.github/workflows/stock-strategy.yml`
2. 重新设置 GitHub Secrets
3. 重新启用 Actions

### Q: 我可以保留两个仓库吗？

**A**: 可以！
- 旧仓库：作为本地备份
- 新仓库：用于 GitHub Actions
- 记得重置旧仓库中的 Token

### Q: 协作的同事怎么办？

**A**: 
1. 通知同事使用新仓库
2. 旧仓库设置为只读或删除
3. 同事重新克隆新仓库

---

## 安全检查清单

迁移完成后确认：

- [ ] 新仓库已创建
- [ ] `.env` 文件已创建且包含 Token
- [ ] `.gitignore` 包含所有敏感文件
- [ ] 运行 `git status` 不显示敏感文件
- [ ] 运行 `python clean_token.py --check` 通过
- [ ] 已推送到 GitHub
- [ ] GitHub Secrets 已设置
- [ ] GitHub Actions 运行正常
- [ ] 旧仓库已备份或删除

---

**这是最简单、最安全的方案！新仓库完全干净，没有任何历史负担。**
