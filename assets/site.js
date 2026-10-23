/* Shared report loading, chart lifecycle, and accessible modal controls. */
(() => {
    'use strict';
    const cache = new Map();
    async function loadJSON(url, {reload = false} = {}) {
        const key = new URL(url, document.baseURI).href;
        if (reload) cache.delete(key);
        if (!cache.has(key)) {
            const pending = (async () => {
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 15000);
                try {
                    const response = await fetch(key, {signal: controller.signal});
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);
                    return await response.json();
                } finally { clearTimeout(timer); }
            })();
            cache.set(key, pending);
            pending.catch(() => { if (cache.get(key) === pending) cache.delete(key); });
        }
        return cache.get(key);
    }
    window.SiteUI = {
        loadJSON,
        escape(value) {
            return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        },
        safeLink(value) {
            if (typeof value !== 'string' || !value || /^(?:[a-z]+:|\/\/|\/)|[<>"']|(?:^|\/)\.\.(?:\/|$)/i.test(value)) return null;
            return value;
        }
    };

    let request = 0, focusedBefore = null, echartsPromise = null;
    function ensureECharts() {
        if (window.echarts) return Promise.resolve();
        if (!echartsPromise) {
            echartsPromise = new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = document.body.dataset.echartsSrc;
                script.onload = () => window.echarts ? resolve() : reject(new Error('图表组件加载失败'));
                script.onerror = () => { script.remove(); reject(new Error('图表组件加载失败')); };
                document.head.append(script);
            }).catch(error => { echartsPromise = null; throw error; });
        }
        return echartsPromise;
    }
    function statusNode(modal) {
        let node = document.getElementById('chartStatus');
        if (!node) {
            node = document.createElement('div');
            node.id = 'chartStatus'; node.className = 'site-chart-status';
            node.setAttribute('role', 'status');
            modal.querySelector('.modal-content').prepend(node);
        }
        return node;
    }
    function cancel() {
        request++;
        document.body.style.overflow = '';
        if (focusedBefore?.isConnected) focusedBefore.focus();
    }
    function close() {
        const modal = document.getElementById('chartModal');
        if (modal) modal.style.display = 'none';
        cancel();
    }
    window.SiteReports = {
        cancel,
        isCurrent(data) {
            return data._request === request && document.getElementById('chartModal')?.style.display === 'block';
        },
        async loadStock(code) {
            const modal = document.getElementById('chartModal');
            if (!modal || !/^[A-Za-z0-9_.-]+$/.test(code)) return null;
            const current = ++request;
            if (modal.style.display !== 'block') focusedBefore = document.activeElement;
            modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true');
            modal.setAttribute('aria-labelledby', 'modalTitle');
            modal.tabIndex = -1; modal.style.display = 'block'; modal.setAttribute('aria-busy', 'true');
            document.body.style.overflow = 'hidden';
            document.getElementById('modalTitle').textContent = code + ' · 加载中…';
            const status = statusNode(modal);
            status.hidden = false; status.textContent = '正在加载图表数据…';
            modal.querySelectorAll('.chart-container').forEach(node => node.style.visibility = 'hidden');
            modal.querySelectorAll('#stockInfoPanel, #detailTable').forEach(node => node.style.visibility = 'hidden');
            modal.querySelector('.close')?.focus();
            try {
                const [data] = await Promise.all([
                    loadJSON(document.body.dataset.chartBase + encodeURIComponent(code) + '.json'), ensureECharts()
                ]);
                if (current !== request || modal.style.display === 'none') return null;
                if (!Array.isArray(data.dates) || !Array.isArray(data.candlestick) || !Array.isArray(data.volume)) {
                    throw new Error('图表数据格式异常');
                }
                // Older reports used both high/low orders. Present a stable input
                // to the modal scripts without changing the archived price values.
                const normalized = {...data, _request: current, candlestick: data.candlestick.map(c =>
                    c.slice(1).every(Number.isFinite)
                        ? [c[0], c[1], c[2], Math.max(c[3], c[4]), Math.min(c[3], c[4])]
                        : [c[0], null, null, null, null])};
                status.hidden = true;
                modal.setAttribute('aria-busy', 'false');
                modal.querySelectorAll('.chart-container').forEach(node => node.style.visibility = 'visible');
                modal.querySelectorAll('#stockInfoPanel, #detailTable').forEach(node => node.style.visibility = 'visible');
                return normalized;
            } catch (error) {
                if (current !== request) return null;
                modal.setAttribute('aria-busy', 'false');
                status.replaceChildren(document.createTextNode('加载失败，请检查网络后重试。 '));
                const retry = document.createElement('button');
                retry.type = 'button'; retry.textContent = '重试';
                retry.addEventListener('click', () => (window.openChart || window.showChart)(code));
                status.append(retry);
                return null;
            }
        }
    };

    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('table').forEach(table => {
            const columns = Math.max(0, ...[...table.rows].slice(0,3).map(row =>
                [...row.cells].reduce((sum, cell) => sum + cell.colSpan, 0)));
            if (columns >= 5) table.classList.add('site-wide-table');
            if (table.closest('.overflow-x-auto, .site-table-scroll')) return;
            const wrapper = document.createElement('div');
            wrapper.className = 'site-table-scroll';
            table.before(wrapper); wrapper.append(table);
        });
        document.querySelectorAll('th[onclick]').forEach(header => {
            header.tabIndex = 0;
            header.setAttribute('aria-sort', 'none');
            header.addEventListener('keydown', event => {
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); header.click(); }
            });
            header.addEventListener('click', () => queueMicrotask(() => {
                header.closest('thead').querySelectorAll('th').forEach(node => node.setAttribute('aria-sort',
                    node.classList.contains('sort-desc') ? 'descending' : node.classList.contains('sort-asc') ? 'ascending' : 'none'));
            }));
        });
        document.querySelectorAll('tr[onclick]').forEach(row => {
            row.tabIndex = 0;
            row.setAttribute('aria-label', '查看图表：' + [...row.cells].slice(0,2).map(cell => cell.textContent.trim()).join(' '));
            row.addEventListener('keydown', event => {
                if (event.target === row && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); row.click(); }
            });
        });
        const modal = document.getElementById('chartModal');
        if (modal) {
            modal.addEventListener('click', event => { if (event.target === modal) close(); });
            document.addEventListener('keydown', event => {
                if (modal.style.display !== 'block') return;
                if (event.key === 'Escape') { event.preventDefault(); close(); }
                if (event.key === 'Tab') {
                    const nodes = [...modal.querySelectorAll('button, a[href], input, select, [tabindex="0"]')]
                        .filter(node => node.getClientRects().length && !node.disabled);
                    if (!nodes.length) { event.preventDefault(); modal.focus(); return; }
                    const first = nodes[0], last = nodes[nodes.length - 1];
                    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
                    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
                }
            });
        }
        let frame = 0;
        const resize = () => {
            if (frame) return;
            frame = requestAnimationFrame(() => {
                frame = 0;
                document.querySelectorAll('[_echarts_instance_]').forEach(node => {
                    if (node.clientWidth && node.clientHeight) window.echarts?.getInstanceByDom(node)?.resize();
                });
            });
        };
        if (window.ResizeObserver) {
            const observer = new ResizeObserver(resize);
            document.querySelectorAll('.chart-container, [id^="chart"], [id$="Chart"]').forEach(node => observer.observe(node));
        }
        window.addEventListener('resize', resize);
    });
})();
