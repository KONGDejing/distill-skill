"""Pinned, reviewed originals eligible for bounded local import, never broad downloads.

Each exact source page was fetched on 2026-11-10 and directly embeds its media
URL. Original bytes, media metadata, and the listed windows were reviewed; the
full original files decoded without errors. No generated or replacement footage.
The 179 seconds below also passed the production motion/repeated-sequence gates;
the final montage still checks its actual chosen shots.
"""

REVIEWED_LOCAL_MAX_FILE_BYTES = 80_000_000
REVIEWED_LOCAL_MAX_TOTAL_BYTES = 150_000_000
_ROOT = "factory/newsroom/2026-11-10/editor3_dlss5/raw/"
_NOVEMBER = "https://www.nvidia.cn/geforce/news/hogwarts-legacy-war-thunder-dlss-5-3d-guided-neural-rendering/"
_LAUNCH = "https://www.nvidia.cn/geforce/news/dlss-5-3d-guided-neural-rendering/"

REVIEWED_LOCAL_ASSETS = {
    "https://images.nvidia.cn/cn/youtube-replicates/58FagrSqC4M.mp4": {
        "sha256": "97cfe72d8aa4cfdfa64f365d82bb77a3e5b59e6a6316894a9a86a45226cd54d6",
        "bytes": 74508969, "duration": 660.492,
        "local_path": _ROOT + "58FagrSqC4M.mp4",
        "reference_urls": (_NOVEMBER,),
        "description": "NVIDIA技术人员讲解DLSS 5，穿插开发者调参、角色和局部控制演示；已剔除静止停留及原片内部回切。AI增强游戏图像，非CES 2027现场实拍。",
        "topic_tags": ("dlss5", "official_game_demo", "developer_controls"),
        "clips": (
            (0, 20, "NVIDIA技术人员正面讲解与手势，说明报道对象和技术原理；不是本项目采访。"),
            (146, 166, "NVIDIA技术人员继续讲解，有镜头与手势变化；与开头为不重复原片区间。"),
            (406, 420, "坐姿游戏角色与开发者控制面板；对应画面变化由谁决定。"),
            (450, 454, "水果、器皿和桌面控制界面切换；排除后续重复显示。"),
            (490, 494, "食材桌面与局部控制界面变化；排除前后静止停留。"),
        ),
    },
    "https://images.nvidia.cn/cn/youtube-replicates/GPCM-PHjmWw.mp4": {
        "sha256": "8257d43c4fe252f9f76e4d5cdb5da8b50fc7818bb012814278ebd727005aaf10",
        "bytes": 20992097, "duration": 82.965,
        "local_path": _ROOT + "GPCM-PHjmWw.mp4",
        "reference_urls": (_NOVEMBER,),
        "description": "NVIDIA《霍格沃茨之遗》DLSS 5官方游戏演示，包含角色、城镇、庭院与室内Off/On对比；候选窗口避开后段厂商FPS数字。非独立实测或CES 2027现场。",
        "topic_tags": ("dlss5", "official_game_demo", "gameplay", "comparison"),
        "clips": (
            (8, 18, "城堡内外环境动态画面；保留原片DLSS 5 On标记。"),
            (18, 25, "女角色与服装的官方比较画面；不重新调色，排除后段原片回切。"),
            (28, 34, "城镇屋顶的官方比较画面；排除后段原片回切。"),
            (36, 43, "庭院与植被的官方比较画面；排除后段原片回切。"),
            (49, 52, "室内阶梯与光影的官方比较画面；排除前段低运动停留。"),
            (55, 57, "室内动态收尾；不据此下性能结论。"),
        ),
    },
    "https://images.nvidia.cn/cn/youtube-replicates/5khwlu2qD9U.mp4": {
        "sha256": "c43a80c54916c1704b635e7c6be1e76c1ee3635f135f36a025722d648bb2577b",
        "bytes": 43334458, "duration": 165.884,
        "local_path": _ROOT + "5khwlu2qD9U.mp4",
        "reference_urls": (_LAUNCH,),
        "description": "NVIDIA Jacob Freeman与Tyrese Haliburton体验NBA 2K27的官方视频，包含游戏比赛与真实体验者画中画；不是NBA真实比赛转播或CES 2027现场。",
        "topic_tags": ("dlss5", "official_game_demo", "gameplay"),
        "clips": (
            (22, 42, "游戏球员形象、手势和罚球动态画面。"),
            (54, 64, "篮球游戏运球、投篮及转播视角动态画面；排除后段原片内部定格和回放。"),
            (76, 96, "另一组篮球游戏进攻、传球镜头；不重复前段。"),
            (98, 130, "球场比赛、人物运动与篮下动作；保留官方演示标注。"),
        ),
    },
}

REVIEWED_LOCAL_SOURCE_URLS = frozenset(
    url for asset in REVIEWED_LOCAL_ASSETS.values() for url in asset["reference_urls"]
)
