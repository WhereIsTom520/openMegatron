#!/usr/bin/env python3
"""preflight-check v1.1.0 — 16-dimension pre-submission paper audit with
deep heuristic checks, actionable fix suggestions, and LaTeX-aware parsing."""

from __future__ import annotations
import json, re, sys, os, subprocess, hashlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import compact_text, emit, fail, parse_params

# ═══════════════════════════════════════════════════════════════
# 16-dimension checklist — expanded with all sub-checks
# ═══════════════════════════════════════════════════════════════

DIMENSIONS = {
    "research_coherence": {
        "id": 1,
        "name_zh": "研究主线检查",
        "name_en": "Research Coherence",
        "checks": [
            {"id": "rc1", "zh": "论文明确解决了什么问题", "critical": True},
            {"id": "rc2", "zh": "方法相比已有工作的新意是什么", "critical": True},
            {"id": "rc3", "zh": "创新点是否在 Introduction / Method / Figure / Conclusion 中反复一致出现", "critical": True},
            {"id": "rc4", "zh": "贡献是否具体而非泛泛而谈（避免只说 'better performance'）", "critical": False},
            {"id": "rc5", "zh": "结论是否只基于实验结果，不做过度临床/工业/理论声明", "critical": False},
            {"id": "rc6", "zh": "摘要、引言、方法、实验、结论中对研究问题的描述是否一致，没有前后变换说法", "critical": True},
        ],
    },
    "abstract": {
        "id": 2,
        "name_zh": "摘要检查",
        "name_en": "Abstract",
        "checks": [
            {"id": "ab1", "zh": "是否包含背景问题、方法核心、实验设置、主要结果、结论边界", "critical": True},
            {"id": "ab2", "zh": "是否避免口语化或过度宣传式措辞（revolutionary, game-changing 等）", "critical": False},
            {"id": "ab3", "zh": "是否所有说法都有数据支撑，避免绝对化说法", "critical": True},
            {"id": "ab4", "zh": "是否出现正文没有充分展开的新概念", "critical": True},
            {"id": "ab5", "zh": "是否把补充实验或辅助数据集写得像核心主结果", "critical": False},
            {"id": "ab6", "zh": "方法名称、核心机制、数据集、主要实验组、结果性质、局限或适用边界是否明确", "critical": False},
        ],
    },
    "introduction": {
        "id": 3,
        "name_zh": "Introduction 检查",
        "name_en": "Introduction",
        "checks": [
            {"id": "in1", "zh": "第一段是否清楚交代研究背景和现实困难", "critical": True},
            {"id": "in2", "zh": "是否避免一上来堆术语", "critical": False},
            {"id": "in3", "zh": "痛点是否和后文方法设计一一对应", "critical": True},
            {"id": "in4", "zh": "创新点是否在 Introduction 中明确出现", "critical": True},
            {"id": "in5", "zh": "Fig.1 如果放在 Introduction，是否体现论文创新而非仅仅画流程", "critical": False},
            {"id": "in6", "zh": "contribution list 是否具体、可验证、与实验对应", "critical": True},
        ],
    },
    "related_work": {
        "id": 4,
        "name_zh": "Related Work 检查",
        "name_en": "Related Work",
        "checks": [
            {"id": "rw1", "zh": "是否按大方向分小节（非简单罗列）", "critical": False},
            {"id": "rw2", "zh": "是否覆盖传统方法、近期深度学习方法、与本文最相关的方法", "critical": True},
            {"id": "rw3", "zh": "是否指出已有工作不足并自然引出本文设计", "critical": True},
            {"id": "rw4", "zh": "是否避免 '某某提出了，某某又提出了' 的堆砌式写法", "critical": False},
            {"id": "rw5", "zh": "是否所有重要论断都有引用支撑", "critical": False},
            {"id": "rw6", "zh": "是否避免引用与本文关系不大的文献", "critical": False},
        ],
    },
    "method": {
        "id": 5,
        "name_zh": "Method 检查",
        "name_en": "Method",
        "checks": [
            {"id": "me1", "zh": "方法模块命名是否全文统一", "critical": True},
            {"id": "me2", "zh": "每个模块的输入、输出、职责是否清楚", "critical": True},
            {"id": "me3", "zh": "变量符号是否统一且每个符号都已定义", "critical": True},
            {"id": "me4", "zh": "图、正文、伪代码是否三者一致", "critical": True},
            {"id": "me5", "zh": "是否存在图中画了但正文未解释的模块", "critical": True},
            {"id": "me6", "zh": "哪些模块读 memory / 哪些模块写 memory 是否明确", "critical": False},
            {"id": "me7", "zh": "哪些路径是训练/实验路径、哪些是在线应用路径是否区分清楚", "critical": False},
            {"id": "me8", "zh": "是否存在未确认结果直接进入最终输出或长期记忆的问题", "critical": False},
        ],
    },
    "figures": {
        "id": 6,
        "name_zh": "图件检查",
        "name_en": "Figures",
        "checks": [
            {"id": "fi1", "zh": "每张图是否有明确作用且不与其他图重复", "critical": False},
            {"id": "fi2", "zh": "图中术语是否和正文完全一致", "critical": True},
            {"id": "fi3", "zh": "缩小到论文实际尺寸后是否仍可读", "critical": True},
            {"id": "fi4", "zh": "图注是否解释图的作用而非简单重复图中文字", "critical": False},
            {"id": "fi5", "zh": "是否使用高清 PDF 或矢量图（非低分辨率 PNG）", "critical": False},
            {"id": "fi6", "zh": "Introduction 图和 Method 图功能是否区分", "critical": False},
            {"id": "fi7", "zh": "图中是否无旧命名、草稿痕迹或 PPT 痕迹", "critical": True},
            {"id": "fi8", "zh": "图中文字是否不过多、线条是否清楚、文字箭头图片是否不重叠", "critical": False},
        ],
    },
    "tables": {
        "id": 7,
        "name_zh": "表格检查",
        "name_en": "Tables",
        "checks": [
            {"id": "ta1", "zh": "表格标题是否清楚，指标单位是否明确", "critical": True},
            {"id": "ta2", "zh": "最优值是否加粗", "critical": False},
            {"id": "ta3", "zh": "是否所有方法都在正文或 Related Work 中交代过", "critical": False},
            {"id": "ta4", "zh": "是否有缺失值但未说明", "critical": False},
            {"id": "ta5", "zh": "是否所有表格都被正文引用", "critical": False},
            {"id": "ta6", "zh": "是否有过多小数位（通常 1-3 位足够）", "critical": False},
            {"id": "ta7", "zh": "表格是否太宽导致溢出", "critical": False},
            {"id": "ta8", "zh": "表格和正文描述是否一致", "critical": True},
            {"id": "ta9", "zh": "核心实验表格是否需要报告标准差、置信区间或显著性检验", "critical": False},
        ],
    },
    "experiments": {
        "id": 8,
        "name_zh": "实验设计检查",
        "name_en": "Experiments",
        "checks": [
            {"id": "ex1", "zh": "数据集是否介绍清楚，数据划分是否明确", "critical": True},
            {"id": "ex2", "zh": "检索库是否排除了测试样本（无数据泄漏）", "critical": True},
            {"id": "ex3", "zh": "baseline 是否公平，是否说明模型版本、参数设置、评估指标", "critical": True},
            {"id": "ex4", "zh": "是否包含主实验、对比实验、消融实验", "critical": True},
            {"id": "ex5", "zh": "是否解释失败案例或性能下降情况", "critical": False},
            {"id": "ex6", "zh": "每个实验是否对应一个研究问题", "critical": False},
            {"id": "ex7", "zh": "是否避免只挑有利结果", "critical": True},
            {"id": "ex8", "zh": "是否覆盖：方法整体有效性 / vs 直接模型 / vs 检索增强 / vs agent 框架 / 每个组件必要性 / 结果稳定性", "critical": False},
        ],
    },
    "statistics": {
        "id": 9,
        "name_zh": "统计与显著性检查",
        "name_en": "Statistics & Significance",
        "checks": [
            {"id": "st1", "zh": "是否报告 mean ± std", "critical": True},
            {"id": "st2", "zh": "是否有多折实验或重复实验", "critical": False},
            {"id": "st3", "zh": "核心对比是否需要显著性检验（McNemar / bootstrap CI / paired test 等）", "critical": False},
            {"id": "st4", "zh": "是否避免用单次数值支撑过强结论", "critical": True},
            {"id": "st5", "zh": "是否说明统计检验方法和显著性水平（如 p<0.05）", "critical": False},
            {"id": "st6", "zh": "最核心的主结果或消融结果是否有统计说明", "critical": False},
        ],
    },
    "terminology": {
        "id": 10,
        "name_zh": "术语一致性检查",
        "name_en": "Terminology Consistency",
        "checks": [
            {"id": "tm1", "zh": "方法名是否全文一致", "critical": True},
            {"id": "tm2", "zh": "agent / module / component 是否混用", "critical": False},
            {"id": "tm3", "zh": "图中、正文中、表格中命名是否一致", "critical": True},
            {"id": "tm4", "zh": "数据集缩写是否首次出现时解释", "critical": False},
            {"id": "tm5", "zh": "是否存在旧版本命名残留、草稿词或内部代号", "critical": True},
            {"id": "tm6", "zh": "指标名是否统一（如 Precision@5 vs Prec@5）", "critical": False},
        ],
    },
    "language": {
        "id": 11,
        "name_zh": "语言风格检查",
        "name_en": "Language Style",
        "checks": [
            {"id": "la1", "zh": "是否避免口语化表达", "critical": False},
            {"id": "la2", "zh": "是否避免绝对化词汇（always, never, completely, fully, only）", "critical": True},
            {"id": "la3", "zh": "是否避免空泛词汇（novel, powerful, robust）除非有实验证据", "critical": False},
            {"id": "la4", "zh": "是否使用 shows/suggests/indicates 而非 proves/guarantees", "critical": False},
            {"id": "la5", "zh": "是否避免重复表达和过长句", "critical": False},
            {"id": "la6", "zh": "是否避免过多修饰词和没有必要的复杂术语", "critical": False},
        ],
    },
    "citations": {
        "id": 12,
        "name_zh": "引用检查",
        "name_en": "Citations",
        "checks": [
            {"id": "ci1", "zh": "所有 citation key 是否存在", "critical": True},
            {"id": "ci2", "zh": "是否有 undefined citation", "critical": True},
            {"id": "ci3", "zh": "是否引用最新和最相关工作", "critical": False},
            {"id": "ci4", "zh": "是否有关键背景缺引用", "critical": True},
            {"id": "ci5", "zh": "正文 citation key 与参考文献列表是否一一对应", "critical": True},
            {"id": "ci6", "zh": "是否在同一句话堆过多引用", "critical": False},
            {"id": "ci7", "zh": "是否引用了不可靠来源（arXiv 未审稿预印本需标注）", "critical": False},
            {"id": "ci8", "zh": "参考文献格式是否一致", "critical": False},
            {"id": "ci9", "zh": "如使用 thebibliography：bibitem key 是否唯一、begin/end 是否完整", "critical": True},
        ],
    },
    "latex_compile": {
        "id": 13,
        "name_zh": "LaTeX 编译检查",
        "name_en": "LaTeX Compilation",
        "checks": [
            {"id": "lx1", "zh": "是否有真正的 LaTeX Error", "critical": True},
            {"id": "lx2", "zh": "是否有 missing figure", "critical": True},
            {"id": "lx3", "zh": "是否有 undefined references 或 undefined citations", "critical": True},
            {"id": "lx4", "zh": "是否有 overfull hbox", "critical": False},
            {"id": "lx5", "zh": "是否连续编译 2-3 次", "critical": False},
            {"id": "lx6", "zh": "是否有 underfull hbox", "critical": False},
            {"id": "lx7", "zh": "是否有图片路径错误", "critical": True},
            {"id": "lx8", "zh": "图表编号是否正确、交叉引用是否正确", "critical": True},
            {"id": "lx9", "zh": "是否删除临时强制分页（clearpage/newpage）导致的大空白", "critical": False},
            {"id": "lx10", "zh": "所有图文件是否都在正确目录", "critical": True},
        ],
    },
    "layout": {
        "id": 14,
        "name_zh": "排版检查",
        "name_en": "Layout",
        "checks": [
            {"id": "lo1", "zh": "页面是否有大面积空白", "critical": False},
            {"id": "lo2", "zh": "双栏是否严重不平衡", "critical": False},
            {"id": "lo3", "zh": "图表是否离首次引用太远", "critical": False},
            {"id": "lo4", "zh": "是否有孤立标题（orphan heading）", "critical": False},
            {"id": "lo5", "zh": "表格是否过宽", "critical": False},
            {"id": "lo6", "zh": "图是否过小", "critical": False},
            {"id": "lo7", "zh": "caption 是否过长", "critical": False},
            {"id": "lo8", "zh": "是否一页只有很少文字", "critical": False},
            {"id": "lo9", "zh": "bibliography 前是否被强制分页", "critical": False},
            {"id": "lo10", "zh": "最后一页是否需要手动平衡双栏", "critical": False},
        ],
    },
    "blind_review": {
        "id": 15,
        "name_zh": "盲审与合规检查",
        "name_en": "Blind Review & Compliance",
        "checks": [
            {"id": "br1", "zh": "作者姓名是否匿名", "critical": True},
            {"id": "br2", "zh": "单位和项目名是否隐藏", "critical": True},
            {"id": "br3", "zh": "公司名/医院名是否暴露身份", "critical": True},
            {"id": "br4", "zh": "致谢是否删除", "critical": True},
            {"id": "br5", "zh": "数据来源是否会暴露作者身份", "critical": False},
            {"id": "br6", "zh": "图中是否有 logo/路径/水印", "critical": True},
            {"id": "br7", "zh": "PDF metadata 是否含作者信息", "critical": True},
            {"id": "br8", "zh": "supplementary material 是否也匿名", "critical": True},
            {"id": "br9", "zh": "医学/临床论文：是否避免过度临床声明、说明伦理和数据使用条件", "critical": False},
        ],
    },
    "final_submission": {
        "id": 16,
        "name_zh": "最终提交前检查",
        "name_en": "Final Submission Readiness",
        "checks": [
            {"id": "fs1", "zh": "看标题能否知道论文主题", "critical": True},
            {"id": "fs2", "zh": "看摘要能否知道方法和结果", "critical": True},
            {"id": "fs3", "zh": "看 Fig.1 能否知道创新点", "critical": False},
            {"id": "fs4", "zh": "看 Fig.2 能否知道方法流程", "critical": False},
            {"id": "fs5", "zh": "看实验表能否支撑贡献", "critical": True},
            {"id": "fs6", "zh": "看结论是否不过度", "critical": True},
            {"id": "fs7", "zh": "看全文是否没有旧版本痕迹（TODO, FIXME, 草稿, 内部代号）", "critical": True},
            {"id": "fs8", "zh": "看编译 log 是否没有严重错误", "critical": True},
            {"id": "fs9", "zh": "看 PDF 100% 缩放是否清楚", "critical": False},
            {"id": "fs10", "zh": "看图表是否能在打印版中阅读", "critical": False},
        ],
    },
}

