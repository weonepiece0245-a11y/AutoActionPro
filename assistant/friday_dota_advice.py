"""Dota 2 选英雄和出装建议(OpenDota 免费数据,不花钱):
  “对面有痛苦女王和幽鬼,我选什么” -> 对这几个英雄胜率最高的几个英雄;
  “幽鬼出什么装” -> 这个英雄最近大家最常出的装备(开局、前期、中期、后期)。
英雄可以用中文名、外号(小黑、蓝猫、PA……)或英文名。数据一天更新一次。
"""

from __future__ import annotations

import re
import time
from typing import Callable

API = "https://api.opendota.com/api"
CACHE_SECONDS = 86400
MIN_GAMES = 40  # 两个英雄对上的局数太少,胜率不准:不算
TOP = 5

# OpenDota 的英雄内部名(去掉 npc_dota_hero_)-> 中文名和常用外号
HERO_CN = {
    "antimage": "敌法师 敌法 AM", "axe": "斧王", "bane": "祸乱之源 祸乱", "bloodseeker": "血魔",
    "crystal_maiden": "水晶室女 冰女 CM", "drow_ranger": "卓尔游侠 小黑", "earthshaker": "撼地者 小牛 撼地神牛",
    "juggernaut": "主宰 剑圣", "mirana": "米拉娜 白虎 POM", "morphling": "变体精灵 水人", "nevermore": "影魔 SF",
    "phantom_lancer": "幻影长矛手 猴子 PL", "puck": "帕克", "pudge": "帕吉 屠夫", "razor": "剃刀 电魂", "sand_king": "沙王",
    "storm_spirit": "风暴之灵 蓝猫", "sven": "斯温", "tiny": "小小", "vengefulspirit": "复仇之魂 VS", "windrunner": "风行者 风行",
    "zuus": "宙斯", "kunkka": "昆卡 船长", "lina": "莉娜 火女", "lion": "莱恩", "shadow_shaman": "暗影萨满 小Y",
    "slardar": "斯拉达 大鱼人", "tidehunter": "潮汐猎人 潮汐", "witch_doctor": "巫医", "lich": "巫妖", "riki": "力丸 隐刺",
    "enigma": "谜团", "tinker": "修补匠 TK", "sniper": "狙击手 火枪", "necrolyte": "瘟疫法师 死灵法", "warlock": "术士",
    "beastmaster": "兽王", "queenofpain": "痛苦女王 女王 QOP", "venomancer": "剧毒术士 剧毒", "faceless_void": "虚空假面 虚空",
    "skeleton_king": "冥魂大帝 骷髅王 WK", "death_prophet": "死亡先知 DP", "phantom_assassin": "幻影刺客 PA",
    "pugna": "帕格纳 骨法", "templar_assassin": "圣堂刺客 TA", "viper": "冥界亚龙 毒龙", "luna": "露娜 月骑",
    "dragon_knight": "龙骑士 龙骑", "dazzle": "戴泽 暗影牧师", "rattletrap": "发条技师 发条", "leshrac": "拉席克 老鹿",
    "furion": "先知", "life_stealer": "噬魂鬼 小狗", "dark_seer": "黑暗贤者 兔子", "clinkz": "克林克兹 小骷髅",
    "omniknight": "全能骑士 全能", "enchantress": "魅惑魔女 小鹿", "huskar": "哈斯卡 神灵", "night_stalker": "暗夜魔王 夜魔",
    "broodmother": "育母蜘蛛 蜘蛛", "bounty_hunter": "赏金猎人 赏金", "weaver": "编织者 蚂蚁", "jakiro": "杰奇洛 双头龙",
    "batrider": "蝙蝠骑士 蝙蝠", "chen": "陈", "spectre": "幽鬼 UG", "doom_bringer": "末日使者 末日",
    "ancient_apparition": "远古冰魄 冰魂", "ursa": "熊战士 拍拍熊", "spirit_breaker": "裂魂人 白牛",
    "gyrocopter": "矮人直升机 飞机", "alchemist": "炼金术士 炼金", "invoker": "祈求者 卡尔", "silencer": "沉默术士 沉默",
    "obsidian_destroyer": "殁境神蚀者 黑鸟", "lycan": "狼人", "brewmaster": "酒仙", "shadow_demon": "暗影恶魔 毒狗",
    "lone_druid": "德鲁伊 熊德", "chaos_knight": "混沌骑士 混沌", "meepo": "米波", "treant": "树精卫士 大树",
    "ogre_magi": "食人魔魔法师 蓝胖", "undying": "不朽尸王 尸王", "rubick": "拉比克", "disruptor": "干扰者 萨尔",
    "nyx_assassin": "司夜刺客 小强", "naga_siren": "娜迦海妖 小娜迦", "keeper_of_the_light": "光之守卫 光法",
    "wisp": "艾欧 小精灵 IO", "visage": "维萨吉 死灵龙", "slark": "斯拉克 小鱼人", "medusa": "美杜莎 一姐",
    "troll_warlord": "巨魔战将 巨魔", "centaur": "半人马战行者 人马", "magnataur": "马格纳斯 猛犸", "shredder": "伐木机",
    "bristleback": "钢背兽 刚背", "tusk": "巨牙海民 海民", "skywrath_mage": "天怒法师 天怒", "abaddon": "亚巴顿",
    "elder_titan": "上古巨神 大牛", "legion_commander": "军团指挥官 军团", "techies": "工程师 炸弹人",
    "ember_spirit": "灰烬之灵 火猫", "earth_spirit": "大地之灵 土猫", "abyssal_underlord": "孽主 大屁股",
    "terrorblade": "恐怖利刃 TB", "phoenix": "凤凰", "oracle": "神谕者", "winter_wyvern": "寒冬飞龙 冰龙",
    "arc_warden": "天穹守望者 电狗", "monkey_king": "齐天大圣 大圣", "dark_willow": "邪影芳灵 小仙女",
    "pangolier": "石鳞剑士 滚滚", "grimstroke": "天涯墨客 墨客", "hoodwink": "森海飞霞 小松鼠", "void_spirit": "虚无之灵 紫猫",
    "snapfire": "电炎绝手 老奶奶", "mars": "玛尔斯", "dawnbreaker": "破晓辰星 锤妹", "marci": "玛西", "primal_beast": "兽",
    "muerta": "琼英碧灵", "ringmaster": "百戏大王",
}

