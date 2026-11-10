"""A standalone, escaped HTML snapshot: no CDN, server, player or fetch calls."""

import base64
import hashlib
import json
from pathlib import Path


def render_dashboard(report: dict) -> str:
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    payload = (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    html = _TEMPLATE.replace("__REPORT_JSON__", payload).replace(
        "__EDITOR_SCRIPT__", Path(__file__).with_name("editor.js").read_text(encoding="utf-8")
    )
    script = html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return html.replace("script-src 'unsafe-inline'", f"script-src 'sha256-{digest}'")


_TEMPLATE = """<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="color-scheme" content="dark light">
<title>IPTV · گزارش سلامت کانال‌ها</title>
<style>
:root{--bg:#0c111b;--surface:#141d2b;--line:#28354a;--muted:#a2b2c9;--text:#edf3ff;--accent:#77d9c1;--warn:#f6cc7b;--bad:#ff9eae;--radius:14px}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.8 system-ui,-apple-system,sans-serif}main{max-width:1440px;margin:auto;padding:32px 28px 60px}
header{display:flex;justify-content:space-between;gap:22px;align-items:center;border-bottom:1px solid var(--line);padding-bottom:24px}.brand{font:700 13px/1.5 monospace;color:var(--accent);letter-spacing:2px}h1{font-size:27px;margin:8px 0}h2{font-size:18px;margin:0 0 14px}h3{font-size:15px}p{margin:5px 0}.muted,small{color:var(--muted)}.stamp{border:1px solid var(--line);padding:10px 16px;border-radius:var(--radius);white-space:nowrap;font-size:12px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}button,input,select{font:inherit;color:inherit;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:9px 12px}button{cursor:pointer}button:hover{border-color:var(--accent)}button:disabled{opacity:.35;cursor:default}input:focus,select:focus,button:focus{outline:2px solid var(--accent);outline-offset:2px}
.notice{background:#182333;border-right:3px solid var(--accent);border-radius:6px;padding:12px 16px;margin:22px 0;font-size:13px}.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin:24px 0}.card{border:1px solid var(--line);border-radius:var(--radius);padding:18px;background:var(--surface)}.number{font:600 29px/1.4 system-ui}.label{color:var(--muted);font-size:12px}
.panel{border:1px solid var(--line);border-radius:var(--radius);padding:22px;background:var(--surface);margin:18px 0}.downloads{display:flex;flex-wrap:wrap;gap:8px}.download{font-size:13px;padding:6px 12px;border:1px solid var(--line);border-radius:7px}.toolbar{display:flex;flex-wrap:wrap;gap:9px;margin:18px 0}.toolbar input{flex:1;min-width:210px}.toolbar select{max-width:230px;font-size:13px}.count{font-size:12px;color:var(--muted);margin-bottom:12px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;text-align:right;font-size:13px}th{color:var(--muted);font-weight:500;border-bottom:1px solid var(--line);padding:10px;white-space:nowrap}td{padding:12px 10px;border-bottom:1px solid #243044;vertical-align:top}tbody tr:last-child td{border-bottom:0}td:first-child{min-width:220px}td small{display:block;font:11px/1.8 monospace;direction:ltr;text-align:right}.badge{display:inline-block;font-size:11px;white-space:nowrap;border-radius:5px;padding:2px 7px;background:#253248;color:var(--muted)}.good{background:#1a3d37;color:var(--accent)}.warn{background:#443b29;color:var(--warn)}.bad{background:#432b36;color:var(--bad)}.tiny{font-size:11px}.pagination{display:flex;align-items:center;justify-content:center;gap:16px;margin-top:18px}.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}.two .panel{margin:0}details{border-bottom:1px solid var(--line);padding:12px 0}summary{cursor:pointer;color:var(--muted)}ul{padding-right:20px}li{margin:6px 0;overflow-wrap:anywhere}.ltr{direction:ltr;unicode-bidi:plaintext;text-align:left;word-break:break-all}.empty{padding:25px;text-align:center;color:var(--muted)}footer{margin-top:24px;font-size:12px;color:var(--muted)}dialog{background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:var(--radius);max-width:700px;width:90%;padding:24px}dialog::backdrop{background:#0009}dl{display:grid;grid-template-columns:110px 1fr;gap:9px}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
@media(max-width:1000px){.cards{grid-template-columns:repeat(3,1fr)}.two{grid-template-columns:1fr}}@media(max-width:600px){main{padding:20px 14px}header{display:block}.stamp{display:inline-block;margin-top:14px}h1{font-size:23px}.cards{grid-template-columns:repeat(2,1fr)}.panel{padding:15px}.toolbar select{flex:1;max-width:100%}}
</style>
</head>
<body><main>
<header><div><div class="brand">IPTV / HEALTH SNAPSHOT</div><h1>کانال‌ها، روشن و قابل‌بررسی</h1><p class="muted">فهرست کانال‌ها، سلامت منابع و تغییرات آخرین انتشار</p></div><div class="stamp">آخرین تغییر محتوایی<br><span id="published" dir="ltr"></span><br>آخرین اجرای ساخت<br><span id="last-run" dir="ltr"></span></div></header>
<div class="notice">این صفحه گزارش ثابت است، نه پخش‌کننده یا مانیتور لحظه‌ای. «سالم» یعنی پاسخ ساختاری معتبر در همان بررسی کوتاه، نه تضمین پخش. نتیجهٔ کش، بررسی تازه نیست. زمان آخرین اجرا با زمان آخرین تغییر محتوایی تفاوت دارد. امتیاز پایداری از مشاهدات روزانه است، نه آپ‌تایم ۲۴ ساعته.</div>
<p id="site-notice" class="notice" aria-live="polite"></p>
<section class="cards" id="cards" aria-label="خلاصهٔ ساخت"></section>
<section class="panel"><h2>دریافت پلی‌لیست</h2><div class="downloads" id="downloads"></div><p class="muted tiny" id="favorites"></p></section>
<section class="panel"><h2>کاوش کانال‌ها</h2><div class="toolbar"><input id="search" type="search" placeholder="جست‌وجوی نام، شناسه، گروه یا منبع…" aria-label="جست‌وجوی کانال"><select id="group" aria-label="گروه"><option value="">همهٔ گروه‌ها</option></select><select id="country" aria-label="کشور"><option value="">همهٔ کشورها</option></select><select id="language" aria-label="زبان"><option value="">همهٔ زبان‌ها</option></select><select id="health" aria-label="سلامت"><option value="">همهٔ وضعیت‌ها</option><option value="healthy">پاسخ معتبر</option><option value="cached">نتیجهٔ کش</option><option value="unchecked">بررسی‌نشده</option><option value="unverified">تأییدنشده</option></select><select id="epg" aria-label="راهنمای برنامه"><option value="">همهٔ وضعیت‌های EPG</option><option value="covered">برنامهٔ معتبر</option><option value="missing">بدون پوشش معتبر</option></select></div>
<div id="count" class="count" aria-live="polite"></div><div class="scroll"><table><thead><tr><th>کانال / شناسه</th><th>گروه</th><th>کشور / زبان</th><th>کیفیت</th><th>وضعیت پاسخ</th><th>سن بررسی در این گزارش</th><th>EPG</th></tr></thead><tbody id="channels"></tbody></table></div><div class="pagination"><button id="prev">قبلی</button><span id="page" class="tiny"></span><button id="next">بعدی</button></div></section>
<section class="panel" id="personal-editor"><h2>فهرست شخصی، فقط در مرورگر شما</h2>
<p class="muted tiny">با ستارهٔ کنار نام کانال علاقه‌مندی انتخاب کنید. هیچ انتخابی به سرور یا مخزن فرستاده نمی‌شود؛ پس از بستن صفحه نگه‌داری نمی‌شود. خروجی تنظیمات را برای استفاده در ساخت بعدی دانلود کنید. استریم خصوصی یا دارای توکن عمداً در دادهٔ ویرایشگر قرار نمی‌گیرد.</p>
<div class="toolbar"><label>پروفایل <select id="edit-profile" aria-label="پروفایل موجود"></select></label><label>نام فایل تنظیمات <input id="edit-name" value="personal" maxlength="40" dir="ltr" aria-label="نام پروفایل"></label></div>
<div class="toolbar"><label>زبان‌ها (انتخاب چندتایی)<br><select multiple size="4" id="edit-language" aria-label="زبان‌های فهرست شخصی"></select></label><label>کشورها (انتخاب چندتایی)<br><select multiple size="4" id="edit-country" aria-label="کشورهای فهرست شخصی"></select></label><label>حداکثر ارتفاع تصویر<br><select id="edit-resolution"><option value="0">بدون سقف</option value="2160">2160p</option value="1080">1080p</option value="720">720p</option><option value="480">480p</option><option value="360">360p</option></select></label><label>حداکثر بیت‌ریت، kbps (صفر: بدون سقف)<br><input id="edit-bitrate" type="number" min="0" max="1000000" value="0" step="1" dir="ltr"></label></div>
<div class="toolbar"><label><input type="checkbox" id="edit-favorites-only"> فقط علاقه‌مندی‌ها</label><label><input type="checkbox" id="edit-unknown-quality"> پذیرش کیفیت نامشخص زیر سقف</label><label><input type="checkbox" id="edit-unknown-bitrate"> پذیرش بیت‌ریت نامشخص زیر سقف</label><label><input type="checkbox" id="edit-unchecked"> پذیرش لینک بررسی‌نشده (بدون تأیید سلامت)</label></div>
<p class="muted tiny">بیت‌ریت از اعلام معتبر HLS گرفته می‌شود و ممکن است تغییر کند؛ سقف، تضمین ترافیک نیست. لینک واقعی کم‌کیفیت‌تر انتخاب می‌شود؛ تبدیل کیفیت انجام نمی‌شود. نبود انتخاب در زبان/کشور یعنی همه.</p>
<p id="editor-status" role="status" class="count"></p><p id="editor-error" role="alert" class="warn"></p><div class="toolbar"><button id="download-favorites">دانلود favorites.json</button><button id="download-profile">دانلود profiles.json</button><button id="download-personal">دانلود personal.m3u</button><label>ورود علاقه‌مندی‌های قبلی <input id="import-favorites" type="file" accept=".json,application/json"></label></div></section>
<div class="two"><section class="panel"><h2>تغییرات کانال‌ها</h2><p class="muted tiny">تغییر آدرس همان کانال، اضافه/حذف شدن کانال نیست. کیفیت فقط وقتی قابل‌مقایسه باشد گزارش می‌شود.</p><div id="changes"></div></section><section class="panel"><h2>منابع ورودی</h2><div id="sources"></div></section><section class="panel"><h2>فراداده و راهنمای برنامه</h2><div id="metadata"></div><div id="guides"></div></section><section class="panel"><h2>نمونهٔ بررسی واقعی پخش</h2><div id="playback"></div></section></div>
<footer>گزارش محلی و مستقل · بدون وابستگی خارجی، بارگیری خودکار استریم یا ارسال داده · جزئیات ماشینی: <a href="build_report.json">build_report.json</a> · گزارش متنی: <a href="build_stats.txt">build_stats.txt</a></footer>
</main><dialog id="detail"><h2 id="detail-title"></h2><dl id="detail-body"></dl><button id="close-detail">بستن</button></dialog>
<script id="report" type="application/json">__REPORT_JSON__</script>
<script>
'use strict';
const report=JSON.parse(document.getElementById('report').textContent);
const rows=report.channel_catalog||[], $=id=>document.getElementById(id);
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=String(text);if(cls)el.className=cls;return el}
function badge(text,cls){return node('span',text,'badge '+(cls||''))}
function date(value){if(!value)return 'نامشخص';const d=new Date(value);return Number.isNaN(d.getTime())?'نامشخص':d.toLocaleString('en-GB',{timeZone:'UTC'})+' UTC'}
function age(value){if(!value)return 'بررسی نشده';const seconds=(Date.now()-new Date(value))/1000;if(!Number.isFinite(seconds)||seconds<0)return 'نامشخص';if(seconds<3600)return Math.floor(seconds/60)+' دقیقه';if(seconds<86400)return Math.floor(seconds/3600)+' ساعت';return Math.floor(seconds/86400)+' روز'}
$('published').textContent=date(report.generated_at);
$('last-run').textContent=report.last_run?date(report.last_run.generated_at):'در این تصویر ثبت نشده';
$('site-notice').textContent=report.site&&report.site.preview?'پیش‌نمایش آزمایشیِ نمونهٔ محدود؛ این صفحه انتشار یا جایگزینی پلی‌لیست اصلی نیست.':report.last_run?'آخرین وضعیت اجرا: '+report.last_run.status+' · انتشار خودکار صفحه پس از ساخت موفق؛ بدون نیاز به کامیت روزانهٔ گزارش.':'این فایل یک تصویر آفلاین است؛ برای اجرای جدیدتر گزارش Actions را بررسی کنید.';
if(report.site&&report.site.omitted_sensitive_streams)$('site-notice').textContent+=' لینک دارای اطلاعات حساس از دانلودهای عمومی این صفحه کنار گذاشته شده است.';
for(const [value,label] of [[report.channels,'کانال منتشرشده'],[report.checked_links,'درخواست بررسی‌شده در این اجرا'],[report.cached_links||0,'پاسخ سالم از کش'],[report.added||0,'کانال جدید'],[report.removed||0,'کانال حذف‌شده'],[report.recovered||0,'کانال بازیابی‌شده']]){const card=node('div',undefined,'card');card.append(node('div',value,'number'),node('div',label,'label'));$('cards').append(card)}
const downloads=[['playlist.m3u','همهٔ کانال‌ها'],['persian.m3u','فارسی'],['news.m3u','اخبار'],['music.m3u','موسیقی'],['sports.m3u','ورزش'],['movies.m3u','فیلم'],['kids.m3u','کودک'],['favorites.m3u','علاقه‌مندی‌ها']];
for(const [name,profile] of Object.entries((report.personalization||{}).profiles||{}))if(/^profile-[a-z0-9][a-z0-9-]{0,39}\\.m3u$/.test(profile.file))downloads.push([profile.file,name+' · '+profile.channels]);
for(const [file,label] of downloads){const a=node('a',label,'download');a.href=file;a.setAttribute('download',file);$('downloads').append(a)}
const personal=report.personalization||{};$('favorites').textContent='علاقه‌مندی‌های موجود: '+(personal.favorites||0)+' · شناسه‌های پیدا‌نشده: '+(personal.missing_favorites||[]).join('، ');
for(const key of ['group','country','language'])for(const value of [...new Set(rows.map(r=>r[key]).filter(Boolean))].sort()){const option=node('option',value);option.value=value;$(key).append(option)}
const healthLabels={healthy:'پاسخ معتبر',unchecked:'بررسی‌نشده',timeout:'مهلت تمام شد',restricted:'دسترسی محدود',http_error:'خطای HTTP',network_error:'خطای شبکه',invalid:'پاسخ نامعتبر'};
const epgLabels={covered:'برنامهٔ معتبر',missing_id:'شناسه پیدا نشد',no_id:'بدون شناسه',no_programmes:'بدون برنامه',stale:'منقضی',future:'فقط آیندهٔ دور',invalid:'نامعتبر',unchecked:'بررسی‌نشده'};
function details(row){$('detail-title').textContent=row.name;const body=$('detail-body');body.replaceChildren();for(const [label,value] of [['شناسه',row.identity],['منبع',row.source],['آدرس پخش',row.url],['نتیجه',row.reason],['زمان بررسی',date(row.checked_at)],['نتیجه از کش',row.cached?'بله؛ بررسی تازه نیست':'خیر'],['بیت‌ریت',row.bitrate?(row.bitrate/1000)+' kbps · '+row.bitrate_source:'نامشخص؛ از کیفیت تصویر حدس زده نمی‌شود'],['پایداری مشاهدات',row.reliability?row.reliability.score+'/100 · '+row.reliability.observed_days+' روز مشاهده‌شده از ۳۰ روز':'هنوز دادهٔ کافی نیست']])body.append(node('dt',label),node('dd',value,'ltr'));$('detail').showModal()}
__EDITOR_SCRIPT__
let page=0,filtered=[],size=100;
function draw(){const query=$('search').value.trim().toLocaleLowerCase();filtered=rows.filter(r=>{const h=$('health').value;return(!query||[r.name,r.identity,r.source,r.group].join(' ').toLocaleLowerCase().includes(query))&&['group','country','language'].every(k=>!$(k).value||r[k]===$(k).value)&&(!h||(h==='cached'?r.cached:h==='unverified'?!['healthy','unchecked'].includes(r.status):h==='healthy'?r.status==='healthy'&&!r.cached:r.status===h))&&(!$('epg').value||($('epg').value==='covered'?r.epg==='covered':r.epg!=='covered'))});
const max=Math.max(1,Math.ceil(filtered.length/size));page=Math.min(page,max-1);const body=$('channels');body.replaceChildren();
for(const row of filtered.slice(page*size,(page+1)*size)){const tr=node('tr');const name=node('td');const button=node('button',row.name);button.addEventListener('click',()=>details(row));name.append(favoriteButton(row),button,node('small',row.identity));tr.append(name,node('td',row.group),node('td',(row.country||'—')+' / '+(row.language||'—')),node('td',row.resolution?row.resolution+'p':'نامشخص'));const status=node('td');status.append(badge(row.cached?'سالم · کش':healthLabels[row.status]||row.status,row.status==='healthy'?(row.cached?'warn':'good'):'warn'));const epg=node('td');epg.append(badge(epgLabels[row.epg]||row.epg,row.epg==='covered'?'good':''));tr.append(status,node('td',age(row.checked_at)),epg);body.append(tr)}
if(!filtered.length){const td=node('td','کانالی با این فیلتر پیدا نشد.','empty');td.colSpan=7;const tr=node('tr');tr.append(td);body.append(tr)}$('count').textContent=filtered.length+' از '+rows.length+' کانال · برای جزئیات روی نام کانال بزنید.';$('page').textContent=(page+1)+' / '+max;$('prev').disabled=page===0;$('next').disabled=page>=max-1}
for(const key of ['search','group','country','language','health','epg'])$(key).addEventListener(key==='search'?'input':'change',()=>{page=0;draw()});$('prev').addEventListener('click',()=>{page--;draw()});$('next').addEventListener('click',()=>{page++;draw()});$('close-detail').addEventListener('click',()=>$('detail').close());draw();
for(const [key,label] of [['added_channels','جدید'],['removed_channels','حذف‌شده'],['url_changes','تغییر آدرس یا هدر'],['quality_changes','تغییر کیفیت'],['recovered_channels','بازیابی‌شده'],['returned_unverified_channels','بازگشت بدون بررسی تازهٔ سالم']]){const items=(report.changes||{})[key]||[];const d=node('details'),summary=node('summary',label+' · '+items.length);d.append(summary);const list=node('ul');for(const item of items.slice(0,200))list.append(node('li',item.name+(key==='quality_changes'?' · '+item.before+'p → '+item.after+'p':'')+' · '+item.identity));if(items.length>200)list.append(node('li','موارد بیشتر در گزارش JSON موجود است.'));d.append(list);$('changes').append(d)}
for(const source of report.sources||[]){const d=node('details');d.append(node('summary',source.status==='fresh'?'دریافت تازه':source.status==='cached'?'کش معتبر':'ناموفق'),node('p',source.url,'ltr tiny'));if(source.error)d.append(node('p',source.error,'tiny muted'));$('sources').append(d)}
const meta=report.metadata||{};$('metadata').append(node('p','مطابقت با مرجع: '+(meta.matched||0)+' · بدون تطبیق: '+(meta.missing||0)+' · نام‌های مبهم: '+(meta.ambiguous||0)),node('p','تطبیق خودکار با شناسه انجام می‌شود؛ نام‌های مشابه حدس زده نمی‌شوند.','muted tiny'));
const epg=report.epg||{};$('guides').append(node('p',epg.enabled?'پوشش EPG: '+epg.covered+' · بدون برنامهٔ معتبر: '+epg.missing:'اعتبارسنجی EPG غیرفعال است.'));for(const guide of epg.guides||[]){const d=node('details');d.append(node('summary',guide.status+' · '+guide.channels+' شناسه'),node('p',guide.url,'ltr tiny'),node('p',JSON.stringify(guide.coverage||{}),'ltr tiny'));if(guide.error)d.append(node('p',guide.error,'tiny muted'));$('guides').append(d)}
const play=report.playback||{};$('playback').append(node('p',play.enabled?'نمونه‌های این اجرا: '+play.sampled+' / '+play.total_channels:'بررسی اختیاری ffmpeg در این اجرا فعال نبوده است.','muted'));for(const sample of play.samples||[]){const p=node('p');p.append(node('span',sample.name+' · '),badge(sample.status,sample.status==='decoded'?'good':'warn'));$('playback').append(p,node('p',sample.reason,'tiny muted'))}$('playback').append(node('p','موفقیت چند ثانیه رمزگشایی فقط دربارهٔ همان نمونه و همان زمان است؛ کانال‌های دیگر، DRM، محدودیت جغرافیایی و کیفیت شبکهٔ شما را تأیید نمی‌کند.','tiny muted'));
</script></body></html>
"""