# ═══════════════════════════════════════════════════════════════
# Fix suggestion templates — per check_id
# ═══════════════════════════════════════════════════════════════

FIX_SUGGESTIONS = {
    "rc1": {"zh": "在摘要和引言中明确写出 '本文解决的问题是……' 一句", "en": "State explicitly: 'This paper addresses the problem of...'"},
    "rc2": {"zh": "在 Introduction 末尾用 '与已有工作不同，本文……' 明确区分", "en": "End introduction with 'Unlike prior work, we...'"},
    "rc3": {"zh": "确保方法名在 Introduction/Method/Experiment/Conclusion 中至少各出现一次", "en": "Ensure method name appears in Intro, Method, Experiments, and Conclusion"},
    "rc4": {"zh": "每个 contribution 应包含具体数字或机制，如 'improves recall by X% on dataset Y'", "en": "Make each contribution measurable with numbers or mechanisms"},
    "rc5": {"zh": "检查 Conclusion 中是否有 'guarantees'/'proves'/'彻底解决' 等词，替换为 'suggests'/'indicates'", "en": "Replace absolute language (guarantees, proves) with measured language (suggests, indicates)"},
    "rc6": {"zh": "对比 Abstract 和 Conclusion 中问题描述的关键词是否一致", "en": "Check that problem description keywords match between Abstract and Conclusion"},

    "ab1": {"zh": "摘要应按 背景→方法→实验→结果→局限 五段式组织", "en": "Structure abstract as: background → method → experiments → results → limitations"},
    "ab2": {"zh": "删除 'revolutionary'/'game-changing'/'颠覆性' 等宣传词", "en": "Remove promotional language like 'revolutionary' or 'game-changing'"},
    "ab3": {"zh": "每个性能声明后添加具体数字，如 '(15.3% improvement, p<0.01)'", "en": "Back every performance claim with specific numbers and significance"},
    "ab4": {"zh": "摘要中出现的新术语必须在正文中有定义", "en": "Every new term in abstract must be defined in the body"},
    "ab5": {"zh": "将辅助实验的声明移到正文实验部分，摘要中只保留主实验", "en": "Move supplementary experiment claims to body; keep only main results in abstract"},
    "ab6": {"zh": "在摘要中明确写 '我们提出了 X 方法，在 Y 数据集上达到 Z 性能，局限是……'", "en": "State clearly: 'We propose X, achieving Z on Y datasets, with limitations...'"},

    "in1": {"zh": "第一段应以现实应用场景或公认困难开头，不以 'Recently...' 或 'With the development of...' 开头", "en": "Start with real-world application or recognized difficulty, not 'Recently...'"},
    "in2": {"zh": "前两段只引入 2-3 个核心概念，其余术语推迟到 Method 部分解释", "en": "Introduce only 2-3 core concepts in first two paragraphs"},
    "in3": {"zh": "Introduction 中每个提到的痛点应在 Method 中有对应的解决方案", "en": "Each pain point in intro should map to a specific method component"},
    "in4": {"zh": "在 Introduction 的 contribution 段落中明确写 '本文的创新点是……'", "en": "State the innovation explicitly in the contribution paragraph"},
    "in5": {"zh": "Fig.1 应突出本文方法与已有方法的本质区别，而非只画系统流程图", "en": "Fig.1 should highlight the key difference from prior work, not just system flow"},
    "in6": {"zh": "contribution list 用编号列出，每条包含：做了什么 + 在哪个数据集上 + 达到了什么效果", "en": "Number contributions; each includes: what was done + on which dataset + what result"},

    "rw1": {"zh": "Related Work 应按主题分 3-5 个小节，每节有明确主题句", "en": "Organize Related Work into 3-5 thematic subsections with topic sentences"},
    "rw2": {"zh": "确保引用至少 3 篇近 2 年的顶会/顶刊工作", "en": "Ensure at least 3 recent (2yr) top-venue works are cited"},
    "rw3": {"zh": "每个小节末尾用 'However...' 或 '但……' 指出不足，自然引出本文", "en": "End each subsection with gaps that motivate your approach"},
    "rw4": {"zh": "将 'Author A proposed X. Author B proposed Y.' 改为按主题归类比较", "en": "Group by theme, not author-by-author list"},
    "rw5": {"zh": "每个论断后面应有 \\cite{} 支撑", "en": "Every claim should be followed by a citation"},
    "rw6": {"zh": "删除与本文方法无直接关联的引用", "en": "Remove citations not directly relevant to your method"},

    "me1": {"zh": "全文搜索方法名，确保所有出现处拼写和大小写一致", "en": "Search the full text for method name; ensure consistent spelling and casing"},
    "me2": {"zh": "对每个模块用一句话说明 '输入 X → 模块 → 输出 Y'", "en": "For each module, state 'Input X → Module → Output Y'"},
    "me3": {"zh": "在 Method 第一节列出所有符号及其定义", "en": "List all symbols with definitions in a notation table or first Method paragraph"},
    "me4": {"zh": "逐行对比伪代码和图中的步骤是否一一对应", "en": "Cross-check each pseudocode line against figure steps"},
    "me5": {"zh": "检查图中每个方框是否在正文中有对应的文字解释", "en": "Ensure every box in figures has corresponding text explanation"},
    "me6": {"zh": "在 Method 图中用不同颜色或标注区分 read 和 write 操作", "en": "Use distinct colors/labels to mark read vs write operations in figures"},
    "me7": {"zh": "在 Method 末尾添加 'Training vs Inference' 小节说明路径差异", "en": "Add 'Training vs Inference' subsection at end of Method"},
    "me8": {"zh": "添加确认机制：模块输出需经过验证才能进入长期记忆", "en": "Add confirmation gate: module outputs must be validated before entering long-term memory"},

    "fi1": {"zh": "检查每张图是否回答一个不同的问题，重复的图合并或删除", "en": "Ensure each figure answers a distinct question; merge or remove duplicates"},
    "fi2": {"zh": "全文搜索图中出现的术语，确保与正文拼写一致", "en": "Search figure terminology against text to ensure consistency"},
    "fi3": {"zh": "将图缩小到最终出版尺寸（通常单栏 8.5cm），确认文字和线条仍可辨识", "en": "Shrink figures to final print size (single column ~8.5cm) and verify readability"},
    "fi4": {"zh": "图注第一句应说明图展示的结论，而非描述图中有什么", "en": "Caption first sentence should state the conclusion, not describe what's shown"},
    "fi5": {"zh": "用 PDF/EPS/SVG 矢量格式，避免低分辨率 PNG/JPG", "en": "Use PDF/EPS/SVG vector formats; avoid low-res PNG/JPG"},
    "fi6": {"zh": "Introduction 图突出创新差异，Method 图突出运行流程，两者不能承担同一功能", "en": "Intro figure highlights innovation; Method figure shows workflow. Different purposes."},
    "fi7": {"zh": "检查图中是否有 'draft'/'v2'/'old' 等草稿标注", "en": "Check for draft labels like 'draft'/'v2'/'old' in figures"},
    "fi8": {"zh": "简化图中文字至 8-12 个关键词以内，检查线条和箭头不重叠", "en": "Simplify figure text to 8-12 keywords; check lines and arrows don't overlap"},

    "ta1": {"zh": "表格标题格式: 'Table X: 方法在数据集上的指标 (mean±std)'", "en": "Table caption format: 'Table X: Metric (mean±std) of methods on dataset'"},
    "ta2": {"zh": "用 \\textbf{} 加粗每列最优值", "en": "Bold the best value in each column with \\textbf{}"},
    "ta3": {"zh": "表格中出现的每个方法名应在正文或 Related Work 中有介绍", "en": "Every method name in tables should be introduced in text or Related Work"},
    "ta4": {"zh": "缺失值用 '-' 或 'N/A' 标注，并在表注中说明原因", "en": "Mark missing values with '-' or 'N/A' and explain in table note"},
    "ta5": {"zh": "用 grep 搜索每个 \\ref{table:...} 确认被正文引用", "en": "Grep for each \\ref{table:...} to confirm it's cited in text"},
    "ta6": {"zh": "将小数位统一为 1-3 位，如 0.856 → 0.86", "en": "Round all decimals to 1-3 places, e.g. 0.856 → 0.86"},
    "ta7": {"zh": "使用 table* 环境或缩小字体以适应双栏", "en": "Use table* environment or smaller font to fit within column width"},
    "ta8": {"zh": "正文中提到的数字必须与表格中一致", "en": "Every number cited in text must match the table exactly"},
    "ta9": {"zh": "核心表格添加标准差列或显著性标记（*, **, ***）", "en": "Add std columns or significance markers (*, **, ***) to core tables"},

    "ex1": {"zh": "在实验部分第一节明确写数据集名称、样本数、划分方式（如 8:1:1）", "en": "State dataset name, size, and split ratio (e.g. 8:1:1) in first experiment subsection"},
    "ex2": {"zh": "确认测试集样本从未出现在检索库或训练集中", "en": "Confirm test samples never appear in retrieval corpus or training set"},
    "ex3": {"zh": "对每个 baseline 注明模型版本（如 GPT-4-0613）、参数设置和出处", "en": "Note model version (e.g. GPT-4-0613), parameters, and source for each baseline"},
    "ex4": {"zh": "确保包含：主结果表 + 与 baseline 对比 + 消融实验（逐个去掉组件）", "en": "Ensure: main results table + baseline comparison + ablation (remove each component)"},
    "ex5": {"zh": "添加 'Error Analysis' 小节，展示 3-5 个典型失败案例并分析原因", "en": "Add 'Error Analysis' subsection with 3-5 representative failure cases"},
    "ex6": {"zh": "每个实验小节开头写 '本实验回答的问题是……'", "en": "Start each experiment subsection with 'This experiment answers...'"},
    "ex7": {"zh": "检查是否报告了所有方法的全部指标，而非只选有利的", "en": "Verify all metrics are reported for all methods, not cherry-picked"},
    "ex8": {"zh": "补充实验矩阵：整体有效性 / vs 直接模型 / vs 检索增强 / vs agent 框架 / 消融 / 稳定性", "en": "Fill experiment matrix: overall / vs direct model / vs retrieval / vs agent / ablation / stability"},

    "st1": {"zh": "所有数值结果以 mean ± std 格式报告，如 '85.3 ± 2.1'", "en": "Report all numeric results as mean ± std, e.g. '85.3 ± 2.1'"},
    "st2": {"zh": "至少进行 3 折交叉验证或 5 次随机种子重复实验", "en": "Run at least 3-fold CV or 5 random-seed repetitions"},
    "st3": {"zh": "分类任务用 McNemar test，回归用 paired t-test，或使用 bootstrap 95% CI", "en": "Use McNemar for classification, paired t-test for regression, or bootstrap 95% CI"},
    "st4": {"zh": "避免写 'our method is better'，改为 'our method outperforms baselines by X% (p<0.05)'", "en": "Replace 'our method is better' with 'outperforms by X% (p<0.05)'"},
    "st5": {"zh": "在实验设置中写 'Statistical significance was assessed using X test at α=0.05'", "en": "State: 'Significance assessed using X test at α=0.05' in experimental setup"},
    "st6": {"zh": "主结果表和消融实验图至少标注标准差", "en": "At minimum, report std on main results table and ablation figure"},

    "tm1": {"zh": "用 grep/case-sensitive-search 检查方法名是否只有一种写法", "en": "Case-sensitive search to verify method name has exactly one spelling"},
    "tm2": {"zh": "全文统一用 'module' 或 'component'，不要混用", "en": "Pick 'module' or 'component' and use consistently"},
    "tm3": {"zh": "交叉检查图中标注名、正文引用名、表格方法名三者一致", "en": "Cross-check figure labels, text references, and table method names"},
    "tm4": {"zh": "搜索每个缩写首次出现处，确认有全称 + (缩写) 格式", "en": "Search first occurrence of each abbreviation; confirm full name + (abbr) format"},
    "tm5": {"zh": "搜索 'old'/'v1'/'v2'/'legacy'/'deprecated' 等词，确认无残留", "en": "Search for 'old'/'v1'/'legacy' to ensure no residual names"},
    "tm6": {"zh": "全文统一指标名，如统一用 Precision@5 而非混用 Prec@5 和 P@5", "en": "Standardize metric names; use Precision@5 consistently, not Prec@5 and P@5"},

    "la1": {"zh": "搜索 'a lot'/'kind of'/'pretty'/'really'/'actually'/'basically' 等口语词并替换", "en": "Search for colloquialisms like 'a lot'/'kind of' and replace"},
    "la2": {"zh": "搜索 'always'/'never'/'completely'/'fully'/'only' 并改为限定表达", "en": "Replace absolutes (always, never, completely) with qualified expressions"},
    "la3": {"zh": "搜索 'novel'/'powerful'/'robust'/'state-of-the-art' 确认有实验证据支撑", "en": "Verify each use of 'novel'/'powerful'/'robust' is backed by experimental evidence"},
    "la4": {"zh": "搜索 'proves'/'guarantees' 替换为 'shows'/'suggests'/'indicates'/'supports'", "en": "Replace 'proves'/'guarantees' with 'shows'/'suggests'/'indicates'"},
    "la5": {"zh": "检查是否有超过 40 词的句子，考虑拆分", "en": "Check for sentences >40 words; consider splitting"},
    "la6": {"zh": "搜索 'very'/'extremely'/'highly'/'remarkably' 等修饰词，删除或替换为具体数字", "en": "Replace intensifiers (very, extremely, remarkably) with specific numbers"},

    "ci1": {"zh": "用 grep 提取所有 \\cite{...}，与 .bib 文件中的 key 逐一比对", "en": "Extract all \\cite{...} keys and cross-check against .bib file entries"},
    "ci2": {"zh": "编译后搜索 log 中的 'Citation ... undefined' 警告", "en": "Search compile log for 'Citation ... undefined' warnings"},
    "ci3": {"zh": "检查最近 2 年顶会/顶刊的相关工作是否被引用", "en": "Check that recent (2yr) top-venue related work is cited"},
    "ci4": {"zh": "检查 Introduction 中的背景陈述是否有引用支撑", "en": "Verify background claims in Introduction have citation support"},
    "ci5": {"zh": "确保 \\cite{a} 和 \\bibitem{a} 中的 key 完全一致", "en": "Ensure \\cite{a} and \\bibitem{a} keys match exactly"},
    "ci6": {"zh": "同一处引用不超过 4 个，分散到不同句子", "en": "Limit to ≤4 citations per point; distribute across sentences"},
    "ci7": {"zh": "arXiv 引用标注 'preprint'，不当作已审稿论文", "en": "Mark arXiv citations as 'preprint'; don't treat as peer-reviewed"},
    "ci8": {"zh": "检查所有参考文献格式是否统一（作者名大小写、会议/期刊名缩写、页码格式）", "en": "Verify consistent formatting across all references"},
    "ci9": {"zh": "检查 \\begin{thebibliography}{99} 和 \\end{thebibliography} 配对，每个 \\bibitem{} key 唯一", "en": "Check thebibliography begin/end pairing and unique bibitem keys"},

    "lx1": {"zh": "编译 log 中搜索 '!' (Error)，优先修复所有 Error", "en": "Search compile log for '!' (Error); fix all errors first"},
    "lx2": {"zh": "编译 log 中搜索 'not found' 或 'missing file'", "en": "Search compile log for 'not found' or 'missing file'"},
    "lx3": {"zh": "编译 log 中搜索 'undefined' 和 'reference'", "en": "Search compile log for 'undefined' and 'reference'"},
    "lx4": {"zh": "编译 log 中搜索 'overfull'，调整换行或微调文字", "en": "Search for 'overfull' in log; adjust line breaks or wording"},
    "lx5": {"zh": "确保运行 pdflatex/latexmk 至少 2-3 次以解析所有交叉引用", "en": "Run pdflatex/latexmk at least 2-3 times to resolve all cross-references"},
    "lx6": {"zh": "编译 log 中搜索 'underfull'，通常可忽略但过多时需调整", "en": "Search for 'underfull' in log; usually ignorable but fix if excessive"},
    "lx7": {"zh": "检查所有 \\includegraphics 路径指向的文件是否存在", "en": "Verify all \\includegraphics paths point to existing files"},
    "lx8": {"zh": "交叉检查 \\label 和 \\ref 是否一一对应，图表编号是否连续", "en": "Cross-check \\label and \\ref pairs; verify figure/table numbering is sequential"},
    "lx9": {"zh": "删除不必要的 \\clearpage 和 \\newpage", "en": "Remove unnecessary \\clearpage and \\newpage"},
    "lx10": {"zh": "检查 figures/ 目录下是否缺少任何被引用的图文件", "en": "Check figures/ directory for any missing referenced files"},

    "lo1": {"zh": "检查每页底部是否有超过 3cm 的空白", "en": "Check for >3cm blank space at bottom of any page"},
    "lo2": {"zh": "最后一页双栏高度差不应超过 5 行", "en": "Last page two-column height difference should not exceed 5 lines"},
    "lo3": {"zh": "图表应放在首次引用后 1 页以内", "en": "Figures/tables should appear within 1 page of first citation"},
    "lo4": {"zh": "检查节标题是否出现在页面最后一行（孤标题）", "en": "Check for section headings as the last line on a page"},
    "lo5": {"zh": "使用 table* 或 resizebox 处理过宽表格", "en": "Use table* or resizebox for overwide tables"},
    "lo6": {"zh": "图的最小文字不应小于 6pt", "en": "Minimum figure text size should be ≥6pt"},
    "lo7": {"zh": "图注和表注不超过 3 行", "en": "Figure/table captions should not exceed 3 lines"},
    "lo8": {"zh": "避免一页只有 2-3 行文字其余全是空白", "en": "Avoid pages with only 2-3 lines of text"},
    "lo9": {"zh": "检查 \\bibliography 前是否有 \\clearpage，如需压缩可删除", "en": "Check for \\clearpage before \\bibliography; remove if compressing"},
    "lo10": {"zh": "使用 \\balance 或手动调整最后一页双栏", "en": "Use \\balance or manually balance last page columns"},

    "br1": {"zh": "将 \\author{...} 替换为 \\author{Anonymous Authors} 或删除", "en": "Replace \\author{...} with \\author{Anonymous Authors} or remove"},
    "br2": {"zh": "删除 \\affiliation 和 \\institute 中的机构信息", "en": "Remove institution info from \\affiliation and \\institute"},
    "br3": {"zh": "搜索公司名/医院名，替换为通用描述", "en": "Replace company/hospital names with generic descriptions"},
    "br4": {"zh": "删除或注释 \\section*{Acknowledgments} 整节", "en": "Remove or comment out the entire Acknowledgments section"},
    "br5": {"zh": "将 'collected from our hospital' 改为 'collected from a tertiary hospital'", "en": "Replace 'collected from our X' with 'collected from a tertiary X'"},
    "br6": {"zh": "检查图中是否有机构 logo、文件路径 (C:\\Users\\...)、个人水印", "en": "Check figures for logos, file paths, or personal watermarks"},
    "br7": {"zh": "用 PDF 属性查看器检查作者/单位字段是否清空", "en": "Check PDF properties to verify author/affiliation fields are empty"},
    "br8": {"zh": "对 supplementary material 执行同样的匿名检查", "en": "Apply the same anonymization checks to supplementary material"},
    "br9": {"zh": "添加 'This model is for research only, not for clinical diagnosis' 声明", "en": "Add disclaimer: 'For research only, not for clinical diagnosis'"},

    "fs1": {"zh": "标题应包含方法名和任务名，如 'X: A Y Approach for Z'", "en": "Title should include method name and task: 'X: A Y Approach for Z'"},
    "fs2": {"zh": "摘要应独立可理解，不依赖正文就能知道做了什么", "en": "Abstract should be self-contained and understandable without the body"},
    "fs3": {"zh": "Fig.1 应让审稿人在 30 秒内理解本文创新", "en": "Fig.1 should communicate innovation within 30 seconds"},
    "fs4": {"zh": "Fig.2 应清晰展示从输入到输出的完整流程", "en": "Fig.2 should clearly show the complete input-to-output pipeline"},
    "fs5": {"zh": "主实验表应直接支撑 contribution list 中的每条声明", "en": "Main results table should directly support each contribution claim"},
    "fs6": {"zh": "Conclusion 中每个结论前加限定条件，如 'On these benchmarks...'", "en": "Prefix each conclusion with qualifiers, e.g. 'On these benchmarks...'"},
    "fs7": {"zh": "全文搜索 TODO/FIXME/???/草稿/待补充，全部删除或完成", "en": "Search for TODO/FIXME/???/draft; resolve or remove all"},
    "fs8": {"zh": "最终编译 log 中不应有 Error 或 Warning（或 Warning 数 < 5）", "en": "Final compile log should have zero Errors and <5 Warnings"},
    "fs9": {"zh": "在 PDF 阅读器中 100% 缩放逐页检查", "en": "Review every page at 100% zoom in PDF viewer"},
    "fs10": {"zh": "黑白打印后检查图表是否仍可区分（色盲友好）", "en": "Print in B/W and verify figures/tables remain distinguishable (colorblind-safe)"},
}