# 常见装备的中文名(其他的用英文名)
ITEM_CN = {
    "blink": "跳刀", "black_king_bar": "BKB", "power_treads": "假腿", "phase_boots": "相位鞋", "arcane_boots": "秘法鞋",
    "tranquil_boots": "静谧之鞋", "travel_boots": "飞鞋", "boots": "速度之靴", "magic_wand": "魔杖", "magic_stick": "魔棒",
    "bottle": "魔瓶", "wraith_band": "怨灵系带", "null_talisman": "空灵挂件", "bracer": "护腕", "tango": "吃树",
    "flask": "大药", "clarity": "净化药水", "faerie_fire": "仙灵之火", "branches": "铁树枝干", "quelling_blade": "补刀斧",
    "ward_observer": "假眼", "ward_sentry": "真眼", "smoke_of_deceit": "诡计之雾", "dust": "粉", "enchanted_mango": "芒果",
    "orb_of_venom": "淬毒之珠", "blight_stone": "枯萎之石", "urn_of_shadows": "骨灰盒", "spirit_vessel": "大骨灰",
    "hand_of_midas": "点金手", "bfury": "狂战斧", "manta": "分身斧", "butterfly": "蝴蝶", "satanic": "撒旦",
    "skadi": "冰眼", "desolator": "暗灭", "greater_crit": "大炮", "monkey_king_bar": "金箍棒", "abyssal_blade": "大晕锤",
    "basher": "晕锤", "radiance": "辉耀", "heart": "龙心", "assault": "强袭胸甲", "shivas_guard": "希瓦",
    "ultimate_scepter": "A 杖", "aghanims_shard": "魔晶", "sheepstick": "羊刀", "orchid": "紫怨", "bloodthorn": "血棘",
    "force_staff": "推推棒", "glimmer_cape": "微光披风", "ghost": "绿杖", "cyclone": "吹风", "aether_lens": "以太透镜",
    "pipe": "笛子", "crimson_guard": "赤红甲", "lotus_orb": "莲花", "vanguard": "先锋盾", "blade_mail": "刃甲",
    "sange_and_yasha": "双刀", "yasha": "夜叉", "sange": "散华", "kaya": "慧光", "mjollnir": "大电锤", "maelstrom": "电锤",
    "diffusal_blade": "散失", "echo_sabre": "回音战刃", "silver_edge": "大隐刀", "invis_sword": "隐刀", "armlet": "臂章",
    "mask_of_madness": "疯脸", "solar_crest": "大勋章", "medallion_of_courage": "勋章", "mekansm": "梅肯",
    "guardian_greaves": "卫士胫甲", "holy_locket": "圣洁吊坠", "refresher": "刷新球", "octarine_core": "玲珑心",
    "ethereal_blade": "虚灵刀", "rod_of_atos": "阿托斯之棍", "nullifier": "否决", "moon_shard": "银月",
    "soul_ring": "魂戒", "hurricane_pike": "大推推", "heavens_halberd": "天堂之戟", "vladmir": "祭品",
}
COUNTER_RE = re.compile(r"对面|敌方|对方|克制|counter|打得过|怎么打", re.I)
PICK_RE = re.compile(r"选|拿|用什么|玩什么|什么英雄|哪个英雄|counter|克制", re.I)
BUILD_RE = re.compile(r"出装|出什么|买什么|装备|怎么出|出啥|build", re.I)


