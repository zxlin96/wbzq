        function formatDate(d) {
            if (!d || d.length !== 8) return d;
            return d.substring(0, 4) + '-' + d.substring(4, 6) + '-' + d.substring(6, 8);
        }

        function renderCard(etf) {
            const escape = SiteUI.escape;
            const cleanColor = value => /^#[0-9a-f]{6}$/i.test(value || '') ? value : '#6b7280';
            const safeHref = SiteUI.safeLink(etf.report_file);
            etf = {...etf, name: escape(etf.name), ts_code: escape(etf.ts_code),
                action_label: escape(etf.action_label), action_detail: escape(etf.action_detail),
                action_color: cleanColor(etf.action_color),
                positions: (etf.positions || []).map(p => ({...p, action_label: escape(p.action_label),
                    action_detail: escape(p.action_detail), action_color: cleanColor(p.action_color)}))};
            for (const field of ['return_pct','trade_count','total_invested','last_price']) {
                if (typeof etf[field] !== 'number' || !Number.isFinite(etf[field])) etf[field] = 0;
            }
            var color = etf.action_color || '#95a5a6';
            var bgColor = color + '15';
            var hasPosition = etf.shares && etf.shares > 0;
            var returnColor = etf.return_pct >= 0 ? 'text-red-600' : 'text-green-600';
            var returnSign = etf.return_pct >= 0 ? '+' : '';
            var positions = etf.positions || [];
            var multiPos = positions.length > 1;

            var metricsHtml = '';
            metricsHtml += '<span class="metric-pill">J=' + (etf.weekly_j || 0).toFixed(1) + '</span>';
            metricsHtml += '<span class="metric-pill">K=' + (etf.weekly_k || 0).toFixed(1) + '</span>';
            metricsHtml += '<span class="metric-pill">D=' + (etf.weekly_d || 0).toFixed(1) + '</span>';

            if (hasPosition) {
                var pnlVal = etf.loss_pct || 0;
                var pnlLabel = pnlVal >= 0 ? '浮盈' : '浮亏';
                var pnlColor = pnlVal >= 0 ? 'text-red-600' : 'text-green-600';
                metricsHtml += '<span class="metric-pill ' + pnlColor + '">' + pnlLabel + Math.abs(pnlVal).toFixed(2) + '%</span>';
            }

            var positionHtml = '';
            if (hasPosition) {
                positionHtml = '<div class="flex items-center gap-4 mt-3 text-sm text-gray-600">'
                    + '<span>总持仓: ' + etf.shares.toFixed(0) + '份</span>'
                    + (etf.avg_cost ? '<span>成本: ' + etf.avg_cost.toFixed(4) + '</span>' : '')
                    + '</div>';
            }

            // 标签区域：多仓位时显示每个仓位的独立标签
            var badgesHtml = '';
            if (multiPos) {
                // 多仓位：每个仓位一个标签 + 详情
                for (var i = 0; i < positions.length; i++) {
                    var p = positions[i];
                    var pColor = p.action_color || '#95a5a6';
                    var pBgColor = pColor + '15';
                    var pLabel = p.action_label || ('R' + p.round_id);
                    var pDetail = p.action_detail || '';
                    var pShares = p.shares ? p.shares.toFixed(0) + '份' : '';
                    var pCost = p.avg_cost ? '成本' + p.avg_cost.toFixed(4) : '';

                    badgesHtml += '<div class="pos-row">'
                        + '<span class="action-badge-sm" style="background:' + pBgColor + ';color:' + pColor + ';">'
                        + '<span class="pulse-dot" style="background:' + pColor + ';"></span>'
                        + pLabel
                        + '</span>'
                        + '<div class="flex-1 min-w-0">'
                        + '<div class="text-xs text-gray-600">' + pDetail + '</div>'
                        + '<div class="pos-meta">' + pShares + (pCost ? ' · ' + pCost : '') + '</div>'
                        + '</div>'
                        + '</div>';
                }
            } else if (positions.length === 1) {
                // 单仓位：显示主标签
                var p0 = positions[0];
                badgesHtml = '<div class="mb-3">'
                    + '<span class="action-badge" style="background:' + bgColor + ';color:' + color + ';">'
                    + '<span class="pulse-dot" style="background:' + color + ';"></span>'
                    + (p0.action_label || etf.action_label || '未知')
                    + '</span>'
                    + '</div>'
                    + '<p class="text-sm text-gray-500 leading-relaxed">' + (p0.action_detail || etf.action_detail || '') + '</p>';
            } else {
                // 空仓
                badgesHtml = '<div class="mb-3">'
                    + '<span class="action-badge" style="background:' + bgColor + ';color:' + color + ';">'
                    + '<span class="pulse-dot" style="background:' + color + ';"></span>'
                    + (etf.action_label || '未知')
                    + '</span>'
                    + '</div>'
                    + '<p class="text-sm text-gray-500 leading-relaxed">' + (etf.action_detail || '') + '</p>';
            }

            var totalReturnHtml = '<div class="flex items-center justify-between mt-4 pt-3 border-t border-gray-100">'
                + '<div>'
                + '<div class="text-xs text-gray-400">总收益</div>'
                + '<div class="text-lg font-bold ' + returnColor + '">' + returnSign + etf.return_pct.toFixed(2) + '%</div>'
                + '</div>'
                + '<div class="text-right">'
                + '<div class="text-xs text-gray-400">交易 ' + etf.trade_count + ' 次</div>'
                + '<div class="text-xs text-gray-400">投入 ' + (etf.total_invested / 10000).toFixed(1) + ' 万</div>'
                + '</div>'
                + '</div>';

            return '<a href="' + escape(safeHref || '#') + '" class="etf-card bg-white rounded-xl shadow-md overflow-hidden">'
                + '<div class="p-5">'
                + '<div class="flex items-start justify-between mb-3">'
                + '<div>'
                + '<h3 class="text-lg font-bold text-gray-900">' + etf.name + '</h3>'
                + '<p class="text-xs text-gray-400 mt-0.5">' + (etf.ts_code || '') + ' · 截至 ' + formatDate(String(etf.last_date)) + '</p>'
                + '</div>'
                + '<div class="text-right">'
                + '<div class="text-xl font-bold text-gray-800">' + (etf.last_price || 0).toFixed(4) + '</div>'
                + '</div>'
                + '</div>'
                + badgesHtml
                + '<div class="flex flex-wrap gap-1 mt-2">' + metricsHtml + '</div>'
                + positionHtml
                + totalReturnHtml
                + '</div>'
                + '</a>';
        }

        async function loadSummary(reload = false) {
            try {
                var data = await SiteUI.loadJSON('dca_summary.json', {reload});
                if (!Array.isArray(data.etf_list)) throw new Error('汇总格式异常');

                document.getElementById('lastUpdate').textContent = data.lastUpdate;

                var grid = document.getElementById('etfGrid');
                if (!data.etf_list || data.etf_list.length === 0) {
                    grid.innerHTML = '<div class="col-span-full text-center text-gray-500 py-12">暂无ETF数据</div>';
                    return;
                }

                var sorted = data.etf_list.slice().sort(function(a, b) {
                    var order = { buy: 0, sell_third: 1, sell_third_pullback: 2, v6h_stage_1: 1, v6h_stage_2: 2, wait_price_below_dk: 3, wait_price_below_mid: 4, wait_dead_cross: 5, wait_golden: 6, hold: 7, v6h_stage_3: 7, wait: 8 };
                    return (order[a.action] ?? 99) - (order[b.action] ?? 99);
                });

                grid.innerHTML = sorted.map(renderCard).join('');
            } catch (err) {
                document.getElementById('etfGrid').innerHTML =
                    '<div class="col-span-full text-center text-red-500 py-12">加载数据失败，请检查网络。 <button type="button" class="site-retry" onclick="loadSummary(true)">重试</button></div>';
                document.getElementById('lastUpdate').textContent = '加载失败';
            }
        }

        document.addEventListener('DOMContentLoaded', loadSummary);