# ═══════════════════════════════════════════════════════════════
# LaTeX-aware text extraction
# ═══════════════════════════════════════════════════════════════

def _extract_latex_sections(text: str) -> dict:
    """Parse LaTeX document into sections for targeted analysis."""
    sections = {}
    # Find abstract
    abs_match = re.search(
        r'\\begin\{abstract\}(.*?)\\end\{abstract\}', text, re.DOTALL | re.IGNORECASE
    )
    if abs_match:
        sections["abstract"] = abs_match.group(1)

    # Find introduction (from \section{Introduction} to next \section)
    intro_match = re.search(
        r'\\section\*?\{(\w*\s*introduction|\w*\s*intro)\}(.*?)(?=\\section\*?\{)',
        text, re.DOTALL | re.IGNORECASE
    )
    if intro_match:
        sections["introduction"] = intro_match.group(2)

    # Find related work
    rw_match = re.search(
        r'\\section\*?\{(\w*\s*related\s*work|\w*\s*background)\}(.*?)(?=\\section\*?\{)',
        text, re.DOTALL | re.IGNORECASE
    )
    if rw_match:
        sections["related_work"] = rw_match.group(2)

    # Find method
    method_match = re.search(
        r'\\section\*?\{(\w*\s*method|\w*\s*approach|\w*\s*framework|\w*\s*model)\}(.*?)(?=\\section\*?\{)',
        text, re.DOTALL | re.IGNORECASE
    )
    if method_match:
        sections["method"] = method_match.group(2)

    # Find experiments
    exp_match = re.search(
        r'\\section\*?\{(\w*\s*experiment|\w*\s*evaluation|\w*\s*result)\}(.*?)(?=\\section\*?\{)',
        text, re.DOTALL | re.IGNORECASE
    )
    if exp_match:
        sections["experiments"] = exp_match.group(2)

    # Find conclusion
    concl_match = re.search(
        r'\\section\*?\{(\w*\s*conclusion|\w*\s*discussion)\}(.*?)(?=\\section\*?\{|\\bibliograph|\\end\{document\})',
        text, re.DOTALL | re.IGNORECASE
    )
    if concl_match:
        sections["conclusion"] = concl_match.group(2)

    sections["_full"] = text
    return sections


