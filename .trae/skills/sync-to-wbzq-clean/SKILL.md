---
name: "sync-to-wbzq-clean"
description: "同步 wbzq 项目代码到 wbzq_clean 目录。Invoke when user says '同步到 wbzq_clean', 'sync to clean', '更新 wbzq_clean', or asks to copy code to the clean directory."
---

# 同步代码到 wbzq_clean

这个 Skill 用于将当前 wbzq 项目的代码同步到 wbzq_clean 目录。

## 使用场景

- 用户说："同步到 wbzq_clean"
- 用户说："更新 wbzq_clean"
- 用户说："sync to clean"
- 用户说："同步代码"
- 用户要求将代码复制到干净目录

## 执行步骤

1. **运行迁移脚本**
   ```bash
   python migrate_to_new_repo.py --target-dir "../wbzq_clean" --update
   ```

2. **检查同步结果**
   - 确认文件成功复制
   - 查看变更文件列表
   - 分析变更内容类型

3. **生成智能提交信息**
   
   根据同步的文件类型，生成有意义的提交信息：
   
   - **工作流变更** (`.github/workflows/*.yml`):
     ```bash
     git commit -m "ci: 更新 GitHub Actions 工作流配置"
     ```
   
   - **HTML 报告变更** (`html/**/*.html`):
     ```bash
     git commit -m "docs: 更新股票分析报告 $(date +%Y%m%d)"
     ```
   
   - **Python 脚本变更** (`*.py`):
     ```bash
     git commit -m "feat: 更新股票策略脚本"
     ```
   
   - **配置文件变更** (`requirements.txt`, `.gitignore` 等):
     ```bash
     git commit -m "chore: 更新项目配置"
     ```
   
   - **混合变更**:
     ```bash
     git commit -m "sync: 同步代码更新 ($(date +%Y-%m-%d %H:%M))"
     ```

4. **提示用户提交**
   
   显示具体的提交命令：
   ```bash
   cd C:\Users\zxlin\Desktop\大富翁\wbzq_clean
   git add .
   git commit -m "<根据变更类型生成的提交信息>"
   git push origin main
   ```

## 提交信息规范

遵循 Conventional Commits 规范：

| 类型 | 说明 | 示例 |
|------|------|------|
| `feat` | 新功能 | feat: 添加 J13 趋势图 90% 分位线 |
| `fix` | 修复 | fix: 修复 QQ 邮箱 SMTP 配置 |
| `docs` | 文档/报告 | docs: 更新 20260308 股票分析报告 |
| `ci` | CI/CD 配置 | ci: 更新 GitHub Actions 邮件通知 |
| `chore` | 杂项 | chore: 更新依赖配置 |
| `sync` | 同步 | sync: 同步代码更新 |

## 注意事项

- 使用 `--update` 模式进行增量同步
- 会自动处理新增、修改的文件
- 不会删除目标目录的文件
- 同步后会显示 Git 状态，方便用户提交
- 提交信息应根据实际变更内容智能生成

## 示例

用户："帮我同步到 wbzq_clean"

助手应该：
1. 执行 `python migrate_to_new_repo.py --target-dir "../wbzq_clean" --update`
2. 显示同步结果
3. 分析变更文件类型
4. 生成对应的提交命令
