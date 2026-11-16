"""Render the persisted editorial contract as a read-only review document."""
from html import escape
from services.factory_editorial_policy import editorial_policy


def render_policy_document():
    p=editorial_policy()
    css='body{max-width:1150px;margin:35px auto;padding:0 20px;background:#0b1420;color:#e4edf6;font:16px/1.8 sans-serif}h1{font-size:28px}article{padding:20px;border:1px solid #34506a;border-radius:12px;margin:18px 0}a{color:#86d7ff}table{border-collapse:collapse;width:100%}th,td{padding:12px 8px;text-align:left;vertical-align:top;border-bottom:1px solid #314255}strong{color:#a8e5d5}.notice{padding:16px;background:#20372d;border-radius:10px}@media(max-width:700px){table{font-size:12px}td,th{padding:8px 4px}}'
    parts=['<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>三位剪辑师职责与发布规则</title><style>'+css+'</style>',
        '<h1>三位剪辑师职责与发布规则</h1><p class="notice">已确认保存 · 2026-11-16 · 每天6条原创，三位剪辑师各2条；抖音每天3条、每人各1条。全部6条保存完整视频和文案，快手每日自动2条，选择未上抖音的内容；小红书由本人按需手动上传。</p>']
    for t in p['tracks']:
        parts.append('<article><h2>'+escape(t['editor']+' · '+t['direction'])+'</h2>'+''.join('<p><strong>'+label+'：</strong>'+escape(t[key])+'</p>' for label,key in [('内容','subjects'),('口播结构','story'),('画面风格','visual'),('声音表达','voice')])+'</article>')
    parts.append('<h2>海外核心公司</h2><p>'+escape('；'.join(p['overseas_core_companies']))+'</p><h2>国内仅限七家</h2><p>'+escape('；'.join(p['domestic_company_allowlist']))+'</p>')
    for region,count in [('海外',40),('国内',10)]:
        parts.append(f'<h2>{region}观察人物 · {count}位</h2><table><tr><th>编号</th><th>人物</th><th>背景线索</th><th>关注方向</th><th>来源</th></tr>')
        for person in p['people']:
            if person['region']!=region:continue
            name=person['name']+(' / '+person['chinese_name'] if person.get('chinese_name') else '')
            parts.append('<tr><td>'+str(person['number'])+'</td><td>'+escape(name)+'</td><td>'+escape(person['background'])+'</td><td>'+escape(person['watch_topics'])+'</td><td><a target="_blank" rel="noreferrer" href="'+escape(person['source_url'],quote=True)+'">资料入口</a></td></tr>')
        parts.append('</table>')
    parts.append('<h2>执行约定</h2><ul>'+''.join('<li>'+escape(note)+'</li>' for note in p['notes'])+'</ul><p>默认抖音基准时段17:00、20:00、22:00；快手19:00、21:00，均前后15分钟浮动；实际时段和发布状态以工厂看板为准，素材包不等于已提交或公开发布。</p>')
    return ''.join(parts)