def _extract_citation_keys(text: str) -> list:
    """Extract all citation keys from \\cite{...} patterns."""
    keys = []
    for m in re.finditer(r'\\cite\s*\{([^}]+)\}', text):
        for k in m.group(1).split(','):
            keys.append(k.strip())
    return keys


def _extract_bibitem_keys(text: str) -> list:
    """Extract keys from \\bibitem{...} in thebibliography."""
    return re.findall(r'\\bibitem\s*\{([^}]+)\}', text)


def _extract_figure_paths(text: str) -> list:
    """Extract image paths from \\includegraphics."""
    paths = []
    for m in re.finditer(r'\\includegraphics(?:\[.*?\])?\s*\{([^}]+)\}', text):
        paths.append(m.group(1))
    return paths


def _extract_label_ref_pairs(text: str) -> tuple:
    """Extract all \\label{...} and \\ref{...} keys."""
    labels = set(re.findall(r'\\label\s*\{([^}]+)\}', text))
    refs = set()
    for m in re.finditer(r'\\ref\s*\{([^}]+)\}', text):
        refs.add(m.group(1))
    # Also check \\cite as refs
    return labels, refs


def _count_sentence_lengths(text: str) -> list:
    """Count words per sentence, return sentences >40 words."""
    # Remove LaTeX commands for cleaner analysis
    clean = re.sub(r'\\[a-zA-Z]+\{', '', text)
    clean = re.sub(r'\\[a-zA-Z]+', '', clean)
    clean = re.sub(r'\{|\}', '', clean)
    sentences = re.split(r'(?<=[.!?])\s+', clean)
    long_sentences = []
    for s in sentences:
        words = s.split()
        if len(words) > 40:
            long_sentences.append({"text": s[:120] + "...", "word_count": len(words)})
    return long_sentences


