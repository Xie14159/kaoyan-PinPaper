# -*- coding: utf-8 -*-
"""10-12月全程学习规划 PDF 生成（数二+408+英一）v2 紧凑4页版"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak)

OUT = r"C:\Users\Xie\WPSDrive\1718374671\WPS企业云盘\江西理工大学\我的企业文档\考研备考\考研数学\880拼好卷\数学二408十月至十二月学习规划_2026.pdf"

F = 'SimHei'
pdfmetrics.registerFont(TTFont(F, r'C:\Windows\Fonts\simhei.ttf'))

NAVY = HexColor('#1F3864')
BLUE = HexColor('#2E5FA3')
TXT = HexColor('#222222')
GRAY = HexColor('#555555')
LINE = HexColor('#B9C7DE')
HEADBG = HexColor('#DCE6F2')
ROWBG = HexColor('#F2F6FB')

def PS(name, **kw):
    base = dict(fontName=F, fontSize=9, leading=12.5, textColor=TXT,
                alignment=TA_LEFT, spaceAfter=2)
    base.update(kw)
    return ParagraphStyle(name, **base)

s_title = PS('t', fontSize=16, leading=20, textColor=NAVY, spaceAfter=2)
s_sub   = PS('s', fontSize=8.5, leading=12, textColor=GRAY, spaceAfter=5)
s_h1    = PS('h1', fontSize=13, leading=17, textColor=NAVY, spaceBefore=5, spaceAfter=3)
s_h2    = PS('h2', fontSize=10.5, leading=15, textColor=BLUE, spaceBefore=3, spaceAfter=2)
s_body  = PS('b', fontSize=9, leading=12.5)
s_cell  = PS('c', fontSize=8, leading=11, spaceAfter=0)
s_cellb = PS('cb', fontSize=8, leading=11, spaceAfter=0, textColor=NAVY)

def P(text, style=s_body):
    return Paragraph(text, style)

story = []

# ================= 第 1 页：总览 + 十月 =================
story.append(P('2026 年 10-12 月全面学习规划（数二 + 408 + 英语一）', s_title))
story.append(P('2027 考研 · 冲刺全程  |  修订 2026-10-05（第 4 版）  |  距离考研 75 天  |  按 DS + Claude 意见定稿', s_sub))

info = [
    [P('考生', s_cellb), P('数学二 + 408 + 英一（北理工珠海校区·计算机）', s_cell)],
    [P('每日作息', s_cellb), P('8:30-12:00 数学（3h 限时+收尾）→ 13:50-17:10 408 → 17:20-18:20 吃饭 → 18:20-20:00 408收尾+英语阅读精翻 → 20:00-22:30 数学复盘（错题重做+计算，不碰新题）', s_cell)],
    [P('碎片时间', s_cellb), P('单词 20-25min；政治刷题 20-30min；选填专项 20-30min', s_cell)],
    [P('休息制度', s_cellb), P('每周半天（周日下午低强度）；每两周大休息（W2/W4 周末，替代当周半天）：上午自然醒（11:30 前）→ 下午 14:00-17:30 低强度自习室（单词+政治+补欠账）→ 晚上轻量复盘；大休息后第一天 80% 强度', s_cell)],
]
tbl = Table(info, colWidths=[58, 458])
tbl.setStyle(TableStyle([
    ('GRID', (0,0), (-1,-1), 0.5, LINE),
    ('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ('LEFTPADDING', (0,0), (-1,-1), 5), ('RIGHTPADDING', (0,0), (-1,-1), 5),
    ('BACKGROUND', (0,0), (0,-1), HEADBG),
]))
story.append(tbl)
story.append(Spacer(1, 2))

# ---------- 十月 ----------
story.append(P('【十月】真题主线月 · 武忠祥顺序 8 套 + 2021 二刷', s_h1))

story.append(P('一、数学（每日约 5.5h）', s_h2))
for t in [
    '· <b>真题主线（武忠祥顺序）</b>：W1 2025 → W2 2018/2016/2020 → W3 2023/2022/2024 → W4 2026 + 2021 二刷。',
    '· <b>真题日</b>：8:30-11:30 严格限时 → 归因表（计算/方法/知识点/审题）→ 当晚错题重做；次日复盘 1-1.5h。',
    '· <b>非真题日</b>：选填限时 50min + 880 弱章补 + 艾宾浩斯错题 10 道。',
    '· <b>880</b>：线代 11/12 章 W1 闭环；高数按真题暴露弱章补，不追求全清。2021 二刷 W4 严格模拟。',
]:
    story.append(P(t))

story.append(P('二、408（每日约 4.3h）', s_h2))
for t in [
    '· <b>计组</b>：task12-16 收尾（10/6 前清完）。',
    '· <b>OS</b>：10/6 启动，进程→调度→PV→死锁→内存→虚拟内存→文件→I/O；10 月目标"过半"（进程+内存+文件系统），PV/虚拟内存留 2-3 天王道课后大题，一轮标志=能独立做大题。',
    '· <b>计网</b>：带背 day19 起（15min/天）+ 每周 1 次综合题验证。数据结构 10 月不碰。',
]:
    story.append(P(t))

story.append(P('三、英语（每日约 2h，英一）', s_h2))
for t in [
    '· <b>阅读一刷收尾</b>：23 余下 text2-4 → 24 → 25，每天 1 篇精做+精翻。',
    '· <b>二刷启动</b>（W3-W4）：错题+蒙对+纠结题，20min/篇，写选项分析。',
    '· 新题型+完形 W2 起隔天交替；作文小模板 W1-W2 → 大模板 W3-W4，每日仿写 1 段。',
]:
    story.append(P(t))

story.append(P('四、政治（每日 ≤1h）', s_h2))
for t in [
    '· <b>只做马原</b>：哲学三倍速听课（理解优先）+ 1000 题马原部分；政经/科社不单独做，11 月模拟卷里学。',
    '· 不背大题、不管时政、不画导图；史纲/毛中特 11 月用一页纸+模拟卷覆盖。',
]:
    story.append(P(t))

story.append(Spacer(1, 2))
story.append(P('十月周计划（10/5 - 11/1）· 每周半天休息 + W2/W4 大休息', s_h2))

week10 = [
    [P('周次', s_cellb), P('数学', s_cellb), P('408', s_cellb), P('英语', s_cellb), P('政治', s_cellb)],
    [P('W1', s_cell), P('2025 真题；线代 11/12 章闭环', s_cell), P('计组收官；OS 启动：进程→调度→PV 入门；计网 day19-25', s_cell), P('一刷 23 收尾+24 开篇；小作文模板 1', s_cell), P('马原 1-2 章', s_cell)],
    [P('W2（大休息）', s_cell), P('2018/2016/2020 三套', s_cell), P('OS：死锁→内存→虚拟内存（重点）；计网 IP/路由', s_cell), P('一刷 24 收尾+25 开篇；新题型+完形', s_cell), P('马原 3-4 章', s_cell)],
    [P('W3', s_cell), P('2023/2022/2024 三套', s_cell), P('OS：文件→I/O；计网 TCP/GBN', s_cell), P('一刷 25 收尾；二刷启动', s_cell), P('马原收尾+错题', s_cell)],
    [P('W4（大休息）', s_cell), P('2026 + 2021 二刷（8:30-11:30）', s_cell), P('四科错题/综合回看；计网 CSMA/DNS-HTTP', s_cell), P('二刷推进；新题型+完形', s_cell), P('马原错题清；一页纸起步', s_cell)],
]
tbl = Table(week10, colWidths=[58, 125, 125, 105, 103], repeatRows=1)
tbl.setStyle(TableStyle([
    ('GRID', (0,0), (-1,-1), 0.5, LINE),
    ('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ('LEFTPADDING', (0,0), (-1,-1), 4), ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ('BACKGROUND', (0,0), (-1,0), HEADBG),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [None, ROWBG]),
]))
story.append(tbl)
story.append(PageBreak())

# ================= 第 2 页：十一月 =================
story.append(P('【十一月】模拟卷 + 旧真题 + 复盘月', s_h1))

story.append(P('一、数学（每日约 5.5h）', s_h2))
for t in [
    '· <b>旧真题 4 套</b>（2.5h 快速过全卷，重点精析解答题与错题）：W1 2015 → W2 2014 → W3 2013 → W4 2012。',
    '· <b>模拟卷 4 套</b>（3h 严格限时）：26 张宇四套卷 1-4（W1-W4 各 1 套）。',
    '· <b>复盘主力</b>：晚上 2.5h 给 10 月 8 套真题错题回炉 + 2021 二刷错题 + 模拟卷归因。',
    '· <b>选填专项（碎片）</b>：李六 2/5/6 + 李擂前 3 套，每天 20-30min 刷 1 套。高数弱章随二刷顺手补。',
]:
    story.append(P(t))

story.append(P('二、408（每日约 4.3h）', s_h2))
for t in [
    '· <b>OS 一轮收官</b>：I/O/死锁收尾 + PV/虚拟内存王道大题，能独立做即达标。',
    '· <b>数据结构（主菜）</b>：捡回一轮：算法/大题专项（数组/链表/树/图/查找排序）。',
    '· <b>计网</b>：六类大题专项（IP/路由/TCP/GBN/CSMA/DNS-HTTP）+ 带背保持。计组真题/错题回看。',
]:
    story.append(P(t))

story.append(P('三、英语（每日约 2h）', s_h2))
for t in [
    '· <b>阅读二刷推进</b>：错题+蒙对+纠结题，20min/篇，写选项分析；目标累计 15-20 篇。',
    '· 新题型/完形每周各 2 篇；作文模板成型 + 每日仿写 1 段 + 批改。单词每日不中断。',
]:
    story.append(P(t))

story.append(P('四、政治（每日 ≤1h）', s_h2))
for t in [
    '· <b>换挡：模拟卷选择 + 一页纸</b>：每天 1 套模拟卷选择（肖八/腿四/米六/徐六，累计 20-25 套，30min 碎片）+ 一页纸背诵 20-30min（只读求再认，不用背诵手册）。',
    '· 近 5 年真题选择 11/20 后开刷；史纲不听课（时间线乱才补）；章节题降级为错题回查工具；大题/时政 12 月启动。',
]:
    story.append(P(t))

story.append(Spacer(1, 2))
story.append(P('十一月周计划（11/2 - 11/29）· 每周半天休息 + W2/W4 大休息', s_h2))

week11 = [
    [P('周次', s_cellb), P('数学', s_cellb), P('408', s_cellb), P('英语', s_cellb), P('政治', s_cellb)],
    [P('W1', s_cell), P('2015 + 26张四 1；10 月错题回炉', s_cell), P('OS 收官开始；DS 捡回', s_cell), P('二刷；新题型', s_cell), P('一页纸起步；模拟卷选择', s_cell)],
    [P('W2（大休息）', s_cell), P('2014 + 26张四 2；错题回炉', s_cell), P('OS 收官；计网 IP/路由', s_cell), P('二刷；完形', s_cell), P('一页纸；模拟卷选择', s_cell)],
    [P('W3', s_cell), P('2013 + 26张四 3；错题回炉', s_cell), P('DS 主菜；计组真题', s_cell), P('二刷；作文仿写', s_cell), P('一页纸；模拟卷选择', s_cell)],
    [P('W4（大休息）', s_cell), P('2012 + 26张四 4；错题回炉', s_cell), P('四科错题/真题', s_cell), P('二刷收尾；作文冲刺', s_cell), P('模拟卷选择；近 5 年真题选择', s_cell)],
]
tbl = Table(week11, colWidths=[58, 125, 125, 105, 103], repeatRows=1)
tbl.setStyle(TableStyle([
    ('GRID', (0,0), (-1,-1), 0.5, LINE),
    ('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ('LEFTPADDING', (0,0), (-1,-1), 4), ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ('BACKGROUND', (0,0), (-1,0), HEADBG),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [None, ROWBG]),
]))
story.append(tbl)
story.append(PageBreak())

# ================= 第 3 页：十二月 =================
story.append(P('【十二月】收尾 + 回炉月（11/30 - 12/20）', s_h1))

story.append(P('一、数学', s_h2))
for t in [
    '· <b>旧真题 2 套</b>（2.5h 快速过）：W1 2011 → W2 2010（最后两套）。',
    '· <b>模拟卷 4 套</b>：W1 李林四 1 → W2 李林四 2 → W3（12/15 前）李林四 3/4 最后全真模拟；状态好加餐张八 4/5、欧几里得 1、李六 1。',
    '· <b>错题回炉 2 轮</b>：10-12 月所有真题/模拟卷错题 + 2021 三刷 + 17/19 错题重做；12/8 起公式默写 30min/天。',
    '· <b>考前红线</b>：12/17 起不碰新题，只翻错题本 + 公式。',
]:
    story.append(P(t))

story.append(P('二、408', s_h2))
for t in [
    '· 四科真题/错题收尾，12/10 前停止推新内容；408 综合卷 1-2 套保持手感。',
]:
    story.append(P(t))

story.append(P('三、英语', s_h2))
for t in [
    '· 作文冲刺：模板背熟 + 每日仿写；阅读隔天 1 篇真题错题重做，不刷新题；单词到考前。',
]:
    story.append(P(t))

story.append(P('四、政治（12/1 起 2h/天 → 12/11 起 3-4h/天）', s_h2))
for t in [
    '· <b>12/1-12/10</b>：每天 2h——一页纸 1h + 肖四选择 1h（30min/套）+ 时政集中。',
    '· <b>12/11-考前</b>：每天 3-4h——肖四第一套背熟 + 其余三套只看一页纸对应内容 + 学"抄材料答题方法"（保大题底分）+ 限时完整模考一套。',
]:
    story.append(P(t))

story.append(Spacer(1, 2))
story.append(P('十二月周计划（11/30 - 12/20）· 考前 3 天不碰新题', s_h2))

week12 = [
    [P('周次', s_cellb), P('数学', s_cellb), P('408', s_cellb), P('英语', s_cellb), P('政治', s_cellb)],
    [P('W1', s_cell), P('2011 + 李林四 1；错题回炉', s_cell), P('四科真题/错题', s_cell), P('作文冲刺；阅读保持', s_cell), P('肖四选择；时政', s_cell)],
    [P('W2', s_cell), P('2010 + 李林四 2；公式默写启动', s_cell), P('408 综合收尾', s_cell), P('作文仿写+批改', s_cell), P('肖四第一套背诵', s_cell)],
    [P('W3（12/14-20）', s_cell), P('李林四 3/4（15 日前）；全错题回炉；12/17 起不碰新题', s_cell), P('停止新内容，只错题', s_cell), P('保持手感', s_cell), P('肖四背熟；抄材料方法；限时模考', s_cell)],
]
tbl = Table(week12, colWidths=[58, 125, 125, 105, 103], repeatRows=1)
tbl.setStyle(TableStyle([
    ('GRID', (0,0), (-1,-1), 0.5, LINE),
    ('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ('LEFTPADDING', (0,0), (-1,-1), 4), ('RIGHTPADDING', (0,0), (-1,-1), 4),
    ('BACKGROUND', (0,0), (-1,0), HEADBG),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [None, ROWBG]),
]))
story.append(tbl)
story.append(PageBreak())

# ================= 第 4 页：风险 + 原则 =================
story.append(P('风险预警与调整', s_h1))
for t in [
    '· <b>一周 3 套真题熔断</b>：任一套复盘超"当晚 2.5h + 次日 0.5h"没清完，当周降到 2 套——注水的复盘等于没复盘。',
    '· <b>2025 真题 &lt;100 分</b>：暂停推进新真题，改 880 弱章专项 + 2025 重做归因；100-120 正常推进；&gt;120 可加量。',
    '· <b>OS 一轮偏紧</b>（10 月 18 天一轮）：PV/虚拟内存过完未必会做题 → 11 月第一周回炉专项；进度落后先砍计网带背频次，不砍 OS。',
    '· <b>模拟卷定位</b>：暴露问题不是预测分数，做完 70-90 分心态别崩；复盘重点是重复失分题型。',
    '· <b>英语二刷正确率 &lt;85%</b>：停二刷，改阅读方法论专项。',
    '· <b>大休息红线</b>：下午低强度硬性执行；大休息后第一天 80% 强度；连续两次状态崩盘则降级回半天休息制。',
    '· <b>W2 高危周</b>：若过载（真题+OS 重点+大休息），2023 真题后移 W3，或大休息只休半天、懒觉挪 W4——二选一。',
]:
    story.append(P(t))

story.append(Spacer(1, 4))
story.append(P('关键原则（DS + Claude + 豆包共识）', s_h1))
for t in [
    '· 数学：吃透 &gt; 数量——14 套真题 + 12 套模拟每套复盘 3 遍，胜过 50 套对答案；归因表是底线；错题回炉 ≥2 轮。',
    '· 408：OS 先"过完"再"精通"，数据结构 11 月必须捡回；计网带背需配综合题验证。',
    '· 英语：选项分析 &gt; 做对；作文模板自提取 + 批改，不用市面通用模板。',
    '· 政治：10-11 月每天 ≤1h（马原听课 + 模拟卷选择 + 一页纸）；12 月加量到 3-4h 肖四；抄材料方法保大题底分。',
    '· 休息：大休息是恢复机制不是放纵借口——边界定死，上午休/下午低强度/晚上轻量。',
    '· 总原则：删掉 20% 的任务，把剩下 80% 做扎实。',
]:
    story.append(P(t))

story.append(Spacer(1, 4))
story.append(P('修订日期：2026-10-05（第 4 版 · 10-12 月全程）· 依据 DeepSeek / Claude 审查意见定稿', s_sub))

doc = SimpleDocTemplate(OUT, pagesize=A4,
                        leftMargin=36, rightMargin=36,
                        topMargin=30, bottomMargin=26,
                        title='数学二408十月至十二月学习规划')
doc.build(story)
print('OK', os.path.getsize(OUT))
