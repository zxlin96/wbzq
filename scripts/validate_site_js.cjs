/* Parse all public inline and shared scripts without executing market logic. */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
function walk(dir) {
    return fs.readdirSync(dir, {withFileTypes:true}).flatMap(entry =>
        entry.isDirectory() ? walk(path.join(dir,entry.name)) : [path.join(dir,entry.name)]);
}
const root = path.resolve(process.argv[2] || '.');
const legacy = fs.readdirSync(root).filter(name => /^(?:stock_selection_\d{8}|daily_first_j13_interactive|first_j13_step_daily_count|industry_total_amount_trend)\.html$/.test(name)).map(name => path.join(root,name));
const pages = [path.join(root,'index.html'), ...legacy, ...walk(path.join(root,'html')).filter(p => p.endsWith('.html'))];
const errors = []; let scripts = 0;
for (const page of pages) {
    const text = fs.readFileSync(page,'utf8');
    for (const match of text.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
        if (/\bsrc\s*=|type\s*=\s*["']application\/(?:ld\+)?json/.test(match[1]) || !match[2].trim()) continue;
        scripts++;
        try { new vm.Script(match[2], {filename: page}); }
        catch (error) { errors.push(`${path.relative(root,page)}: ${error.message}`); }
    }
}
for (const file of walk(path.join(root,'assets')).filter(p => p.endsWith('.js'))) {
    scripts++;
    try { new vm.Script(fs.readFileSync(file,'utf8'), {filename:file}); }
    catch (error) { errors.push(`${path.relative(root,file)}: ${error.message}`); }
}
console.log(JSON.stringify({pages:pages.length,scripts,errors},null,2));
process.exitCode = errors.length ? 1 : 0;