def _find_duplicate_bibitem_keys(text: str) -> list:
    """Find duplicate keys in thebibliography."""
    keys = _extract_bibitem_keys(text)
    counts = Counter(keys)
    return [k for k, v in counts.items() if v > 1]


# ═══════════════════════════════════════════════════════════════
# Enhanced heuristics with LaTeX-aware analysis
# ═══════════════════════════════════════════════════════════════

def score_dimension(dim_key: str, paper_text: str, sections: dict,
                    strictness: str, paper_dir: str = "") -> dict:
    """Score one dimension with full heuristic analysis."""
    dim = DIMENSIONS[dim_key]
    total = len(dim["checks"])
    issues = []
    passed = 0

    for check in dim["checks"]:
        check_id = check["id"]
        severity = "critical" if check.get("critical") else "warning"
        result, evidence = _check_heuristic(dim_key, check_id, paper_text, sections, paper_dir)

        fix = FIX_SUGGESTIONS.get(check_id, {})
        fix_zh = fix.get("zh", "")
        fix_en = fix.get("en", "")

        if result is True:
            passed += 1
        elif result is False:
            issues.append({
                "check_id": check_id,
                "severity": severity,
                "zh": check["zh"],
                "en": check.get("en", check["zh"]),
                "finding": "not_found" if severity == "critical" else "needs_review",
                "evidence": evidence or "",
                "fix_zh": fix_zh,
                "fix_en": fix_en,
            })
        else:
            issues.append({
                "check_id": check_id,
                "severity": "suggestion",
                "zh": check["zh"],
                "en": check.get("en", check["zh"]),
                "finding": "needs_manual_review",
                "evidence": evidence or "",
                "fix_zh": fix_zh,
                "fix_en": fix_en,
            })

    # Calculate score
    if total == 0:
        score = 5
    else:
        ratio = passed / total
        if strictness == "critical":
            score = max(1, round(ratio * 5))
        elif strictness == "friendly":
            score = max(2, round(ratio * 5 + 0.5))
        else:
            score = max(1, round(ratio * 5))

    return {
        "dimension": dim_key,
        "id": dim["id"],
        "name_zh": dim["name_zh"],
        "name_en": dim["name_en"],
        "score": score,
        "max_score": 5,
        "checks_total": total,
        "checks_passed": passed,
        "issues": issues,
        "critical_count": sum(1 for i in issues if i["severity"] == "critical"),
        "warning_count": sum(1 for i in issues if i["severity"] == "warning"),
        "suggestion_count": sum(1 for i in issues if i["severity"] == "suggestion"),
    }


