# 迁移到新 Git 仓库脚本（PowerShell）
# 功能：
#   1. 创建项目的干净副本，不包含任何 Git 历史记录
#   2. 支持后续代码更新同步
#   3. 支持增量更新（只复制修改的文件）
#
# 使用方法：
#   .\migrate_to_new_repo.ps1                    # 首次迁移
#   .\migrate_to_new_repo.ps1 -Update            # 后续更新同步
#   .\migrate_to_new_repo.ps1 -TargetDir "..\myproject"  # 指定目标目录

param(
    [string]$TargetDir = "..\wbzq_clean",
    [string]$RepoName = "",
    [switch]$Update = $false,           # 更新模式
    [switch]$DryRun = $false,           # 试运行（不实际复制）
    [switch]$IncludeOld = $false        # 是否包含 old/ 目录
)

# 版本号
$ScriptVersion = "1.1.0"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  迁移到新 Git 仓库工具 v$ScriptVersion" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$SourceDir = Get-Location

# 处理目标目录
if ($RepoName) {
    $TargetPath = Resolve-Path (Join-Path $SourceDir "..") -ErrorAction SilentlyContinue
    if (-not $TargetPath) {
        $TargetPath = Split-Path $SourceDir -Parent
    }
    $FullTargetDir = Join-Path $TargetPath $RepoName
} else {
    $FullTargetDir = Resolve-Path (Join-Path $SourceDir $TargetDir) -ErrorAction SilentlyContinue
    if (-not $FullTargetDir) {
        $FullTargetDir = Join-Path (Split-Path $SourceDir -Parent) (Split-Path $TargetDir -Leaf)
    }
}

Write-Host "源目录: $SourceDir" -ForegroundColor Gray
Write-Host "目标目录: $FullTargetDir" -ForegroundColor Gray
if ($Update) {
    Write-Host "模式: 更新同步" -ForegroundColor Yellow
} else {
    Write-Host "模式: 全新迁移" -ForegroundColor Green
}
if ($DryRun) {
    Write-Host "⚠️  试运行模式（不会实际复制文件）" -ForegroundColor Magenta
}
Write-Host ""

# 定义要排除的文件和目录
$ExcludeDirs = @(
    '.git',
    '__pycache__',
    '.pytest_cache',
    '.venv',
    'venv',
    'data_cache',
    'logs',
    '.token_backup',
    '.git_backup'
)

if (-not $IncludeOld) {
    $ExcludeDirs += 'old'
}

$ExcludeFiles = @(
    '*.pyc',
    '*.pyo',
    '*.db',
    '*.csv',
    '*.html',
    '*.pkl',
    '*.log',
    '2000_token',
    '5000_token',
    '.env',
    '.env.local',
    '.gitignore'  # 我们会创建新的
)

