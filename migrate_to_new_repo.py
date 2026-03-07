#!/usr/bin/env python3
"""
迁移到新 Git 仓库脚本（Python 版本）
功能：
  1. 创建项目的干净副本，不包含任何 Git 历史记录
  2. 支持后续代码更新同步
  3. 支持增量更新（只复制修改的文件）

使用方法：
  python migrate_to_new_repo.py                    # 首次迁移
  python migrate_to_new_repo.py --update           # 后续更新同步
  python migrate_to_new_repo.py --target-dir "../myproject"  # 指定目标目录
  python migrate_to_new_repo.py --dry-run          # 试运行
"""

import os
import sys
import shutil
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Set, Tuple

__version__ = "1.1.0"

# 定义要排除的文件和目录
EXCLUDE_DIRS = {
    '.git', '__pycache__', '.pytest_cache', '.venv', 'venv',
    'data_cache', 'logs', '.token_backup', '.git_backup', '.idea', '.vscode'
}

EXCLUDE_FILES = {
    '*.pyc', '*.pyo', '*.db', '*.csv', '*.pkl', '*.log',
    '2000_token', '5000_token', '.env', '.env.local', '.gitignore'
}


def print_header(text: str, color: str = "cyan"):
    """打印带颜色的标题"""
    colors = {
        "cyan": "\033[96m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "red": "\033[91m",
        "magenta": "\033[95m",
        "gray": "\033[90m",
        "reset": "\033[0m"
    }
    
    if sys.platform == "win32":
        # Windows 可能需要启用 ANSI 支持
        os.system("")
    
    c = colors.get(color, colors["reset"])
    reset = colors["reset"]
    print(f"{c}{text}{reset}")


def should_exclude(path: Path, relative_path: str) -> bool:
    """检查是否应该排除该路径"""
    # 检查目录
    for part in relative_path.split(os.sep):
        if part in EXCLUDE_DIRS:
            return True
    
    # 检查文件
    filename = path.name
    for pattern in EXCLUDE_FILES:
        if pattern.startswith('*'):
            if filename.endswith(pattern[1:]):
                return True
        elif pattern.endswith('*'):
            if filename.startswith(pattern[:-1]):
                return True
        elif filename == pattern:
            return True
    
    return False


def get_gitignore_content() -> str:
    """获取 .gitignore 内容"""
    return '''# 环境变量文件（包含敏感信息）
.env
.env.local

# Token 文件（包含敏感 API Token）
*_token
*token*
!token_example.txt

# 数据库和缓存
stock_strategy.db
*.db
data_cache/
logs/

# 生成的结果文件
*.csv
*.html
*.pkl
*.log

# Python
*.pyc
__pycache__/
*.pyo
*.pyd
.Python
*.so
*.egg
*.egg-info/
dist/
build/

# 项目特定文件
daily_first_j13_interactive.html
first_j13_step_daily_count.html
dtw_results/
validation_report.txt

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# 备份目录
.token_backup/
.git_backup/

# 迁移工具生成的文件
MIGRATION_NOTE.md
'''


def get_env_example_content() -> str:
    """获取 .env.example 内容"""
    return '''# Tushare API Token（必填）
# 从 https://tushare.pro/register.html 注册获取
TUSHARE_TOKEN=your_tushare_token_here

# 可选配置
DB_PATH=stock_strategy.db
CACHE_DIR=data_cache
'''


def get_readme_content() -> str:
    """获取 README.md 内容"""
    return '''# 股票策略回测系统

基于 Python 的股票策略分析和回测系统。

## 快速开始

1. 安装依赖
   ```bash
   pip install -r requirements.txt
   ```

2. 配置 Token
   ```bash
   cp .env.example .env
   # 编辑 .env 文件，添加你的 Tushare Token
   ```

3. 运行策略
   ```bash
   python main_par2.py --backtest --hold-days 3
   ```

## GitHub Actions 自动运行

1. 在 GitHub 创建仓库
2. 推送代码
3. 设置 Secrets: `TUSHARE_TOKEN`
4. 自动定时运行

## 项目结构

- `main_par2.py` - 主程序
- `config.py` - 配置文件
- `data_manager.py` - 数据管理
- `.github/workflows/` - GitHub Actions 配置

## 安全说明

- Token 存储在 `.env` 文件，不会被提交
- 运行 `python clean_token.py --check` 检查安全
- 定期更新 Token

## 许可证

MIT
'''


def get_migration_note(source_dir: Path, target_dir: Path) -> str:
    """获取迁移说明内容"""
    return f'''# 迁移说明

## 项目来源
此项目从以下目录迁移而来：
源目录: {source_dir}
迁移时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 下一步操作

1. 创建 .env 文件并添加 Token：
   cd '{target_dir}'
   notepad .env
   
   添加内容：
   TUSHARE_TOKEN=你的Token

2. 推送到 GitHub：
   git remote add origin https://github.com/你的用户名/仓库名.git
   git push -u origin main

3. 设置 GitHub Secrets

4. 后续更新同步：
   在源目录运行：
   python migrate_to_new_repo.py --update

## 文件变更
此目录是全新的 Git 仓库，不包含任何历史记录。
所有敏感信息（Token）已排除。
'''


def scan_source_files(source_dir: Path, include_old: bool = False) -> List[Path]:
    """扫描源目录中的所有文件"""
    files = []
    exclude_dirs = EXCLUDE_DIRS.copy()
    if not include_old:
        exclude_dirs.add('old')
    
    for root, dirs, filenames in os.walk(source_dir):
        # 过滤目录
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        
        for filename in filenames:
            file_path = Path(root) / filename
            relative_path = file_path.relative_to(source_dir)
            
            if not should_exclude(file_path, str(relative_path)):
                files.append(file_path)
    
    return files


def copy_file(src: Path, dst: Path, dry_run: bool = False) -> bool:
    """复制单个文件"""
    try:
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return True
    except Exception as e:
        print(f"  [错误] 复制失败 {src}: {e}")
        return False


def migrate_new(source_dir: Path, target_dir: Path, dry_run: bool = False, include_old: bool = False):
    """全新迁移模式"""
    print_header("=" * 70, "cyan")
    print_header(f"  迁移到新 Git 仓库工具 v{__version__}", "cyan")
    print_header("=" * 70, "cyan")
    print()
    
    print_header(f"源目录: {source_dir}", "gray")
    print_header(f"目标目录: {target_dir}", "gray")
    print_header("模式: 全新迁移", "green")
    if dry_run:
        print_header("⚠️  试运行模式（不会实际复制文件）", "magenta")
    print()
    
    # 检查目标目录
    if target_dir.exists():
        print_header(f"⚠️  目标目录已存在: {target_dir}", "yellow")
        response = input("是否删除现有目录？(yes/no): ")
        if response.lower() == 'yes':
            if not dry_run:
                shutil.rmtree(target_dir)
                print_header("  ✅ 已删除现有目录", "green")
            else:
                print_header(f"  [试运行] 将删除目录: {target_dir}", "magenta")
        else:
            print_header("操作已取消", "yellow")
            return
    
    # 步骤 1: 创建目标目录
    print_header("步骤 1/5: 创建目标目录...", "cyan")
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        print_header("  ✅ 目录创建完成", "green")
    else:
        print_header(f"  [试运行] 将创建目录: {target_dir}", "magenta")
    
    # 步骤 2: 扫描并复制文件
    print_header("\n步骤 2/5: 扫描并复制项目文件...", "cyan")
    files = scan_source_files(source_dir, include_old)
    print_header(f"  发现 {len(files)} 个文件需要复制", "gray")
    
    success_count = 0
    fail_count = 0
    
    for src_file in files:
        relative_path = src_file.relative_to(source_dir)
        dst_file = target_dir / relative_path
        
        if dry_run:
            print_header(f"  [试运行] 将复制: {relative_path}", "gray")
            success_count += 1
        else:
            if copy_file(src_file, dst_file):
                print_header(f"  ✅ {relative_path}", "green")
                success_count += 1
            else:
                fail_count += 1
    
    print_header(f"\n  复制完成: 成功 {success_count}, 失败 {fail_count}", 
                "green" if fail_count == 0 else "yellow")
    
    # 步骤 3: 创建配置文件
    print_header("\n步骤 3/5: 创建配置文件...", "cyan")
    if not dry_run:
        # 创建 .gitignore
        (target_dir / ".gitignore").write_text(get_gitignore_content(), encoding='utf-8')
        print_header("  ✅ .gitignore 创建完成", "green")
        
        # 创建 .env.example
        (target_dir / ".env.example").write_text(get_env_example_content(), encoding='utf-8')
        print_header("  ✅ .env.example 创建完成", "green")
        
        # 创建 README.md（如果不存在）
        readme_path = target_dir / "README.md"
        if not readme_path.exists():
            readme_path.write_text(get_readme_content(), encoding='utf-8')
            print_header("  ✅ README.md 创建完成", "green")
    else:
        print_header("  [试运行] 将创建配置文件", "magenta")
    
    # 步骤 4: 初始化 Git
    print_header("\n步骤 4/5: 初始化新的 Git 仓库...", "cyan")
    if not dry_run:
        os.chdir(target_dir)
        os.system("git init > nul 2>&1")
        print_header("  ✅ Git 初始化完成", "green")
        
        # 配置 Git 用户信息
        os.system('git config user.name "Stock Strategy Bot" > nul 2>&1')
        os.system('git config user.email "bot@example.com" > nul 2>&1')
        print_header("  ✅ Git 用户信息已设置", "green")
        
        # 添加并提交
        os.system("git add . > nul 2>&1")
        print_header("  ✅ 文件已添加到暂存区", "green")
        
        commit_msg = """Initial commit: 股票策略回测系统

- 添加核心策略分析功能
- 添加回测功能
- 添加数据管理模块
- 配置 GitHub Actions 自动运行
- 添加 Token 安全检查工具"""
        
        os.system(f'git commit -m "{commit_msg}" > nul 2>&1')
        print_header("  ✅ 初始提交完成", "green")
        
        os.chdir(source_dir)
    else:
        print_header("  [试运行] 将初始化 Git 仓库", "magenta")
    
    # 步骤 5: 创建迁移说明
    print_header("\n步骤 5/5: 创建迁移说明...", "cyan")
    if not dry_run:
        migration_note = get_migration_note(source_dir, target_dir)
        (target_dir / "MIGRATION_NOTE.md").write_text(migration_note, encoding='utf-8')
        print_header("  ✅ 迁移说明已创建", "green")
    else:
        print_header("  [试运行] 将创建迁移说明", "magenta")
    
    # 完成
    print_header("\n" + "=" * 70, "green")
    if dry_run:
        print_header("  ✅ 试运行完成！", "magenta")
    else:
        print_header("  ✅ 迁移完成！", "green")
    print_header("=" * 70, "green")
    print()
    print_header(f"新仓库位置: {target_dir}", "cyan")
    print()
    
    if not dry_run:
        print_header("下一步操作：", "yellow")
        print()
        print("1. 创建 .env 文件并添加 Token：")
        print(f"   cd '{target_dir}'")
        print("   notepad .env")
        print()
        print("2. 在 GitHub 创建新仓库（不要初始化）")
        print("   https://github.com/new")
        print()
        print("3. 推送到 GitHub：")
        print("   git remote add origin https://github.com/你的用户名/仓库名.git")
        print("   git push -u origin main")
        print()
        print("4. 设置 GitHub Secrets：")
        print("   - 访问仓库 Settings -> Secrets -> Actions")
        print("   - 添加 TUSHARE_TOKEN")
        print()
        print("5. 后续代码更新同步：")
        print(f"   cd '{source_dir}'")
        print("   python migrate_to_new_repo.py --update")
        print()
        print("6. 验证配置：")
        print("   python clean_token.py --check")
        print()
        print("⚠️  注意：")
        print("   - 旧仓库可以删除或保留备份")
        print("   - 新仓库没有历史记录，完全干净")
        print("   - Token 文件需要手动复制到 .env")
        print("   - 后续更新使用 --update 参数")
    else:
        print_header("试运行完成，实际运行请去掉 --dry-run 参数", "yellow")
    print()


def migrate_update(source_dir: Path, target_dir: Path, dry_run: bool = False, include_old: bool = False):
    """更新同步模式"""
    print_header("=" * 70, "cyan")
    print_header(f"  迁移到新 Git 仓库工具 v{__version__}", "cyan")
    print_header("=" * 70, "cyan")
    print()
    
    print_header(f"源目录: {source_dir}", "gray")
    print_header(f"目标目录: {target_dir}", "gray")
    print_header("模式: 更新同步", "yellow")
    if dry_run:
        print_header("⚠️  试运行模式（不会实际复制文件）", "magenta")
    print()
    
    # 检查目标目录
    if not target_dir.exists():
        print_header("❌ 错误：目标目录不存在，无法更新。请先运行迁移。", "red")
        return
    
    # 步骤 1: 扫描变更文件
    print_header("步骤 1/4: 扫描变更文件...", "cyan")
    
    source_files = scan_source_files(source_dir, include_old)
    files_to_update = []
    files_to_add = []
    
    for src_file in source_files:
        relative_path = src_file.relative_to(source_dir)
        dst_file = target_dir / relative_path
        
        if dst_file.exists():
            # 文件存在，检查是否需要更新
            src_stat = src_file.stat()
            dst_stat = dst_file.stat()
            
            if src_stat.st_mtime > dst_stat.st_mtime or src_stat.st_size != dst_stat.st_size:
                files_to_update.append({
                    'source': src_file,
                    'target': dst_file,
                    'relative': relative_path,
                    'action': '更新'
                })
        else:
            # 新文件
            files_to_add.append({
                'source': src_file,
                'target': dst_file,
                'relative': relative_path,
                'action': '新增'
            })
    
    total_changes = len(files_to_update) + len(files_to_add)
    
    if total_changes == 0:
        print_header("  ✅ 所有文件已是最新，无需更新", "green")
        return
    
    print_header(f"  发现 {len(files_to_update)} 个文件需要更新", "yellow")
    print_header(f"  发现 {len(files_to_add)} 个新文件", "yellow")
    
    if dry_run:
        print_header("\n📋 试运行 - 将要执行的操作：", "magenta")
        for file in files_to_update + files_to_add:
            print_header(f"  [{file['action']}] {file['relative']}", "gray")
        return
    
    # 步骤 2: 复制变更文件
    print_header("\n步骤 2/4: 复制变更文件...", "cyan")
    
    success_count = 0
    fail_count = 0
    
    for file in files_to_update + files_to_add:
        if copy_file(file['source'], file['target']):
            print_header(f"  ✅ [{file['action']}] {file['relative']}", "green")
            success_count += 1
        else:
            fail_count += 1
    
    # 步骤 3: 检查并更新 .gitignore
    print_header("\n步骤 3/4: 检查并更新 .gitignore...", "cyan")
    gitignore_path = target_dir / ".gitignore"
    if gitignore_path.exists():
        current_content = gitignore_path.read_text(encoding='utf-8')
        new_content = get_gitignore_content()
        
        if current_content != new_content:
            gitignore_path.write_text(new_content, encoding='utf-8')
            print_header("  ✅ .gitignore 已更新", "green")
        else:
            print_header("  ✅ .gitignore 已是最新", "green")
    
    # 步骤 4: Git 状态检查
    print_header("\n步骤 4/4: Git 状态检查...", "cyan")
    os.chdir(target_dir)
    result = os.popen("git status --short").read()
    
    if result.strip():
        print_header("  检测到以下变更：", "yellow")
        print(result)
        print()
        print("请手动提交更新：")
        print(f"  cd '{target_dir}'")
        print("  git add .")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        print(f"  git commit -m 'Update from source {timestamp}'")
    else:
        print_header("  ✅ Git 工作区干净", "green")
    
    os.chdir(source_dir)
    
    # 完成
    print_header("\n" + "=" * 70, "green")
    print_header("  ✅ 更新同步完成！", "green")
    print_header("=" * 70, "green")
    print()
    print_header(f"成功: {success_count}, 失败: {fail_count}", 
                "green" if fail_count == 0 else "yellow")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='迁移到新 Git 仓库工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  python migrate_to_new_repo.py                    # 首次迁移
  python migrate_to_new_repo.py --update           # 后续更新同步
  python migrate_to_new_repo.py --target-dir ../myproject  # 指定目标目录
  python migrate_to_new_repo.py --dry-run          # 试运行
  python migrate_to_new_repo.py --include-old      # 包含 old/ 目录
        '''
    )
    
    parser.add_argument('--target-dir', default='../wbzq_clean',
                       help='目标目录路径 (默认: ../wbzq_clean)')
    parser.add_argument('--repo-name', default='',
                       help='仓库名称（如果指定，将创建在父目录）')
    parser.add_argument('--update', action='store_true',
                       help='更新模式：同步代码变更')
    parser.add_argument('--dry-run', action='store_true',
                       help='试运行：只显示将要执行的操作')
    parser.add_argument('--include-old', action='store_true',
                       help='包含 old/ 目录')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    
    args = parser.parse_args()
    
    # 确定源目录和目标目录
    source_dir = Path.cwd()
    
    if args.repo_name:
        target_dir = source_dir.parent / args.repo_name
    else:
        target_dir = Path(args.target_dir).resolve()
    
    # 执行迁移
    if args.update:
        migrate_update(source_dir, target_dir, args.dry_run, args.include_old)
    else:
        migrate_new(source_dir, target_dir, args.dry_run, args.include_old)


if __name__ == "__main__":
    main()