def _check_heuristic(dim_key: str, check_id: str, text: str,
                     sections: dict, paper_dir: str) -> tuple:
    """Returns (True|False|None, evidence_string)."""
    lowered = text.lower() if text else ""
    abs_text = (sections.get("abstract") or "").lower()
    intro_text = (sections.get("introduction") or "").lower()
    method_text = (sections.get("method") or "").lower()
    exp_text = (sections.get("experiments") or "").lower()
    concl_text = (sections.get("conclusion") or "").lower()

    # ── Research coherence ──
    if check_id == "rc1":
        found = bool(re.search(r"(problem|challenge|issue|limitation|gap|不足|问题|挑战)", lowered))
        return found, "检测到问题描述关键词" if found else "未检测到明确的问题陈述"
    if check_id == "rc2":
        found = bool(re.search(r"(novel|propose|introduce|contribution|提出|贡献|创新|不同于|unlike)", lowered))
        return found, "检测到创新声明关键词" if found else "未检测到明确的创新声明"
    if check_id == "rc3":
        return None, "需要人工检查方法名在各章节中是否一致出现"
    if check_id == "rc4":
        return None, "需要人工评估贡献的具体性"
    if check_id == "rc5":
        overclaims = []
        for w in ["guarantees", "proves", "completely solves", "always", "never fails",
                   "彻底解决", "完美", "绝对", "毫无疑问", "全面超越"]:
            if w in lowered:
                overclaims.append(w)
        if overclaims:
            return False, f"检测到过度声明: {', '.join(overclaims)}"
        return True, "未检测到过度声明"
    if check_id == "rc6":
        return None, "需要对比 Abstract 和 Conclusion 的问题描述一致性"

    # ── Abstract ──
    if check_id == "ab1":
        checks = []
        target = abs_text or lowered[:3000]
        checks.append(("背景/问题", bool(re.search(r"(problem|challenge|背景|问题|task)", target))))
        checks.append(("方法", bool(re.search(r"(propose|method|approach|提出|方法|框架)", target))))
        checks.append(("实验/结果", bool(re.search(r"(result|performance|achieve|结果|达到|提升|experiment|dataset)", target))))
        missing = [c[0] for c in checks if not c[1]]
        if missing:
            return False, f"摘要缺少: {', '.join(missing)}"
        return True, "摘要要素完整"
    if check_id == "ab2":
        promo = []
        for p in ["revolutionary", "game-changing", "unprecedented", "颠覆", "革命性", "前所未有", "state-of-the-art"]:
            if p in (abs_text or lowered[:3000]):
                promo.append(p)
        if promo:
            return False, f"检测到宣传词: {', '.join(promo)}"
        return True, "未检测到宣传式措辞"
    if check_id == "ab3":
        return None, "需要人工检查摘要中的数据支撑"
    if check_id == "ab4":
        return None, "需要对比摘要和正文中的概念"
    if check_id == "ab5":
        return None, "需要人工判断摘要是否过度强调辅助实验"
    if check_id == "ab6":
        found_name = bool(re.search(r"(method|model|framework|system|approach|方法|模型|框架|系统)", abs_text or lowered[:3000]))
        found_data = bool(re.search(r"(dataset|benchmark|数据|corpus|collection)", abs_text or lowered[:3000]))
        score = sum([found_name, found_data])
        if score < 2:
            return False, f"方法名={'✓' if found_name else '✗'}, 数据集={'✓' if found_data else '✗'}"
        return True, "摘要包含方法和数据集信息"

    # ── Introduction ──
    if check_id == "in1":
        return None, "需要人工阅读第一段"
    if check_id == "in2":
        # Count unique technical terms in first ~500 words of intro
        intro_start = (intro_text or lowered)[:3000]
        tech_terms = len(re.findall(r'\\[a-zA-Z]+', intro_start))
        if tech_terms > 15:
            return False, f"引言开头检测到 {tech_terms} 个 LaTeX 命令（术语密度偏高）"
        return True, f"术语密度适中 ({tech_terms} LaTeX commands)"
    if check_id == "in3":
        return None, "需要人工对比痛点和方法设计"
    if check_id == "in4":
        found = bool(re.search(r"(contribution|novel|propose|introduce|贡献|创新|提出|本文|our)", intro_text or lowered[:5000]))
        return found, "检测到贡献/创新声明" if found else "Introduction 中未检测到明确的创新声明"
    if check_id == "in5":
        return None, "需要人工评估 Fig.1"
    if check_id == "in6":
        found = bool(re.search(r"(contribution|贡献).*?(\d\.|1\)|\(1\)|first|second|firstly)", intro_text or lowered[:5000]))
        return found, "检测到编号贡献列表" if found else "未检测到编号的贡献列表"

    # ── Related Work ──
    if check_id == "rw1":
        subsections = len(re.findall(r'\\subsection\*?\{', sections.get("related_work") or text))
        if subsections >= 2:
            return True, f"Related Work 包含 {subsections} 个小节"
        return False, f"Related Work 仅有 {subsections} 个小节（建议 ≥2）"
    if check_id == "rw2":
        return None, "需要人工检查引用的覆盖范围"
    if check_id == "rw3":
        found = bool(re.search(r"(however|but|although|limitation|gap|然而|但是|不足|局限|remains|unresolved)", sections.get("related_work") or lowered))
        return found, "检测到转折/不足表述" if found else "Related Work 中未检测到对已有工作的不足分析"
    if check_id == "rw4":
        # Count "Author X proposed" pattern
        proposed_patterns = len(re.findall(r'\\cite\s*\{[^}]*\}\s*(?:proposed|presented|introduced|提出|提出了)', lowered))
        if proposed_patterns > 5:
            return False, f"检测到 {proposed_patterns} 处 'Author proposed' 堆砌模式"
        return True, f"堆砌式写法较少 ({proposed_patterns} 处)"
    if check_id == "rw5":
        rw_section = sections.get("related_work") or ""
        claims = len(re.findall(r'(?<!\\)[A-Z][a-z]+.*?(?=\\cite)', rw_section))
        cites = len(re.findall(r'\\cite\s*\{', rw_section))
        if cites > 0:
            return True, f"检测到 {cites} 处引用"
        return False, "未检测到引用"
    if check_id == "rw6":
        return None, "需要人工判断引用相关性"

    # ── Method ──
    if check_id in ("me1", "me2", "me3", "me4", "me5", "me6", "me7", "me8"):
        return None, "需要人工审查 Method 部分"

    # ── Figures ──
    if check_id == "fi1":
        n_figs = len(re.findall(r'\\begin\{figure\}', text))
        if n_figs > 0:
            return True, f"检测到 {n_figs} 张图"
        return False, "未检测到图环境"
    if check_id == "fi2":
        return None, "需要人工对比图中术语和正文"
    if check_id == "fi3":
        return None, "需要人工检查缩小后的可读性"
    if check_id == "fi4":
        return None, "需要人工检查图注质量"
    if check_id == "fi5":
        # Check for PNG/JPG references
        png_jpg = re.findall(r'\\includegraphics.*?\{([^}]*\.(?:png|jpg|jpeg))\}', text, re.IGNORECASE)
        pdf_eps = re.findall(r'\\includegraphics.*?\{([^}]*\.(?:pdf|eps|svg))\}', text, re.IGNORECASE)
        if png_jpg:
            return False, f"检测到 {len(png_jpg)} 个光栅图: {', '.join(png_jpg[:3])}"
        if pdf_eps:
            return True, f"全部 {len(pdf_eps)} 张图为矢量格式"
        return None, "未检测到图文件引用"
    if check_id == "fi6":
        return None, "需要人工区分 Introduction 图和 Method 图的功能"
    if check_id == "fi7":
        draft_traces = []
        for t in ["draft", "v1", "v2", "old", "tmp", "temp", "草稿", "未完成"]:
            if t in lowered:
                draft_traces.append(t)
        if draft_traces:
            return False, f"检测到可能的草稿痕迹: {', '.join(draft_traces)}"
        return True, "未检测到草稿痕迹"
    if check_id == "fi8":
        return None, "需要人工评估图的复杂度"

    # ── Tables ──
    if check_id == "ta1":
        n_tables = len(re.findall(r'\\begin\{table\}', text))
        if n_tables > 0:
            return True, f"检测到 {n_tables} 个表格"
        return False, "未检测到表格环境"
    if check_id == "ta2":
        has_bold = bool(re.search(r'\\textbf\s*\{', text))
        return has_bold, "检测到加粗标记" if has_bold else "未检测到加粗（最优值应加粗）"
    if check_id == "ta3":
        return None, "需要人工确认表格方法名与正文一致"
    if check_id == "ta4":
        return None, "需要人工检查缺失值标注"
    if check_id == "ta5":
        return None, "需要人工检查表格引用"
    if check_id == "ta6":
        # Check for excessive decimal places
        excessive = re.findall(r'\d\.\d{5,}', text)
        if len(excessive) > 3:
            return False, f"检测到 {len(excessive)} 处超过 4 位小数的数值"
        return True, "小数位数合理"
    if check_id == "ta7":
        return None, "需要编译后检查表格宽度"
    if check_id == "ta8":
        return None, "需要人工对比正文和表格中的数值"
    if check_id == "ta9":
        has_std = bool(re.search(r'(std|stddev|standard deviation|±|\\pm|方差)', lowered))
        return has_std, "检测到标准差/显著性标记" if has_std else "未检测到标准差或显著性标记"

    # ── Experiments ──
    if check_id == "ex1":
        found_dataset = bool(re.search(r"(dataset|data|benchmark|corpus|数据集|数据)", lowered))
        found_split = bool(re.search(r"(split|train.*test|valid|fold|划分|训练.*测试|8.*1.*1|7.*2.*1)", lowered))
        score = sum([found_dataset, found_split])
        if score < 2:
            return False, f"数据集={'✓' if found_dataset else '✗'}, 划分={'✓' if found_split else '✗'}"
        return True, "数据集和划分信息明确"
    if check_id == "ex2":
        return None, "需要人工检查数据泄漏风险"
    if check_id == "ex3":
        found_baseline = bool(re.search(r"(baseline|compare|comparison|对比|比较|vs\.|versus)", lowered))
        found_version = bool(re.search(r"(model.*version|gpt.*\d|llama.*\d|参数|setting)", lowered))
        score = sum([found_baseline, found_version])
        if score >= 1:
            return True, "检测到 baseline 对比"
        return False, "未检测到明确的 baseline 对比"
    if check_id == "ex4":
        has_main = bool(re.search(r"(result|performance|结果|性能|accuracy|f1|bleu|rouge)", lowered))
        has_ablation = bool(re.search(r"(ablation|ablate|消融|component|removing|removing|without)", lowered))
        score = sum([has_main, has_ablation])
        if score >= 1:
            return True, f"主实验={'✓' if has_main else '✗'}, 消融={'✓' if has_ablation else '✗'}"
        return False, "未检测到消融实验"
    if check_id == "ex5":
        found = bool(re.search(r"(failure|error|limitation|失败|错误|局限|不足|case study|案例分析)", lowered))
        return found, "检测到失败案例分析" if found else "未检测到失败案例讨论"
    if check_id == "ex6":
        return None, "需要人工检查实验与研究问题的对应"
    if check_id == "ex7":
        return None, "需要人工检查是否选择性报告结果"
    if check_id == "ex8":
        return None, "需要人工检查实验矩阵完整性"

    # ── Statistics ──
    if check_id == "st1":
        found = bool(re.search(r"(mean|std|standard deviation|±|\\pm|平均|标准差|方差)", lowered))
        return found, "检测到 mean/std 标记" if found else "未检测到 mean ± std 报告"
    if check_id == "st2":
        found = bool(re.search(r"(fold|cross.validation|重复|折|多次|run|seed|trial)", lowered))
        return found, "检测到多折/重复实验" if found else "未检测到多折或重复实验描述"
    if check_id == "st3":
        found = bool(re.search(r"(p.value|significant|t.test|mcnemar|bootstrap|wilcoxon|显著性|p值|置信区间|confidence interval)", lowered))
        return found, "检测到显著性检验" if found else "未检测到显著性检验方法"
    if check_id == "st4":
        return None, "需要人工检查结论强度"
    if check_id == "st5":
        found = bool(re.search(r"(p\s*[<≤]\s*0\.\d|α\s*=\s*0\.\d|significance level|显著性水平)", lowered))
        return found, "检测到显著性水平说明" if found else "未检测到显著性水平（α）说明"
    if check_id == "st6":
        return None, "需要人工检查核心结果是否有统计说明"

    # ── Terminology ──
    if check_id in ("tm1", "tm2", "tm3", "tm4", "tm5", "tm6"):
        return None, "需要人工检查术语一致性"

    # ── Language ──
    if check_id == "la1":
        colloquial = []
        for w in ["a lot", "kind of", "sort of", "pretty", "really", "actually", "basically",
                   "big", "huge", "tiny", "stuff", "thing", "好多", "超", "贼"]:
            if w in lowered:
                colloquial.append(w)
        if colloquial:
            return False, f"检测到口语词: {', '.join(colloquial[:5])}"
        return True, "未检测到口语化表达"
    if check_id == "la2":
        absolutes = []
        for w in ["always", "never", "completely", "fully", "totally", "absolutely",
                   "总是", "永远", "完全", "绝对", "毫无疑问"]:
            if w in lowered:
                absolutes.append(w)
        if absolutes:
            return False, f"检测到绝对化词汇: {', '.join(absolutes[:5])}"
        return True, "未检测到绝对化词汇"
    if check_id == "la3":
        vague = []
        for w in ["novel", "powerful", "robust", "state-of-the-art", "efficient",
                   "effective", "promising", "innovative"]:
            count = len(re.findall(r'\b' + w + r'\b', lowered))
            if count >= 2:  # flag if used 2+ times without evidence
                vague.append(f"{w}({count}x)")
        if vague:
            return False, f"频繁使用空泛词汇（需实验证据）: {', '.join(vague[:5])}"
        return True, "空泛词汇使用适度"
    if check_id == "la4":
        bad_words = []
        for w in ["proves", "guarantees", "证明", "保证"]:
            if w in lowered:
                bad_words.append(w)
        if bad_words:
            return False, f"检测到绝对动词: {', '.join(bad_words)}（建议替换为 shows/suggests）"
        return True, "语言克制适度"
    if check_id == "la5":
        long_sents = _count_sentence_lengths(text)
        if len(long_sents) > 10:
            return False, f"检测到 {len(long_sents)} 个超过 40 词的长句"
        elif len(long_sents) > 3:
            return False, f"检测到 {len(long_sents)} 个长句（>40词）"
        return True, f"长句数量可接受 ({len(long_sents)} 个 >40 词)"
    if check_id == "la6":
        intensifiers = []
        for w in ["very", "extremely", "highly", "remarkably", "surprisingly",
                   "interestingly", "notably", "非常", "极其", "十分", "显著地"]:
            count = len(re.findall(r'\b' + w + r'\b', lowered))
            if count >= 2:
                intensifiers.append(f"{w}({count}x)")
        if intensifiers:
            return False, f"过多修饰词: {', '.join(intensifiers[:5])}"
        return True, "修饰词使用适度"

    # ── Citations ──
    if check_id == "ci1":
        cite_keys = _extract_citation_keys(text)
        bib_keys = set(_extract_bibitem_keys(text))
        if bib_keys:
            missing = [k for k in cite_keys if k not in bib_keys]
            if missing:
                return False, f"缺失的 citation key: {', '.join(missing[:5])}"
            return True, f"所有 {len(cite_keys)} 个 citation key 在 bib 中找到"
        return None, "未检测到 thebibliography（可能使用 .bib 文件）"
    if check_id == "ci2":
        has_undefined = bool(re.search(r"\?\s*citation|undefined|citation.*missing|Citation.*undefined", lowered))
        return not has_undefined, "检测到 undefined citation" if has_undefined else "未检测到 undefined citation 标记"
    if check_id == "ci3":
        return None, "需要人工检查引用时效性"
    if check_id == "ci4":
        return None, "需要人工检查关键背景引用完整性"
    if check_id == "ci5":
        cite_keys = _extract_citation_keys(text)
        bib_keys = _extract_bibitem_keys(text)
        if bib_keys:
            extra = [k for k in bib_keys if k not in cite_keys]
            if extra and len(extra) > 3:
                return False, f"{len(extra)} 个 bibitem key 未被正文引用: {', '.join(extra[:5])}"
            return True, "cite 和 bibitem 对应良好"
        return None, "未检测到 thebibliography"
    if check_id == "ci6":
        # Check for excessive citations at one point
        excessive = re.findall(r'\\cite\s*\{([^}]+)\}', text)
        max_cites = max((len(k.split(',')) for k in excessive), default=0)
        if max_cites > 4:
            return False, f"检测到一处引用 {max_cites} 篇文献（建议 ≤4）"
        return True, f"单处引用最多 {max_cites} 篇"
    if check_id == "ci7":
        arxiv_count = len(re.findall(r'arxiv', lowered))
        if arxiv_count > 0:
            return None, f"检测到 {arxiv_count} 处 arXiv 引用（请确认标注为 preprint）"
        return True, "未检测到 arXiv 引用"
    if check_id == "ci8":
        return None, "需要人工检查参考文献格式一致性"
    if check_id == "ci9":
        has_begin = bool(re.search(r'\\begin\{thebibliography\}', text))
        has_end = bool(re.search(r'\\end\{thebibliography\}', text))
        if has_begin and has_end:
            dupes = _find_duplicate_bibitem_keys(text)
            if dupes:
                return False, f"重复的 bibitem key: {', '.join(dupes[:5])}"
            return True, "thebibliography 环境完整，无重复 key"
        if has_begin or has_end:
            return False, "thebibliography 环境不完整（缺少 begin 或 end）"
        return None, "使用 .bib 文件（非 thebibliography）"

    # ── LaTeX ──
    if check_id in ("lx1", "lx2", "lx3", "lx4", "lx5", "lx6", "lx8", "lx9"):
        return None, "需要实际编译后检查 log"
    if check_id == "lx7":
        paths = _extract_figure_paths(text)
        if not paths:
            return None, "未检测到图文件引用"
        if paper_dir:
            missing = []
            for p in paths:
                full = os.path.join(paper_dir, p)
                if not os.path.exists(full) and not os.path.exists(full + ".pdf") and not os.path.exists(full + ".png"):
                    missing.append(p)
            if missing:
                return False, f"缺失图文件: {', '.join(missing[:5])}"
            return True, f"全部 {len(paths)} 个图文件存在"
        return None, f"检测到 {len(paths)} 个图引用（需要 paper_dir 验证）"
    if check_id == "lx10":
        paths = _extract_figure_paths(text)
        if paths:
            return None, f"检测到 {len(paths)} 个图文件引用"
        return True, "未检测到外部图文件引用"

    # ── Layout ──
    if check_id in ("lo1", "lo2", "lo3", "lo4", "lo5", "lo6", "lo7", "lo8", "lo9", "lo10"):
        return None, "需要编译后检查排版"

    # ── Blind Review ──
    if check_id == "br1":
        has_author = bool(re.search(r'\\author\s*\{', text))
        if has_author:
            author_content = re.search(r'\\author\s*\{([^}]+)\}', text)
            if author_content and re.search(r'anonymous|匿名', author_content.group(1), re.IGNORECASE):
                return True, "作者已匿名处理"
            return False, "检测到 \\author 命令但未匿名"
        return True, "未检测到 \\author 命令"
    if check_id == "br2":
        has_affil = bool(re.search(r'\\affiliation|\\institute|\\department', text))
        return not has_affil, "检测到机构信息" if has_affil else "未检测到机构信息"
    if check_id == "br3":
        # Check for common company/hospital names
        orgs = []
        for w in ["microsoft", "google", "facebook", "tencent", "alibaba", "baidu", "huawei",
                   "hospital", "医院", "公司", "inc", "ltd", "corp", "llc"]:
            if w in lowered:
                orgs.append(w)
        if orgs:
            return False, f"检测到可能的组织名: {', '.join(orgs[:5])}"
        return True, "未检测到明显的组织/公司名"
    if check_id == "br4":
        has_ack = bool(re.search(r'\\section\*?\{acknowledg|acknowledgment|致谢|感谢|acknowledgement', lowered))
        return not has_ack, "检测到致谢部分" if has_ack else "未检测到致谢部分"
    if check_id == "br5":
        return None, "需要人工检查数据来源是否暴露身份"
    if check_id == "br6":
        # Check for file paths in figures
        paths_in_text = re.findall(r'[A-Za-z]:\\[^\s,}]+', text)
        if paths_in_text:
            return False, f"检测到文件路径: {', '.join(paths_in_text[:3])}"
        return True, "未检测到文件路径"
    if check_id == "br7":
        return None, "需要 PDF 工具检查 metadata"
    if check_id == "br8":
        return None, "需要人工检查 supplementary material"
    if check_id == "br9":
        has_clinical = bool(re.search(r'(clinical|diagnosis|patient|病人|诊断|临床)', lowered))
        has_disclaimer = bool(re.search(r'(not for clinical|research only|only for research|not.*diagnos)', lowered))
        if has_clinical and not has_disclaimer:
            return False, "检测到临床相关内容但缺少免责声明"
        return True, "临床声明检查通过" if has_clinical else "非临床论文，无需检查"

    # ── Final submission ──
    if check_id == "fs1":
        has_title = bool(re.search(r'\\title\s*\{', text))
        if has_title:
            title_match = re.search(r'\\title\s*\{([^}]+)\}', text)
            title = title_match.group(1) if title_match else ""
            words = len(title.split())
            if words < 3:
                return False, f"标题过短（{words} 词）"
            return True, f"标题: {title[:80]}"
        return False, "未检测到 \\title"
    if check_id == "fs2":
        return None, "需要人工评估摘要独立性"
    if check_id == "fs3":
        return None, "需要人工评估 Fig.1"
    if check_id == "fs4":
        return None, "需要人工评估 Fig.2"
    if check_id == "fs5":
        return None, "需要人工对比实验表和贡献声明"
    if check_id == "fs6":
        overclaims = []
        for w in ["guarantees", "proves", "completely solves", "彻底解决", "完美", "毫无疑问"]:
            if w in (concl_text or lowered):
                overclaims.append(w)
        if overclaims:
            return False, f"结论中检测到过度声明: {', '.join(overclaims)}"
        return True, "结论措辞克制"
    if check_id == "fs7":
        draft_traces = []
        for t in ["TODO", "FIXME", "XXX", "??", "draft", "草稿", "待补充", "待完成", "TBD"]:
            # Use word-boundary search for English, direct for Chinese
            if re.search(r'\b' + re.escape(t) + r'\b', text):
                draft_traces.append(t)
        if draft_traces:
            return False, f"检测到草稿痕迹: {', '.join(draft_traces)}"
        return True, "未检测到草稿痕迹"
    if check_id == "fs8":
        return None, "需要实际编译后检查 log"
    if check_id == "fs9":
        return None, "需要 PDF 阅读器检查"
    if check_id == "fs10":
        return None, "需要打印后检查"

    return None, ""


