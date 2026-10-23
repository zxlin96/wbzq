(() => {
    'use strict';
    const types = [
        ['stockSelection', '选股结果'], ['c154Html', 'C154', 'c154Result'],
        ['c432Html', 'C432', 'c432Result'], ['macdHtml', 'MACD', 'macdResult'],
        ['industryTrend', '行业趋势'], ['j13Trend', 'J13趋势'],
        ['sentimentRebound', '情绪反弹'], ['multiIndicator', '多指标选股']
    ];
    let reports = [], page = 1;
    const $ = id => document.getElementById(id);
    const fmt = date => /^\d{8}$/.test(date) ? `${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6)}` : date;
    function href(report, type) { return SiteUI.safeLink(report[type[0]] || (type[2] && report[type[2]])); }
    function message(text, retry = false) {
        $('reportList').replaceChildren();
        const row = $('reportList').insertRow(), cell = row.insertCell();
        cell.colSpan = 9; cell.className = 'px-6 py-8 text-center text-gray-500'; cell.textContent = text;
        $('mobileReports').textContent = text;
        if (retry) {
            for (const node of [cell, $('mobileReports')]) {
                const button = document.createElement('button');
                button.type = 'button'; button.className = 'site-retry ml-3'; button.textContent = '重试';
                button.addEventListener('click', () => load(true)); node.append(button);
            }
        }
    }
    function render() {
        const q = $('reportSearch').value.replace(/[-/\s]/g, '');
        const start = $('dateFrom').value.replace(/-/g, ''), end = $('dateTo').value.replace(/-/g, '');
        const selected = types.find(type => type[0] === $('reportType').value);
        const filtered = reports.filter(report => (!q || report.date.includes(q)) &&
            (!start || report.date >= start) && (!end || report.date <= end) && (!selected || href(report, selected)));
        const size = Number($('pageSize').value);
        const pages = Math.max(1, Math.ceil(filtered.length / size)); page = Math.min(Math.max(1, page), pages);
        const visible = filtered.slice((page - 1) * size, page * size);
        $('reportList').replaceChildren(); $('mobileReports').replaceChildren();
        if (!visible.length) message(reports.length ? '没有匹配的报告，请调整筛选条件。' : '暂无报告。');
        for (const report of visible) {
            const tr = document.createElement('tr'); tr.className = 'hover:bg-gray-50';
            const date = document.createElement('th'); date.scope = 'row'; date.className = 'px-4 py-4 font-medium whitespace-nowrap';
            date.textContent = fmt(report.date); tr.append(date);
            const card = document.createElement('article'); card.className = 'site-report-card';
            const title = document.createElement('h3'); title.textContent = fmt(report.date); card.append(title);
            const links = document.createElement('div'); links.className = 'site-report-links';
            for (const type of types) {
                const td = document.createElement('td'); td.className = 'px-4 py-4 whitespace-nowrap';
                const url = href(report, type);
                if (url) {
                    const link = document.createElement('a'); link.href = url; link.className = 'text-blue-600 hover:underline';
                    link.textContent = type[1]; link.setAttribute('aria-label', `${fmt(report.date)} ${type[1]}`);
                    td.append(link); links.append(link.cloneNode(true));
                } else { td.textContent = '暂无'; td.classList.add('text-gray-400'); }
                tr.append(td);
            }
            card.append(links); $('reportList').append(tr); $('mobileReports').append(card);
        }
        $('pageStatus').textContent = `共 ${filtered.length} 个日期 · 第 ${page} / ${pages} 页`;
        $('prevPage').disabled = page <= 1; $('nextPage').disabled = page >= pages;
        const params = new URLSearchParams();
        for (const [key, id] of [['q','reportSearch'],['from','dateFrom'],['to','dateTo'],['type','reportType']]) {
            if ($(id).value) params.set(key, $(id).value);
        }
        if (page > 1) params.set('page', page);
        if (size !== 20) params.set('size', size);
        history.replaceState(null, '', location.pathname + (params.size ? '?' + params : '') + location.hash);
    }
    async function load(reload = false) {
        message('加载中…'); $('lastUpdate').textContent = '加载中…';
        try {
            const data = await SiteUI.loadJSON('reports.json', {reload});
            if (!Array.isArray(data.reports) || data.reports.some(r => !/^\d{8}$/.test(r.date))) throw new Error('索引格式异常');
            reports = data.reports;
            for (const key of ['totalStocks', 'c154Stocks', 'c432Stocks']) $(key).textContent = data[key] ?? '—';
            $('totalReports').textContent = reports.length; $('latestDate').textContent = fmt(data.latestDate);
            $('lastUpdate').textContent = data.lastUpdate || '未知'; render();
        } catch (error) {
            $('lastUpdate').textContent = '加载失败';
            message('报告列表加载失败，请检查网络后重试。', true);
            $('pageStatus').textContent = ''; $('prevPage').disabled = true; $('nextPage').disabled = true;
        }
    }
    const params = new URLSearchParams(location.search);
    for (const [key, id] of [['q','reportSearch'],['from','dateFrom'],['to','dateTo'],['type','reportType']]) {
        if (params.has(key)) $(id).value = params.get(key);
    }
    if (['10','20','50'].includes(params.get('size'))) $('pageSize').value = params.get('size');
    page = Math.max(1, Number.parseInt(params.get('page'), 10) || 1);
    for (const id of ['reportSearch','dateFrom','dateTo','reportType','pageSize']) {
        $(id).addEventListener(id === 'reportSearch' ? 'input' : 'change', () => { page = 1; render(); });
    }
    $('prevPage').addEventListener('click', () => { page--; render(); });
    $('nextPage').addEventListener('click', () => { page++; render(); });
    window.addEventListener('popstate', () => location.reload());
    load();
})();