# 更新模式
if ($Update) {
    if (-not (Test-Path $FullTargetDir)) {
        Write-Host "❌ 错误：目标目录不存在，无法更新。请先运行迁移。" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "步骤 1/4: 扫描变更文件..." -ForegroundColor Cyan
    
    # 获取源目录和目标目录的文件列表
    $sourceFiles = Get-ChildItem -Path $SourceDir -Recurse -File | 
        Where-Object { 
            $relativePath = $_.FullName.Substring($SourceDir.Path.Length + 1)
            $dirName = Split-Path $relativePath -Parent
            
            # 检查是否在排除目录中
            $inExcludedDir = $false
            foreach ($excludedDir in $ExcludeDirs) {
                if ($relativePath -like "$excludedDir*" -or $dirName -like "$excludedDir*") {
                    $inExcludedDir = $true
                    break
                }
            }
            
            # 检查是否在排除文件模式中
            $isExcludedFile = $false
            foreach ($pattern in $ExcludeFiles) {
                if ($_.Name -like $pattern) {
                    $isExcludedFile = $true
                    break
                }
            }
            
            -not $inExcludedDir -and -not $isExcludedFile
        }
    
    $filesToUpdate = @()
    $filesToAdd = @()
    
    foreach ($file in $sourceFiles) {
        $relativePath = $file.FullName.Substring($SourceDir.Path.Length + 1)
        $targetFile = Join-Path $FullTargetDir $relativePath
        
        if (Test-Path $targetFile) {
            # 文件存在，检查是否需要更新
            $targetInfo = Get-Item $targetFile
            if ($file.LastWriteTime -gt $targetInfo.LastWriteTime -or 
                $file.Length -ne $targetInfo.Length) {
                $filesToUpdate += @{
                    Source = $file.FullName
                    Target = $targetFile
                    Relative = $relativePath
                    Action = "更新"
                }
            }
        } else {
            # 新文件
            $filesToAdd += @{
                Source = $file.FullName
                Target = $targetFile
                Relative = $relativePath
                Action = "新增"
            }
        }
    }
    
    $totalChanges = $filesToUpdate.Count + $filesToAdd.Count
    
    if ($totalChanges -eq 0) {
        Write-Host "  ✅ 所有文件已是最新，无需更新" -ForegroundColor Green
        exit 0
    }
    
    Write-Host "  发现 $($filesToUpdate.Count) 个文件需要更新" -ForegroundColor Yellow
    Write-Host "  发现 $($filesToAdd.Count) 个新文件" -ForegroundColor Yellow
    
    if ($DryRun) {
        Write-Host "\n📋 试运行 - 将要执行的操作：" -ForegroundColor Magenta
        foreach ($file in ($filesToUpdate + $filesToAdd)) {
            Write-Host "  [$($file.Action)] $($file.Relative)" -ForegroundColor Gray
        }
        exit 0
    }
    
    Write-Host ""
    Write-Host "步骤 2/4: 复制变更文件..." -ForegroundColor Cyan
    
    $successCount = 0
    $failCount = 0
    
    foreach ($file in ($filesToUpdate + $filesToAdd)) {
        try {
            $targetDir = Split-Path $file.Target -Parent
            if (-not (Test-Path $targetDir)) {
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
            }
            Copy-Item -Path $file.Source -Destination $file.Target -Force
            Write-Host "  ✅ [$($file.Action)] $($file.Relative)" -ForegroundColor Green
            $successCount++
        } catch {
            Write-Host "  ❌ [$($file.Action)] $($file.Relative) - $_" -ForegroundColor Red
            $failCount++
        }
    }
    
    Write-Host ""
    Write-Host "步骤 3/4: 检查并更新 .gitignore..." -ForegroundColor Cyan
    
    $gitignorePath = Join-Path $FullTargetDir ".gitignore"
    if (Test-Path $gitignorePath) {
        $currentContent = Get-Content $gitignorePath -Raw
        $newContent = Get-GitignoreContent
        
        if ($currentContent -ne $newContent) {
            $newContent | Out-File -FilePath $gitignorePath -Encoding UTF8
            Write-Host "  ✅ .gitignore 已更新" -ForegroundColor Green
        } else {
            Write-Host "  ✅ .gitignore 已是最新" -ForegroundColor Green
        }
    }
    
    Write-Host ""
    Write-Host "步骤 4/4: Git 状态检查..." -ForegroundColor Cyan
    
    Set-Location $FullTargetDir
    $gitStatus = git status --short
    
    if ($gitStatus) {
        Write-Host "  检测到以下变更：" -ForegroundColor Yellow
        Write-Host $gitStatus -ForegroundColor Gray
        Write-Host ""
        Write-Host "请手动提交更新：" -ForegroundColor Cyan
        Write-Host "  cd '$FullTargetDir'" -ForegroundColor White
        Write-Host "  git add ." -ForegroundColor White
        Write-Host "  git commit -m 'Update from source $(Get-Date -Format 'yyyy-MM-dd HH:mm')'" -ForegroundColor White
    } else {
        Write-Host "  ✅ Git 工作区干净" -ForegroundColor Green
    }
    
    Set-Location $SourceDir
    
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  ✅ 更新同步完成！" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "成功: $successCount, 失败: $failCount" -ForegroundColor $(if ($failCount -eq 0) { "Green" } else { "Yellow" })
    
    exit 0
}

# 全新迁移模式
if (Test-Path $FullTargetDir) {
    Write-Host "⚠️  目标目录已存在: $FullTargetDir" -ForegroundColor Yellow
    $remove = Read-Host "是否删除现有目录？(yes/no)"
    if ($remove -eq 'yes') {
        if (-not $DryRun) {
            Remove-Item -Recurse -Force $FullTargetDir
            Write-Host "  ✅ 已删除现有目录" -ForegroundColor Green
        } else {
            Write-Host "  [试运行] 将删除目录: $FullTargetDir" -ForegroundColor Magenta
        }
    } else {
        Write-Host "操作已取消" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host "步骤 1/5: 创建目标目录..." -ForegroundColor Cyan
if (-not $DryRun) {
    New-Item -ItemType Directory -Path $FullTargetDir | Out-Null
    Write-Host "  ✅ 目录创建完成" -ForegroundColor Green
} else {
    Write-Host "  [试运行] 将创建目录: $FullTargetDir" -ForegroundColor Magenta
}

Write-Host ""
Write-Host "步骤 2/5: 复制项目文件..." -ForegroundColor Cyan

# 构建 robocopy 参数
$robocopyOptions = @('/E', '/MT:8', '/NDL', '/NFL', '/NJH', '/NJS')

# 添加排除目录
foreach ($dir in $ExcludeDirs) {
    $robocopyOptions += '/XD'
    $robocopyOptions += $dir
}

# 添加排除文件
foreach ($file in $ExcludeFiles) {
    $robocopyOptions += '/XF'
    $robocopyOptions += $file
}

if ($DryRun) {
    Write-Host "  [试运行] 将执行 robocopy:" -ForegroundColor Magenta
    Write-Host "    源: $SourceDir" -ForegroundColor Gray
    Write-Host "    目标: $FullTargetDir" -ForegroundColor Gray
    Write-Host "    排除目录: $($ExcludeDirs -join ', ')" -ForegroundColor Gray
    Write-Host "    排除文件: $($ExcludeFiles -join ', ')" -ForegroundColor Gray
} else {
    $robocopyCmd = "robocopy `"$SourceDir`" `"$FullTargetDir`" $($robocopyOptions -join ' ')"
    Invoke-Expression $robocopyCmd | Out-Null
    
    # 检查 robocopy 结果（0-7 都是成功）
    if ($LASTEXITCODE -le 7) {
        Write-Host "  ✅ 文件复制完成" -ForegroundColor Green
    } else {
        Write-Host "  ⚠️  复制可能有警告，继续执行..." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "步骤 3/5: 创建配置文件..." -ForegroundColor Cyan

if (-not $DryRun) {
    # 创建 .gitignore
    $gitignoreContent = Get-GitignoreContent
    $gitignoreContent | Out-File -FilePath (Join-Path $FullTargetDir ".gitignore") -Encoding UTF8
    Write-Host "  ✅ .gitignore 创建完成" -ForegroundColor Green
    
    # 创建 .env.example
    $envExampleContent = Get-EnvExampleContent
    $envExampleContent | Out-File -FilePath (Join-Path $FullTargetDir ".env.example") -Encoding UTF8
    Write-Host "  ✅ .env.example 创建完成" -ForegroundColor Green
    
    # 创建 README.md（如果不存在）
    $readmePath = Join-Path $FullTargetDir "README.md"
    if (-not (Test-Path $readmePath)) {
        $readmeContent = Get-ReadmeContent
        $readmeContent | Out-File -FilePath $readmePath -Encoding UTF8
        Write-Host "  ✅ README.md 创建完成" -ForegroundColor Green
    }
} else {
    Write-Host "  [试运行] 将创建配置文件" -ForegroundColor Magenta
}

Write-Host ""
Write-Host "步骤 4/5: 初始化新的 Git 仓库..." -ForegroundColor Cyan

if (-not $DryRun) {
    Set-Location $FullTargetDir
    
    # 初始化 Git
    git init | Out-Null
    Write-Host "  ✅ Git 初始化完成" -ForegroundColor Green
    
    # 配置 Git 用户信息（如果未设置）
    $gitUserName = git config user.name
    $gitUserEmail = git config user.email
    
    if (-not $gitUserName) {
        git config user.name "Stock Strategy Bot"
        Write-Host "  ✅ Git 用户名已设置" -ForegroundColor Green
    }
    if (-not $gitUserEmail) {
        git config user.email "bot@example.com"
        Write-Host "  ✅ Git 邮箱已设置" -ForegroundColor Green
    }
    
    # 添加所有文件
    git add . | Out-Null
    Write-Host "  ✅ 文件已添加到暂存区" -ForegroundColor Green
    
    # 提交
    git commit -m "Initial commit: 股票策略回测系统

- 添加核心策略分析功能
- 添加回测功能
- 添加数据管理模块
- 配置 GitHub Actions 自动运行
- 添加 Token 安全检查工具" | Out-Null
    
    Write-Host "  ✅ 初始提交完成" -ForegroundColor Green
    
    Set-Location $SourceDir
} else {
    Write-Host "  [试运行] 将初始化 Git 仓库" -ForegroundColor Magenta
}

Write-Host ""
Write-Host "步骤 5/5: 创建迁移说明..." -ForegroundColor Cyan

if (-not $DryRun) {
    $migrateNote = @"
# 迁移说明

## 项目来源
此项目从以下目录迁移而来：
源目录: $SourceDir
迁移时间: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

## 下一步操作

1. 创建 .env 文件并添加 Token：
   cd '$FullTargetDir'
   notepad .env
   
   添加内容：
   TUSHARE_TOKEN=你的Token

2. 推送到 GitHub：
   git remote add origin https://github.com/你的用户名/仓库名.git
   git push -u origin main

3. 设置 GitHub Secrets

4. 后续更新同步：
   在源目录运行：
   .\migrate_to_new_repo.ps1 -Update

## 文件变更
此目录是全新的 Git 仓库，不包含任何历史记录。
所有敏感信息（Token）已排除。
"@
    
    $migrateNote | Out-File -FilePath (Join-Path $FullTargetDir "MIGRATION_NOTE.md") -Encoding UTF8
    Write-Host "  ✅ 迁移说明已创建" -ForegroundColor Green
} else {
    Write-Host "  [试运行] 将创建迁移说明" -ForegroundColor Magenta
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
if ($DryRun) {
    Write-Host "  ✅ 试运行完成！" -ForegroundColor Magenta
} else {
    Write-Host "  ✅ 迁移完成！" -ForegroundColor Green
}
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "新仓库位置: $FullTargetDir" -ForegroundColor Cyan
Write-Host ""

if (-not $DryRun) {
    Write-Host "下一步操作：" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1. 创建 .env 文件并添加 Token：" -ForegroundColor White
    Write-Host "   cd '$FullTargetDir'" -ForegroundColor Gray
    Write-Host "   notepad .env" -ForegroundColor Gray
    Write-Host ""
    Write-Host "2. 在 GitHub 创建新仓库（不要初始化）" -ForegroundColor White
    Write-Host "   https://github.com/new" -ForegroundColor Gray
    Write-Host ""
    Write-Host "3. 推送到 GitHub：" -ForegroundColor White
    Write-Host "   git remote add origin https://github.com/你的用户名/仓库名.git" -ForegroundColor Gray
    Write-Host "   git push -u origin main" -ForegroundColor Gray
    Write-Host ""
    Write-Host "4. 设置 GitHub Secrets：" -ForegroundColor White
    Write-Host "   - 访问仓库 Settings -> Secrets -> Actions" -ForegroundColor Gray
    Write-Host "   - 添加 TUSHARE_TOKEN" -ForegroundColor Gray
    Write-Host ""
    Write-Host "5. 后续代码更新同步：" -ForegroundColor White
    Write-Host "   cd '$SourceDir'" -ForegroundColor Gray
    Write-Host "   .\migrate_to_new_repo.ps1 -Update" -ForegroundColor Gray
    Write-Host ""
    Write-Host "6. 验证配置：" -ForegroundColor White
    Write-Host "   python clean_token.py --check" -ForegroundColor Gray
    Write-Host ""
    Write-Host "⚠️  注意：" -ForegroundColor Yellow
    Write-Host "   - 旧仓库可以删除或保留备份" -ForegroundColor Gray
    Write-Host "   - 新仓库没有历史记录，完全干净" -ForegroundColor Gray
    Write-Host "   - Token 文件需要手动复制到 .env" -ForegroundColor Gray
    Write-Host "   - 后续更新使用 -Update 参数" -ForegroundColor Gray
} else {
    Write-Host "试运行完成，实际运行请去掉 -DryRun 参数" -ForegroundColor Yellow
}
Write-Host ""

# 辅助函数：获取 .gitignore 内容
function Get-GitignoreContent {
    return @'
# 环境变量文件（包含敏感信息）
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
'@
}

# 辅助函数：获取 .env.example 内容
function Get-EnvExampleContent {
    return @'
# Tushare API Token（必填）
# 从 https://tushare.pro/register.html 注册获取
TUSHARE_TOKEN=your_tushare_token_here

# 可选配置
DB_PATH=stock_strategy.db
CACHE_DIR=data_cache
'@
}

# 辅助函数：获取 README.md 内容
function Get-ReadmeContent {
    return @'
# 股票策略回测系统

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
'@
}