# ═══════════════════════════════════════════════════════════════
# LaTeX compilation check
# ═══════════════════════════════════════════════════════════════

def _try_latex_compile(tex_path: str, paper_dir: str) -> dict:
    """Attempt pdflatex compilation and parse the log for errors/warnings."""
    result = {
        "compiled": False,
        "errors": [],
        "warnings": [],
        "missing_figures": [],
        "undefined_refs": [],
        "undefined_cites": [],
        "overfull": [],
        "underfull": [],
    }

    tex_dir = os.path.dirname(os.path.abspath(tex_path)) or paper_dir or "."
    tex_name = os.path.basename(tex_path)

    try:
        # Run pdflatex in the paper directory (non-stop mode, no interaction)
        proc = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_name],
            cwd=tex_dir, capture_output=True, text=True, timeout=60
        )
        log_output = proc.stdout + "\n" + proc.stderr
    except FileNotFoundError:
        result["errors"].append("pdflatex not found in PATH — install TeX Live or MiKTeX")
        return result
    except subprocess.TimeoutExpired:
        result["errors"].append("pdflatex timed out (>60s)")
        return result
    except Exception as e:
        result["errors"].append(f"pdflatex failed: {e}")
        return result

    result["compiled"] = proc.returncode == 0

    # Parse errors
    for line in log_output.split("\n"):
        if line.startswith("!"):
            result["errors"].append(line.strip())
        if "Error:" in line:
            result["errors"].append(line.strip())

    # Parse warnings
    for m in re.finditer(r"LaTeX Warning: (.+)", log_output):
        warning_text = m.group(1).strip()
        if "Citation" in warning_text and "undefined" in warning_text:
            result["undefined_cites"].append(warning_text)
        elif "Reference" in warning_text and "undefined" in warning_text:
            result["undefined_refs"].append(warning_text)
        elif "File" in warning_text and "not found" in warning_text:
            result["missing_figures"].append(warning_text)
        else:
            result["warnings"].append(warning_text)

    # Parse overfull/underfull
    result["overfull"] = re.findall(r"Overfull.*", log_output)
    result["underfull"] = re.findall(r"Underfull.*", log_output)

    return result


# ═══════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════