class DotaAdvice:
    def __init__(self, fetch: Callable[[str], object], clock: Callable[[], float] = time.time) -> None:
        self.fetch = fetch
        self.clock = clock
        self.cache: dict[str, tuple[float, object]] = {}

    def _get(self, path: str):
        hit = self.cache.get(path)
        if hit and self.clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        data = self.fetch(f"{API}{path}")
        self.cache[path] = (self.clock(), data)
        return data

    # ---------- 英雄名字 ----------
    def heroes(self) -> dict[int, dict]:
        """{id: {"key": 内部名, "en": 英文名, "cn": 中文名}}"""
        table = {}
        for h in self._get("/heroes") or []:
            if not isinstance(h, dict) or "id" not in h:
                continue
            key = str(h.get("name", "")).replace("npc_dota_hero_", "")
            names = HERO_CN.get(key, "").split()
            table[int(h["id"])] = {"key": key, "en": h.get("localized_name", key), "cn": names[0] if names else ""}
        return table

    def name(self, hero_id: int) -> str:
        info = self.heroes().get(int(hero_id))
        return (info["cn"] or info["en"]) if info else f"英雄 {hero_id}"

    def find_heroes(self, text: str) -> list[int]:
        """一句话里提到的英雄(按出现的先后)。"""
        aliases: list[tuple[str, int]] = []
        for hero_id, info in self.heroes().items():
            for alias in HERO_CN.get(info["key"], "").split() + [info["en"]]:
                if alias:
                    aliases.append((alias.lower(), hero_id))
        aliases.sort(key=lambda a: -len(a[0]))  # 长的先认:“小黑”不会被“黑”抢走
        lower = str(text).lower()
        taken = [False] * len(lower)
        found: list[tuple[int, int]] = []
        for alias, hero_id in aliases:
            start = 0
            while True:
                at = lower.find(alias, start)
                if at < 0:
                    break
                end = at + len(alias)
                ascii_alias = alias.isascii()
                boundary = (not ascii_alias or ((at == 0 or not lower[at - 1].isalnum())
                                                and (end == len(lower) or not lower[end].isalnum())))
                if boundary and not any(taken[at:end]):
                    for i in range(at, end):
                        taken[i] = True
                    found.append((at, hero_id))
                start = at + 1
        result = []
        for _, hero_id in sorted(found):
            if hero_id not in result:
                result.append(hero_id)
        return result

    # ---------- 克制 ----------
    def counters(self, enemies: list[int]) -> str:
        enemies = enemies[:5]
        if not enemies:
            return "要告诉我对面有哪些英雄,例如“对面有痛苦女王和幽鬼,我选什么”。"
        score: dict[int, list[float]] = {}
        for enemy in enemies:
            for row in self._get(f"/heroes/{enemy}/matchups") or []:
                games = int(row.get("games_played") or 0)
                hero = int(row.get("hero_id") or 0)
                if games < MIN_GAMES or hero in enemies:
                    continue
                our_rate = 1 - int(row.get("wins") or 0) / games  # 这一行是对面英雄的胜场
                score.setdefault(hero, []).append(our_rate)
        full = {h: rates for h, rates in score.items() if len(rates) == len(enemies)}  # 对每个敌人都有数据的
        if not full:
            return "OpenDota 上这几个英雄的对局数据不够,给不出建议。"
        best = sorted(full.items(), key=lambda kv: -sum(kv[1]) / len(kv[1]))[:TOP]
        names = "、".join(self.name(e) for e in enemies)
        picks = "、".join(f"{self.name(h)}({sum(r) / len(r):.0%})" for h, r in best)
        return (f"对面有 {names}:对他们胜率比较高的是 {picks}(括号里是最近对上他们的平均胜率,"
                f"OpenDota 公开比赛的数据)。也要看你们队缺什么位置。")

    # ---------- 出装 ----------
    def items(self) -> dict[int, str]:
        table = {}
        for key, info in (self._get("/constants/items") or {}).items():
            if isinstance(info, dict) and "id" in info:
                table[int(info["id"])] = ITEM_CN.get(key) or info.get("dname") or key
        return table

    def build(self, hero_id: int) -> str:
        data = self._get(f"/heroes/{hero_id}/itemPopularity") or {}
        names = self.items()
        parts = []
        for key, label, count in (("start_game_items", "开局", 5), ("early_game_items", "前期", 4),
                                  ("mid_game_items", "中期", 4), ("late_game_items", "后期", 4)):
            items = sorted((data.get(key) or {}).items(), key=lambda kv: -int(kv[1]))
            chosen = [names.get(int(i), f"#{i}") for i, _ in items[:count]]
            if chosen:
                parts.append(f"{label}:{'、'.join(chosen)}")
        if not parts:
            return f"OpenDota 上暂时没有{self.name(hero_id)}的出装数据。"
        return f"{self.name(hero_id)}最近大家最常出的装:" + ";".join(parts) + "。(OpenDota 公开比赛的数据,按局势调整)"

    # ---------- 一句话 ----------
    def answer(self, sentence: str) -> str | None:
        """像是在问选英雄/出装就回答;不像就 None(交给别人)。"""
        building = bool(BUILD_RE.search(sentence))
        countering = bool(COUNTER_RE.search(sentence) and PICK_RE.search(sentence))
        if not building and not countering:
            return None  # 不像在问:不去连 OpenDota
        heroes = self.find_heroes(sentence)
        if building and heroes:
            return self.build(heroes[-1])
        if countering and heroes:
            mine = re.split(r"我(选|拿|用|玩)", sentence)[0]  # “对面有 A、B,我选什么”:只算前半句的英雄
            return self.counters(self.find_heroes(mine) or heroes)
        return None
