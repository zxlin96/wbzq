# GitHub Pages 自动部署指南

## 概述

本仓库使用 GitHub Actions 自动生成股票策略报告，并通过 GitHub Pages 部署为静态网站，方便用户在线查看。

## 功能特性

- ✅ 每日 21:30 (北京时间) 自动运行策略
- ✅ 自动生成选股结果 HTML 报告
- ✅ 自动部署到 GitHub Pages
- ✅ 支持查看历史报告
- ✅ 交互式图表（K线、成交额、KDJ、多空指标）

## 部署步骤

### 1. 启用 GitHub Pages

进入仓库设置页面：
```
https://github.com/你的用户名/wbzq/settings/pages
```

配置如下：
- **Source**: 选择 `GitHub Actions`
- **Build and deployment**: 选择 `Deploy to GitHub Pages` 工作流

### 2. 配置 Secrets

在仓库设置中添加 Tushare Token：
```
Settings → Secrets and variables → Actions → New repository secret
Name: TUSHARE_TOKEN
Value: 你的 Tushare Token
```

### 3. 推送代码

推送代码到 GitHub，Actions 会自动运行：
```bash
git add .
git commit -m "Setup GitHub Pages deployment"
git push origin main
```

### 4. 手动触发部署（可选）

在 GitHub Actions 页面选择 `Deploy to GitHub Pages` 工作流，点击 `Run workflow` 按钮手动触发。

## 访问地址

部署成功后，访问：
```
https://你的用户名.github.io/wbzq/
```

## 工作流说明

### stock-strategy.yml
- 每日 21:30 (北京时间) 自动运行
- 生成选股结果和趋势图
- 生成报告索引 `reports.json`
- 上传结果为 Artifacts

### deploy-pages.yml
- 监听 `stock-strategy.yml` 完成事件
- 下载 Artifacts 中的报告文件
- 自动部署到 GitHub Pages

## 文件结构

```
wbzq/
├── .github/
│   └── workflows/
│       ├── stock-strategy.yml      # 主策略工作流
│       └── deploy-pages.yml        # 部署工作流
├── main_par2.py                  # 主程序
├── generate_stock_html.py         # HTML 报告生成
├── generate_reports_json.py       # 索引生成
├── index.html                   # 首页
├── reports.json                 # 报告索引（自动生成）
└── stock_selection_YYYYMMDD.html # 选股报告
```

## 注意事项

1. **首次部署可能需要几分钟时间**
2. **GitHub Pages 有 1GB 存储限制**
3. **Artifacts 保留 30 天**
4. **确保仓库设置为 Public（公开）**

## 故障排查

### 部署失败
- 检查 `Settings → Pages` 是否正确配置
- 检查 Actions 权限：`contents: write` 和 `pages: write`

### 页面无法访问
- 等待 1-2 分钟，GitHub Pages 部署需要时间
- 检查仓库是否为 Public

### 报告未更新
- 检查 Actions 是否成功运行
- 查看 Actions 日志排查错误

## 高级配置

### 自定义域名
在 `Settings → Pages` → Custom domain 中配置自定义域名。

### 修改更新时间
编辑 `.github/workflows/stock-strategy.yml` 中的 `cron` 表达式。

### 修改保留天数
编辑 `stock-strategy.yml` 中的 `retention-days` 参数。