def main():
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    action = params.get("action", "check")
    draft = params.get("draft", "")
    output_path = params.get("output", "")
    dims = params.get("dimensions", [])
    lang = params.get("lang", "zh")
    strictness = params.get("strictness", "standard")
    venue_type = params.get("venue_type", "journal")
    compile_latex = params.get("compile", False)  # whether to run pdflatex

    if action != "check":
        fail(f"Unknown action: {action}")

    # Read draft
    paper_text = ""
    paper_dir = ""
    if draft:
        draft_path = Path(draft)
        if draft_path.exists() and draft_path.is_file():
            try:
                paper_text = draft_path.read_text(encoding="utf-8", errors="replace")
                paper_dir = str(draft_path.parent)
            except Exception:
                paper_text = draft
        else:
            paper_text = draft

    if not paper_text.strip():
        fail("No paper text provided. Set 'draft' to a file path or raw text.")

    # Parse LaTeX sections
    sections = _extract_latex_sections(paper_text)

    # Select dimensions
    selected_dims = dims if dims else list(DIMENSIONS.keys())
    selected_dims = [d for d in selected_dims if d in DIMENSIONS]

    # Run all checks
    results = []
    for dim_key in selected_dims:
        result = score_dimension(dim_key, paper_text, sections, strictness, paper_dir)
        results.append(result)

    # ── LaTeX compilation (if requested and applicable) ──
    compile_result = None
    if compile_latex and draft and draft.endswith(".tex") and os.path.exists(draft):
        compile_result = _try_latex_compile(draft, paper_dir)
        # Augment latex_compile dimension with real results
        for r in results:
            if r["dimension"] == "latex_compile":
                _augment_latex_dimension(r, compile_result)

    # Calculate overall score
    if results:
        overall = sum(r["score"] for r in results) / len(results) * 20
    else:
        overall = 0

    total_critical = sum(r["critical_count"] for r in results)
    total_warning = sum(r["warning_count"] for r in results)
    total_suggestion = sum(r["suggestion_count"] for r in results)

    # Build report
    report = {
        "status": "success",
        "overall_score": round(overall, 1),
        "max_score": 100,
        "readiness": (
            "ready" if overall >= 85 else
            "minor_fixes" if overall >= 70 else
            "needs_work" if overall >= 50 else
            "major_revision"
        ),
        "strictness": strictness,
        "venue_type": venue_type,
        "dimensions_checked": len(results),
        "total_issues": total_critical + total_warning + total_suggestion,
        "critical_issues": total_critical,
        "warning_issues": total_warning,
        "suggestion_issues": total_suggestion,
        "dimensions": results,
        "priority_fixes": [
            {
                "dimension": r["name_zh"] if lang == "zh" else r["name_en"],
                "check": i["zh"] if lang == "zh" else i["en"],
                "severity": i["severity"],
                "fix": i.get("fix_zh") if lang == "zh" else i.get("fix_en", ""),
                "evidence": i.get("evidence", ""),
            }
            for r in results
            for i in r["issues"]
            if i["severity"] == "critical"
        ],
        "compile_result": compile_result,
    }

    # Write output
    if output_path:
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix == ".json":
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            md = _generate_markdown_report(report, lang)
            out_path.write_text(md, encoding="utf-8")
        report["output_file"] = str(out_path)

    emit(report)


def _augment_latex_dimension(dim_result: dict, compile_result: dict):
    """Update latex_compile dimension with real compilation results."""
    if not compile_result:
        return

    for issue in dim_result["issues"]:
        cid = issue["check_id"]
        if cid == "lx1":
            if compile_result["errors"]:
                issue["severity"] = "critical"
                issue["finding"] = "not_found"
                issue["evidence"] = f"编译错误: {compile_result['errors'][0][:200]}"
                dim_result["critical_count"] += 1
                dim_result["score"] = max(1, dim_result["score"] - 1)
            else:
                issue["finding"] = "found"
                dim_result["checks_passed"] += 1
        elif cid == "lx2":
            if compile_result["missing_figures"]:
                issue["severity"] = "critical"
                issue["finding"] = "not_found"
                issue["evidence"] = str(compile_result["missing_figures"][:3])
                dim_result["critical_count"] += 1
                dim_result["score"] = max(1, dim_result["score"] - 1)
        elif cid == "lx3":
            total_undef = len(compile_result["undefined_refs"]) + len(compile_result["undefined_cites"])
            if total_undef > 0:
                issue["severity"] = "critical"
                issue["finding"] = "not_found"
                issue["evidence"] = f"{total_undef} undefined references/citations"
                dim_result["critical_count"] += 1
                dim_result["score"] = max(1, dim_result["score"] - 1)
        elif cid == "lx4":
            if compile_result["overfull"]:
                issue["finding"] = "needs_review"
                issue["evidence"] = f"{len(compile_result['overfull'])} overfull hbox(es)"
        elif cid == "lx6":
            if compile_result["underfull"]:
                issue["finding"] = "needs_review"
                issue["evidence"] = f"{len(compile_result['underfull'])} underfull hbox(es)"


def _generate_markdown_report(report: dict, lang: str) -> str:
    """Generate a human-readable Markdown audit report."""
    zh = lang == "zh"

    lines = []
    lines.append("# " + ("论文投稿前检查报告" if zh else "Pre-Submission Paper Audit Report"))
    lines.append("")

    score = report["overall_score"]
    readiness = report["readiness"]
    readiness_labels = {
        "ready": "✅ 可以投稿" if zh else "✅ Ready for submission",
        "minor_fixes": "🟡 小修后投稿" if zh else "🟡 Minor fixes needed",
        "needs_work": "🟠 需要较大修改" if zh else "🟠 Significant revision needed",
        "major_revision": "🔴 需要大幅修改" if zh else "🔴 Major revision required",
    }

    lines.append(f"## " + ("总体评分" if zh else "Overall Score"))
    lines.append(f"**{score}/100** — {readiness_labels.get(readiness, readiness)}")
    lines.append("")
    lines.append(f"- " + (f"检查维度: {report['dimensions_checked']}/16" if zh else f"Dimensions checked: {report['dimensions_checked']}/16"))
    lines.append(f"- " + (f"严重问题: {report['critical_issues']}" if zh else f"Critical issues: {report['critical_issues']}"))
    lines.append(f"- " + (f"警告: {report['warning_issues']}" if zh else f"Warnings: {report['warning_issues']}"))
    lines.append(f"- " + (f"建议: {report['suggestion_issues']}" if zh else f"Suggestions: {report['suggestion_issues']}"))
    lines.append("")

    # Priority fixes with evidence and suggestions
    if report["priority_fixes"]:
        lines.append("## 🔴 " + ("优先修复项" if zh else "Priority Fixes"))
        lines.append("")
        for i, fix in enumerate(report["priority_fixes"], 1):
            lines.append(f"**{i}. [{fix['dimension']}]** {fix['check']}")
            if fix.get("evidence"):
                lines.append(f"   > " + ("检测到" if zh else "Found") + f": {fix['evidence']}")
            if fix.get("fix"):
                lines.append(f"   > 💡 " + ("修复建议" if zh else "Fix") + f": {fix['fix']}")
            lines.append("")
        lines.append("")

    # Per-dimension summary table
    lines.append("## " + ("各维度详情" if zh else "Dimension Details"))
    lines.append("")
    lines.append("| # | " + ("维度" if zh else "Dimension") + " | " + ("评分" if zh else "Score") + " | 🔴 | 🟡 | 🔵 |")
    lines.append("|---|------|-------|---|---|---|")
    for r in report["dimensions"]:
        name = r["name_zh"] if zh else r["name_en"]
        lines.append(f"| {r['id']} | {name} | **{r['score']}/5** | {r['critical_count']} | {r['warning_count']} | {r['suggestion_count']} |")
    lines.append("")

    # Detailed issues with fix suggestions
    for r in report["dimensions"]:
        if not r["issues"]:
            continue
        name = r["name_zh"] if zh else r["name_en"]
        lines.append(f"### {r['id']}. {name} ({r['score']}/5)")
        lines.append("")
        for issue in r["issues"]:
            icon = {"critical": "🔴", "warning": "🟡", "suggestion": "🔵"}.get(issue["severity"], "⚪")
            desc = issue["zh"] if zh else issue["en"]
            lines.append(f"- {icon} {desc}")
            if issue.get("evidence"):
                lines.append(f"  > " + ("检测到" if zh else "Found") + f": {issue['evidence']}")
            if issue.get("fix_zh" if zh else "fix_en"):
                fix_text = issue.get("fix_zh") if zh else issue.get("fix_en", "")
                lines.append(f"  > 💡 " + ("修复建议" if zh else "Fix") + f": {fix_text}")
        lines.append("")

    # Compilation section
    if report.get("compile_result"):
        cr = report["compile_result"]
        lines.append("## 🔧 " + ("LaTeX 编译结果" if zh else "LaTeX Compilation Results"))
        lines.append("")
        if cr.get("compiled"):
            lines.append("- ✅ " + ("编译成功" if zh else "Compilation succeeded"))
        else:
            lines.append("- ❌ " + ("编译失败" if zh else "Compilation failed"))
        if cr.get("errors"):
            lines.append(f"- " + (f"Error: {len(cr['errors'])} 个" if zh else f"Errors: {len(cr['errors'])}"))
            for e in cr["errors"][:5]:
                lines.append(f"  - `{e[:150]}`")
        if cr.get("undefined_refs"):
            lines.append(f"- " + (f"Undefined references: {len(cr['undefined_refs'])} 个" if zh else f"Undefined references: {len(cr['undefined_refs'])}"))
        if cr.get("undefined_cites"):
            lines.append(f"- " + (f"Undefined citations: {len(cr['undefined_cites'])} 个" if zh else f"Undefined citations: {len(cr['undefined_cites'])}"))
        if cr.get("overfull"):
            lines.append(f"- " + (f"Overfull hbox: {len(cr['overfull'])} 个" if zh else f"Overfull hboxes: {len(cr['overfull'])}"))
        lines.append("")

    # Final principle
    lines.append("---")
    lines.append("")
    lines.append(
        "> " + (
            "论文不是把所有内容都塞进去，而是让审稿人用最少成本理解：问题重要、方法有新意、实验可信、结论克制、排版专业。"
            if zh else
            "A paper is not about cramming everything in — it's about letting reviewers understand with minimal effort: the problem matters, the method is novel, the experiments are credible, the conclusions are measured, and the presentation is professional."
        )
    )

    return "\n".join(lines)


if __name__ == "__main__":
    main()
