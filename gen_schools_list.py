# -*- coding: utf-8 -*-
"""2027考研 12408组合 三档择校名单 PDF 生成（研可岸会员逐校核实 + DS/Claude 讨论定稿）"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak)

OUT = r"C:\Users\Xie\WPSDrive\1718374671\WPS企业云盘\江西理工大学\我的企业文档\考研备考\考研数学\880拼好卷\2027考研_12408组合_三档择校名单.pdf"

F = 'SimHei'
pdfmetrics.registerFont(TTFont(F, r'C:\Windows\Fonts\simhei.ttf'))

NAVY = HexColor('#1F3864')
BLUE = HexColor('#2E5FA3')
TXT = HexColor('#222222')
GRAY = HexColor('#555555')
LINE = HexColor('#B9C7DE')
HEADBG = HexColor('#DCE6F2')
ROWBG = HexColor('#F2F6FB')
RED = HexColor('#C0392B')
GREEN = HexColor('#1E6B3A')

def PS(name, **kw):
    base = dict(fontName=F, fontSize=9, leading=12.5, textColor=TXT,
                alignment=TA_LEFT, spaceAfter=2)
    base.update(kw)
    return ParagraphStyle(name, **base)

s_title = PS('t', fontSize=16, leading=20, textColor=NAVY, spaceAfter=2, alignment=TA_CENTER)
s_sub   = PS('s', fontSize=8.5, leading=12, textColor=GRAY, spaceAfter=6, alignment=TA_CENTER)
s_h1    = PS('h1', fontSize=13, leading=17, textColor=NAVY, spaceBefore=6, spaceAfter=3)
s_h2    = PS('h2', fontSize=10.5, leading=15, textColor=BLUE, spaceBefore=3, spaceAfter=2)
s_body  = PS('b', fontSize=9, leading=12.5)
s_cell  = PS('c', fontSize=8, leading=11, spaceAfter=0)
s_cellb = PS('cb', fontSize=8, leading=11, spaceAfter=0, textColor=NAVY)
s_cellr = PS('cr', fontSize=8, leading=11, spaceAfter=0, textColor=RED)
s_cellg = PS('cg', fontSize=8, leading=11, spaceAfter=0, textColor=GREEN)

def P(text, style=s_body):
    return Paragraph(text, style)

def mk_tbl(rows, widths, head=True, align_first=False):
    t = Table(rows, colWidths=widths)
    st = [
        ('GRID', (0,0), (-1,-1), 0.5, LINE),
        ('TOPPADDING', (0,0), (-1,-1), 2), ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('LEFTPADDING', (0,0), (-1,-1), 5), ('RIGHTPADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]
    if head:
        st.append(('BACKGROUND', (0,0), (-1,0), HEADBG))
        st.append(('BACKGROUND', (0,1), (-1,-1), ROWBG))
    t.setStyle(TableStyle(st))
    return t

story = []

# ================= 第 1 页 =================
story.append(P('2027 考研 · 12408 组合三档择校名单', s_title))
story.append(P('考试组合 12408 = 政治101 + 英语一201 + 数学二302 + 408 ｜ 2026-12-19 考试（按 10-05 快照距考 75 天）<br/>数据来源：研可岸会员逐校核实（2026-10-05）+ 研招网 / 院校官网交叉验证 ｜ 按 DS + Claude 讨论定稿', s_sub))

story.append(P('一、核心结论（务必先读）', s_h1))
story.append(P('<b>12408（英一+数二+408）是全国极端冷门组合。</b>经研可岸智能择校筛出 53 行全国候选、逐一进详情页核对考试科目后发现：智能择校为宽松匹配，绝大多数命中院校实际是 <b>22408（英语二）</b>或 <b>11408（数学一）</b>。'))
story.append(Spacer(1, 2))
for t in [
    '· <b>一志愿可报：全国仅北京理工大学珠海校区一所</b>（085404 计算机技术 + 085410 人工智能两个专业均为 12408）。',
    '· <b>广东 34 所院校逐一核查</b>：符合 12408 的仅北理珠海一所（华工 085404=数一、深大=数一、广工/华农=英二、广财=自命题809、广油=英二、北科顺德=数一）。',
    '· <b>已核实排除（11408 数一）</b>：深大教育学部085410、深大本部085404/085410、北科顺德085404、西电AI085404、南理工083900/083500、南师大081200、上科大085400、广西科大081200/085404、江西中医药081200、国际关系学院083900、石河子大学083900。',
    '· <b>已核实排除（22408 英二）</b>：广东石油化工085404、川大085400、成都信息工程085404、西华085404、大连大学085404、湖北民族085404、内蒙古财经085404、辽宁工业085404、河北农大085410、江汉大学085400、国际关系学院085411。',
    '· <b>因此"冲稳保"不再是三所不同院校，而是重构为：</b>主攻 085404 + 备选 085410 + 22408 调剂池兜底（英一可调英二、数二可调数二，调剂通道合规且考生成绩有优势）。',
]:
    story.append(P(t))

story.append(P('二、三档结构总览', s_h1))
rows = [
    [P('档位', s_cellb), P('院校 / 专业', s_cellb), P('定位与触发条件', s_cellb), P('当前评估', s_cellb)],
    [P('冲刺档', s_cellb), P('北理珠海 085410 人工智能', s_cell), P('初试 ≥365 才建议选报（政70+英72+数二125+408 98）', s_cell), P('概率低（408 需冲 105 均分），仅超常发挥时启用', s_cellr)],
    [P('主攻档', s_cellb), P('北理珠海 085404 计算机技术', s_cell), P('目标 355-360（守 340 冲 365），一志愿主攻', s_cell), P('匹配度高：345-359 段录取率 77.8%、360+ 近 100%', s_cellg)],
    [P('保底档', s_cellb), P('22408 调剂池（约 11 所）', s_cell), P('初试 ≥300 且单科过线后调剂；复试被刷即启动', s_cell), P('调剂成功率约 75%；广油/辽工等 275-313 分段可覆盖', s_cellg)],
]
story.append(mk_tbl(rows, [52, 118, 200, 146]))
story.append(Spacer(1, 4))

story.append(P('三、主攻档详表：北理珠海 085404 计算机技术（2026 年精算数据）', s_h1))
rows = [
    [P('指标', s_cellb), P('数值', s_cellb), P('解读', s_cellb)],
    [P('2026 复试线', s_cell), P('300（珠海单列；本部中关村 354）', s_cell), P('珠海单独划线，低于本部 54 分', s_cell)],
    [P('进复试 / 录取', s_cell), P('72 进复试 → 43 录取（复录比 1.67）', s_cell), P('约 40% 复试淘汰，进线≠录取，复试要准备', s_cell)],
    [P('分数分布', s_cell), P('最低 302 / 最高 390 / 中位 328 / 建议分 344', s_cell), P('2026 录取均分约 333', s_cell)],
    [P('分段录取率', s_cell), P('360+ 近100%｜345-359 为77.8%｜330-344 为72.7%', s_cell), P('335 以下逆袭概率低（最低 302，320 以下仅 5 人）', s_cell)],
    [P('科目均分（进复试）', s_cell), P('政 59.51 / 英 66.02 / 数二 120.54 / 408 98.78', s_cell), P('408 均分不到 100，是相对洼地也是命门', s_cell)],
    [P('招生规模', s_cell), P('2025 实招 20 → 2026 实招 44（研招网拟招仅 11）→ 2027 拟招 17', s_cell), P('2026 大幅扩招；2027 拟招 17，线位可能上移 10-15 分', s_cellr)],
    [P('复试规则', s_cell), P('初试复试各 50%，不区分方向', s_cell), P('初试分高但复试表现差仍可能被刷', s_cell)],
    [P('2027 线位预测', s_cell), P('DS：310-325（320 基准）；Claude：320-330', s_cell), P('建议分 344 基础上再上移，目标定 360+ 更稳', s_cell)],
]
story.append(mk_tbl(rows, [108, 182, 226]))

story.append(PageBreak())

# ================= 第 2 页 =================
story.append(P('四、冲刺档详表：北理珠海 085410 人工智能（2026 年数据）', s_h1))
rows = [
    [P('指标', s_cellb), P('数值', s_cellb), P('解读', s_cellb)],
    [P('2026 复试线', s_cell), P('306（一志愿 34 人 / 调剂 0）', s_cell), P('同校同科目组，线比 085404 高 6 分', s_cell)],
    [P('分数分布', s_cell), P('最低 306 / 最高 402 / 建议分 357', s_cell), P('录取均分与建议分均高于 085404', s_cell)],
    [P('科目均分', s_cell), P('政 59.89 / 英 64.94 / 数二 123.33 / 408 105.39', s_cell), P('408 均分比 085404 高 6.6 分，考生 408 目标 95 差距大', s_cellr)],
    [P('招生规模', s_cell), P('2025 实招 36 → 2026 实招 34（拟招仅 6）→ 2027 拟招 13', s_cell), P('同样存在缩招与线上移风险', s_cellr)],
]
story.append(mk_tbl(rows, [108, 182, 226]))
story.append(Spacer(1, 4))
story.append(P('<b>DS/Claude 一致裁决：主攻 085404，不碰 085410。</b>408 均分 105.39 对考生当前水平（408 目标 95）是硬门槛，76 天内冲到 105+ 概率极低；085410 仅保留为"初试出分 ≥365 且 408 稳定 100+"时的临时选项。', s_body))

story.append(P('五、保底档：22408 调剂池（初试后启动，非报名阶段）', s_h1))
story.append(P('调剂规则：统考科目相同或相近——<b>英语一可调英语二、数学二可调数学二</b>，考生 12408 成绩合规调入所有"数二+408"专业。英语一 70 分在英二调剂市场属高分段，是结构性优势。', s_body))
story.append(Spacer(1, 2))
story.append(P('A 区 · 广东', s_h2))
rows = [
    [P('院校', s_cellb), P('专业', s_cellb), P('2026 录取参考', s_cellb), P('备注', s_cellb)],
    [P('广东石油化工学院', s_cell), P('085404', s_cell), P('一志愿 3 人 264-302；调剂 15 人 275-313（来源含深大/广工/广技师/北理工）', s_cell), P('广东保底首选，过线即收概率大', s_cellg)],
]
story.append(mk_tbl(rows, [130, 70, 230, 86]))
story.append(Spacer(1, 3))
story.append(P('A 区 · 其他 / B 区', s_h2))
rows = [
    [P('院校', s_cellb), P('专业', s_cellb), P('2026 参考', s_cellb), P('备注', s_cellb)],
    [P('辽宁工业大学', s_cell), P('085404', s_cell), P('2026 复试线 272（A 类线+8）', s_cell), P('调剂竞争低', s_cellg)],
    [P('西华大学 / 大连大学 / 江汉大学', s_cell), P('085404 / 085400', s_cell), P('均为 22408，调剂常态化', s_cell), P('分数段 2027 调剂系统确认', s_cell)],
    [P('河北农业大学 / 成都信息工程 / 中国计量大学', s_cell), P('085410 / 085404', s_cell), P('均为 22408', s_cell), P('分数段 2027 调剂系统确认', s_cell)],
    [P('湖北民族大学 / 内蒙古财经大学（B 区）', s_cell), P('085404', s_cell), P('B 区国家线低 10 分，调剂意愿强', s_cell), P('最稳兜底，接受学校层次降级', s_cell)],
]
story.append(mk_tbl(rows, [170, 70, 160, 116]))
story.append(Spacer(1, 3))
story.append(P('<b>代价声明：</b>调剂兜底是"有学上"的保底而非"上好学"的保底——学校层次从 985 异地校区降至双非，地域多为茂名/锦州/恩施等非核心城市，且调剂名额随年度缩招波动，需 2027 年 3 月调剂系统开放后逐校确认。', s_body))

story.append(P('六、报考与分数策略（DS + Claude 一致结论）', s_h1))
rows = [
    [P('决策项', s_cellb), P('结论', s_cellb)],
    [P('是否换组合', s_cell), P('坚守 12408。数二转数一（11408）76 天补不完级数/曲面积分等增量，不现实；英一换英二（22408）等于放弃调剂核心武器，得不偿失', s_cell)],
    [P('一志愿', s_cell), P('北理珠海 085404 计算机技术（唯一合理主攻）；085410 仅超常发挥时启用', s_cell)],
    [P('目标总分', s_cell), P('355-360（守 340、冲 365）。缩招背景下建议尽量冲 360+，不要卡 355', s_cell)],
    [P('单科分解', s_cell), P('政治 68 / 英语 70 / 数二 122 / 408 95。英语 70 高于进复试均分 66.02（护城河）；408 95 略低于均分 98.78，靠英语数学对冲', s_cell)],
    [P('上岸概率（Claude）', s_cell), P('085404 主攻约 60% + 复试被刷后调剂成功约 75% = 综合上岸率约 88%', s_cellg)],
]
story.append(mk_tbl(rows, [120, 396]))

story.append(PageBreak())

# ================= 第 3 页 =================
story.append(P('七、风险清单与应急预案', s_h1))
rows = [
    [P('风险', s_cellb), P('概率', s_cellb), P('影响', s_cellb), P('应对', s_cellb)],
    [P('2027 缩招致复试线上移（44→17）', s_cell), P('高', s_cell), P('建议分 344 可能不够', s_cell), P('目标定 360+；出分后立即做复试+调剂双线准备', s_cell)],
    [P('408 目标 95 低于进复试均分 98.78', s_cell), P('中', s_cell), P('复试竞争力不足', s_cell), P('OS 系统学完后 408 冲 100+；11 月模考 &lt;90 则启用对冲预案', s_cell)],
    [P('复试被刷（复录比 1.67，约 40% 淘汰）', s_cell), P('中', s_cell), P('需调剂', s_cell), P('出分后 24h 内联系广油等调剂院校，准备本科成绩单+六级+408 成绩亮点', s_cell)],
    [P('数学二波动（真题 113-150 之间）', s_cell), P('中', s_cell), P('总分目标不稳', s_cell), P('76 天重点补薄弱环节（中值定理证明/微分方程/二重积分），保持 120+', s_cell)],
    [P('初试 &lt;300 或单科不过线', s_cell), P('低（&lt;5%）', s_cell), P('无学可上', s_cell), P('按现有基础概率极低；万一发生则评估二战（可换 11408 冲深大/西电）', s_cell)],
]
story.append(mk_tbl(rows, [150, 44, 96, 226]))

story.append(P('八、数据来源与口径声明', s_h1))
for t in [
    '· <b>已核证（研可岸 APP 截图 6 张 + 北理珠海官网复试通知 300 线 + 多源录取名单交叉吻合）</b>：北理珠海 085404 / 085410 全部 2026 录取数据、分段录取率、招生规模、科目均分。',
    '· <b>已核证（研可岸网页版详情页"考试范围"tab + 研招网/院校官网 2026-2027 目录）</b>：全国 53 行候选逐一核实，形成"仅北理珠海符合 12408"与 22 所排除清单。',
    '· <b>待确认（不编数字）</b>：北理珠海 2027 实际招生数（拟招 17 非最终）、2027 复试线预测（DS 310-325 / Claude 320-330，为估计值）、调剂池各校 2027 调剂名额与分数段（需 2027 年 3 月调剂系统开放后逐校确认）。',
    '· <b>时间口径</b>：名单基于 2026-10-05 快照（当时距 2026-12-19 考试 75 天）；本名单基于 2026 录取数据外推，报考前请以 2027 年 9 月官方招生目录为准。',
]:
    story.append(P(t))

story.append(P('九、75 天冲刺优先级（供参考，与十月规划 PDF 一致）', s_h1))
rows = [
    [P('科目', s_cellb), P('当前→目标', s_cellb), P('主攻动作', s_cellb), P('时间占比', s_cellb)],
    [P('数学二', s_cell), P('113→122（+9）', s_cell), P('真题二刷+错题三刷+模拟卷；中值定理/微分方程/二重积分', s_cell), P('35%', s_cell)],
    [P('408', s_cell), P('→95', s_cell), P('计网带背完成 + OS 系统刷 + 计组收尾 + 数据结构大题限时', s_cell), P('30%', s_cell)],
    [P('英语一', s_cell), P('16/20→18/20', s_cell), P('真题阅读逐篇精读 + 长难句 + 11 月作文模板', s_cell), P('20%', s_cell)],
    [P('政治', s_cell), P('0→68', s_cell), P('帕拉迪宇直播跟学 + 1000 题马原 + 肖四肖八（12 月集中）', s_cell), P('15%', s_cell)],
]
story.append(mk_tbl(rows, [70, 90, 250, 106]))

doc = SimpleDocTemplate(OUT, pagesize=A4,
                        leftMargin=40, rightMargin=40, topMargin=42, bottomMargin=40,
                        title='2027考研 12408组合 三档择校名单')
doc.build(story)
print('OK', OUT)
