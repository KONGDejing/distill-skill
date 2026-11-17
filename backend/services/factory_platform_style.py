"""Our editorial choices per destination, not claims about platform algorithms."""
from copy import deepcopy

PLATFORM_STYLES = {
    'douyin': {'platform': 'douyin', 'name': '抖音', 'accent': '#5ef0c5', 'label': 'AI · 看懂科技变化',
        'opening_mode': 'first_frame_title', 'opening_brief': '本条真实首帧就是封面背景，主体对应本期人物、机器人、产品或论文证据；叠加中上部大标题，背景从第一秒继续运动。每条独立内容画面，不再使用统一AI品牌图片片头。',
        'narrative': '先展示真实动作、成果或本期事件→一句具体的新进展或反差→解释2–3个关键原理→用真实结果兑现开头→自然邀请继续关注。开场3秒有具体对象，10秒内说明发生了什么。短句、主动语态、关键数字与对应证据同屏；画面跟随口播对象变化，不把整片做成文字播报。',
        'copy': '短标题突出一个冲突或疑问；正文简洁给看点，结尾问一个与本条相关的问题。'},
    'xiaohongshu': {'platform': 'xiaohongshu', 'name': '小红书', 'accent': '#ffacb7', 'label': 'AI · 实用科技笔记',
        'opening_mode': 'first_frame_title', 'opening_brief': '直接用本条视频真实首帧，叠加视频大标题；紧凑笔记式文字，不能套用抖音独立图片片头。背景按原视频播放。',
        'narrative': '具体使用问题开场→分2–3步讲怎么理解或怎么判断→真实原文/操作画面→适用条件与避坑总结。表达清晰可回看，重点编号上屏；无亲测不能写亲测。',
        'copy': '标题说明具体问题和收获；正文分短段或编号，给可保存的判断清单、适用范围和资料来源，避免照抄口播或假装使用体验。'},
    'kuaishou': {'platform': 'kuaishou', 'name': '快手', 'accent': '#ffc45e', 'label': 'AI · 把原理讲明白',
        'opening_mode': 'first_frame_title', 'opening_brief': '直接用本条视频真实首帧，叠加直白清晰的大标题；保留动作和演示背景，不能套用抖音独立图片片头。背景按原视频播放。',
        'narrative': '一个直观例子开场→看完整动作或过程→用日常比喻解释原理→结果和限制。口气自然、沉稳直白，少术语堆砌，动作不能为快切而剪碎。',
        'copy': '标题用直白问题讲清对象；正文围绕一个例子说清用途与限制，话题最多4个，自然邀请观众提实际问题。'},
}


def platform_style(platform):
    style = deepcopy(PLATFORM_STYLES.get(platform, {}))
    if style:
        style['opening_brief'] += ' 封面大标题放中上部，避开底部文案及右侧按钮，保持大字号和清晰对比；不能把主标题放在底部。'
    return style
