# GitHub Pages 自动部署指南

## 概述

本仓库使用 GitHub Actions 自动生成股票策略报告，并通过 GitHub Pages 部署为静态网站，方便用户在线查看。

## 功能特性

- ✅ 按工作流计划自动运行策略，页面显示实际生成时间（北京时间）
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
- 按 `cron` 计划运行，实际开始时间以 Actions 记录为准
- 生成选股结果和趋势图
- 生成报告索引 `reports.json`
- 上传结果为 Artifacts

### deploy-pages.yml
- 监听 `stock-strategy.yml` 成功完成事件，或手动触发
- 检出报告分支，构建并校验独立的 `dist/` 发布目录
- 仅将公开页面、图表 JSON、静态资源及索引引用的 CSV 部署到 GitHub Pages

### static-site-check.yml
- 在 Pull Request 或手动触发时执行生成器回归测试
- 校验全部页面的链接、脚本语法、索引字段和个股数据
- 用 Chromium 验证历史图表、筛选分页、手机布局、缓存和失败重试

## 本地构建和验证

Python 3.10+，安装项目依赖后执行：

```bash
npm ci --ignore-scripts --no-audit --no-fund
python static_site.py optimize
python generate_reports_json.py
npm run build:css
python -m unittest discover -s test -p 'test_static*.py' -v
python static_site.py build
python scripts/validate_static_site.py --root dist
node scripts/validate_site_js.cjs dist
```

浏览器回归使用 Playwright：

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/test_static_site_browser.py --root dist --output test-results
```

该测试启动仅监听本机的临时 HTTP 服务，并使用 `/project/` 子路径验证 GitHub Pages 的相对链接。结果和截图写入输出目录。

使用 `python -m http.server 8000 --directory dist` 预览，再打开 `http://localhost:8000/`；页面通过 HTTP 加载 JSON。

## 资源与报告生成约定

报告生成器通过 `static_site.write_report()` 保存页面。历史文件可重复执行 `optimize` 迁移；同一版本再次执行不会改变页面。股票价格、选股结果和历史明细保留原值，非有限指标值以标准 JSON 的 `null` 表示空缺。

发布目录兼容旧版根目录的选股与趋势报告地址。旧报告重复内嵌的 Plotly 3.1.0 已提取为同一份本地资源，保留原许可证和代码；多个旧报告可复用浏览器缓存。

每个股票报告旁的 `同名.data/` 目录保存个股 JSON 和 `manifest.json`。发布时应完整保留该目录。首次打开报告只加载表格，点击股票后加载对应 JSON 与本地 ECharts；同一页面内再次查看使用缓存。弹窗统一管理缩放、Esc、焦点和请求状态。

Tailwind 固定为 3.4.17，编译结果位于 `assets/tailwind.css`；扫描 HTML、前端脚本和 Python 模板。新增样式后运行 `npm run build:css`。ECharts 固定为 5.5.0，文件及许可证位于 `assets/vendor/`。依赖版本记录在 `package-lock.json`。

`reports.json` 使用 schemaVersion 2，覆盖主选股、C154、C432、MACD、行业、J13、情绪反弹及多指标报告。最新日期缺少某类输出时数量为 `null`，首页显示 `—`；成功生成但无入选股票时数量为 `0`。

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
- 检查部署工作流权限：`contents: read`、`pages: write` 和 `id-token: write`
- 检查发布前校验输出；修复缺失链接、损坏 JSON 或脚本错误后再部署

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
