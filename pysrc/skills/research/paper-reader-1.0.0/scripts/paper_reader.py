from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research_common import compact_text, emit, extract_pdf_text, fail, normalize_paper, normalize_papers, openalex_fetch_work, openalex_work_to_paper, parse_params, reading_from_paper

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    import requests
except Exception:
    requests = None


def build_summary(reading: dict) -> dict:
    """Build a lightweight paper summary from a reading dict."""
    return {
        "title": reading.get("title", ""),
        "authors": reading.get("author") or reading.get("authors") or "",
        "year": reading.get("year", ""),
        "venue": reading.get("venue", ""),
        "doi": reading.get("doi", ""),
        "method_category": reading.get("method_category", ""),
        "contribution_type": reading.get("contribution_type", ""),
        "core_problem": reading.get("core_problem", ""),
        "key_findings": reading.get("key_findings", ""),
        "limitations": reading.get("limitations", ""),
        "evidence_strength": reading.get("evidence_strength", ""),
    }


def _maybe_summarize(data: dict, action: str) -> dict:
    """Convert reading/s to summary/ies when action is 'summarize'."""
    if action != "summarize":
        return data
    if "reading" in data:
        r = data.pop("reading")
        data["summary"] = build_summary(r) if isinstance(r, dict) else {}
    if "readings" in data:
        rs = data.pop("readings")
        data["summaries"] = [build_summary(r) for r in rs if isinstance(r, dict)]
        data["count"] = len(data["summaries"])
    return data


def read_file(path: Path, max_chars: int) -> dict:
    if not path.exists():
        fail(f"File not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".markdown", ".rst", ".bib", ".ris"):
        text = path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        papers = normalize_papers(value)
        if papers:
            return {"status": "success", "completed": True, "readings": [reading_from_paper(p) for p in papers]}
        text = json.dumps(value, ensure_ascii=False)
    elif suffix == ".pdf":
        text = extract_pdf_text(path, max_chars=max_chars)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    paper = normalize_paper({"title": path.stem, "text": compact_text(text, max_chars=max_chars)})
    paper["abstract"] = compact_text(text, max_chars=max_chars)
    return {"status": "success", "completed": True, "reading": reading_from_paper(paper)}


def read_url(url: str, max_chars: int) -> dict:
    if "doi.org/" in url or url.lower().startswith("10."):
        identifier = url if url.lower().startswith("http") else "https://doi.org/" + url
        work = openalex_fetch_work(identifier)
        if work:
            return {"status": "success", "completed": True, "reading": reading_from_paper(openalex_work_to_paper(work))}
    if requests is None:
        fail("requests package is required to read URLs.")
    response = requests.get(url, timeout=25, headers={"User-Agent": "MegatronResearchAssistant/1.0"})
    response.raise_for_status()
    html = response.text
    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        title = soup.title.get_text(" ", strip=True) if soup.title else url
    else:
        text = re.sub(r"<[^>]+>", " ", html)
        title = url
    paper = normalize_paper({"title": title, "url": url, "abstract": compact_text(text, max_chars=max_chars)})
    return {"status": "success", "completed": True, "reading": reading_from_paper(paper)}


# ── Methodology extraction helpers ────────────────────

DATASET_PATTERNS = [
    (re.compile(r'\b(ImageNet|CIFAR-10|CIFAR-100|MNIST|COCO|PASCAL\s*VOC|Cityscapes|ADE20K|Places\d*)\b', re.I), "vision"),
    (re.compile(r'\b(SQuAD|GLUE|SuperGLUE|XNLI|WMT\d*|IWSLT\d*|MuST-C|LibriSpeech|CommonVoice|SWAG|HellaSwag|MMLU|HumanEval|MBPP|BIG-Bench)\b', re.I), "nlp"),
    (re.compile(r'\b(PTCB|MIMIC-III|MIMIC-IV|CheXpert|UKBB|ABCD|ABIDE|ADNI|OASIS)\b', re.I), "medical"),
    (re.compile(r'\b(Waymo|nuScenes|KITTI|Argoverse|Lyft)\b', re.I), "autonomous"),
    (re.compile(r'\b(GitHub|Stack\s*Overflow|Wikipedia|Common\s*Crawl|The\s*Pile|RedPajama|C4|OpenWebText)\b', re.I), "web_text"),
    (re.compile(r'(dataset|benchmark|corpus)\s*(?:called|named|:)?\s*["\']?(\w+[\w\s-]*)', re.I), "mentioned"),
]

METRIC_PATTERNS = [
    r'\b(accuracy|acc\.?|precision|recall|F1|F1-score|BLEU|BLEU-\d|ROUGE|ROUGE-\d|METEOR|CIDEr|SPICE|'
    r'perplexity|ppl|WER|CER|MOS|PSNR|SSIM|LPIPS|FID|IS\b.*\d|'
    r'mAP|mAP@\d|IoU|Dice|AUC|AUC-ROC|MSE|MAE|RMSE|R²|R-squared|'
    r'ABX|MCD|log-F0|V/UV|MCC|Matthews)',
]

FRAMEWORK_PATTERNS = [
    (re.compile(r'\b(PyTorch|torch\.|torchvision|pytorch)\b', re.I), "PyTorch"),
    (re.compile(r'\b(TensorFlow|tensorflow|tf\.keras|Keras)\b', re.I), "TensorFlow"),
    (re.compile(r'\b(JAX|jax\.|flax|haiku)\b', re.I), "JAX"),
    (re.compile(r'\b(Hugging\s*Face|transformers|datasets|tokenizers|diffusers)\b', re.I), "HuggingFace"),
    (re.compile(r'\b(scikit-learn|sklearn)\b', re.I), "scikit-learn"),
    (re.compile(r'\b(OpenAI|GPT|ChatGPT|openai\.)\b', re.I), "OpenAI API"),
    (re.compile(r'\b(LangChain|LlamaIndex)\b', re.I), "LangChain"),
]

GITHUB_PATTERN = re.compile(r'(?:github\.com/[\w.-]+/[\w.-]+|gitlab\.com/[\w.-]+/[\w.-]+|bitbucket\.org/[\w.-]+/[\w.-]+|huggingface\.co/[\w.-]+/[\w.-]+)', re.I)
HYPERPARAM_PATTERN = re.compile(r'\b(learning\s*rate|lr\s*=|batch\s*size|epochs?\s*=|dropout|weight\s*decay|optimizer|Adam|SGD|RMSprop)\b', re.I)


def _extract_datasets(abstract: str, paper: dict) -> list[dict]:
    text = abstract + " " + str(paper.get("title", ""))
    found = []
    seen = set()
    for pat, domain in DATASET_PATTERNS:
        for m in pat.finditer(text):
            name = m.group(1) if pat is DATASET_PATTERNS[0][0] else (m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(0))
            name = name.strip()[:80]
            if name.lower() not in seen:
                seen.add(name.lower())
                found.append({"name": name, "domain": domain, "public": True})
    return found[:10]


def _extract_benchmarks(abstract: str, paper: dict) -> list[str]:
    text = abstract + " " + str(paper.get("title", ""))
    bench_pattern = re.compile(r'(?:on|evaluated?\s*(?:on|using)|benchmark(?:ed)?\s*(?:on|using)?)\s+["\']?(\w+[\w\s-]*)', re.I)
    return list(set(m.group(1).strip()[:60] for m in bench_pattern.finditer(text)))[:8]


def _extract_metrics(abstract: str, paper: dict) -> list[str]:
    text = abstract + " " + str(paper.get("title", ""))
    return list(set(re.findall(METRIC_PATTERNS[0], text, re.I)))[:10]


def _extract_framework(abstract: str, paper: dict) -> str:
    text = abstract + " " + str(paper.get("title", ""))
    for pat, fw in FRAMEWORK_PATTERNS:
        if pat.search(text):
            return fw
    return "unknown"


def _extract_code_info(paper: dict) -> dict:
    text = str(paper.get("abstract", "")) + " " + str(paper.get("notes", "")) + " " + str(paper.get("url", ""))
    links = list(set(GITHUB_PATTERN.findall(text)))
    return {"available": len(links) > 0, "links": links[:5]}


def _has_hyperparams(abstract: str) -> bool:
    return bool(HYPERPARAM_PATTERN.search(abstract))


# ── Translation ────────────────────────────────────────

ACADEMIC_TERMS_EN_ZH = {
    "abstract": "摘要", "introduction": "引言", "related work": "相关工作",
    "methodology": "方法", "method": "方法", "experiment": "实验", "experiments": "实验",
    "results": "结果", "discussion": "讨论", "conclusion": "结论", "conclusions": "结论",
    "future work": "未来工作", "limitation": "局限性", "limitations": "局限性",
    "contribution": "贡献", "contributions": "贡献", "evaluation": "评估",
    "dataset": "数据集", "benchmark": "基准", "baseline": "基线", "baselines": "基线",
    "accuracy": "准确率", "precision": "精确率", "recall": "召回率", "F1 score": "F1 分数",
    "training": "训练", "inference": "推理", "fine-tuning": "微调",
    "transformer": "Transformer", "attention": "注意力机制",
    "neural network": "神经网络", "deep learning": "深度学习",
    "machine learning": "机器学习", "natural language processing": "自然语言处理",
    "computer vision": "计算机视觉", "reinforcement learning": "强化学习",
    "large language model": "大语言模型", "LLM": "大语言模型",
    "human-computer interaction": "人机交互", "HCI": "人机交互",
    "information systems": "信息系统", "information management": "信息管理",
    "literature review": "文献综述", "systematic review": "系统综述",
    "meta-analysis": "元分析", "case study": "案例研究", "survey": "综述",
    "novel": "新颖的", "state-of-the-art": "最先进的", "SOTA": "最先进的",
    "outperform": "优于", "comparable": "相当的", "significant": "显著的",
    "robust": "鲁棒的", "scalable": "可扩展的", "efficient": "高效的",
    "我们": "we", "提出": "propose", "方法": "method", "模型": "model",
    "系统": "system", "框架": "framework", "架构": "architecture",
    "实验结果表明": "experimental results show", "优于现有": "outperforms existing",
}

ACADEMIC_TERMS_ZH_EN = {v: k for k, v in ACADEMIC_TERMS_EN_ZH.items()}


def _detect_lang(text: str) -> str:
    cjk = sum(1 for c in text if '一' <= c <= '鿿')
    return "zh" if cjk > len(text) * 0.15 else "en"


def _translate_text(text: str, direction: str) -> str:
    """Simple keyword-based translation of academic terms."""
    if not text:
        return ""
    if direction == "en2zh":
        result = text
        for en, zh in sorted(ACADEMIC_TERMS_EN_ZH.items(), key=lambda x: -len(x[0])):
            result = re.sub(r'\b' + re.escape(en) + r'\b', zh, result, flags=re.I)
        return result
    else:
        result = text
        for zh, en in sorted(ACADEMIC_TERMS_ZH_EN.items(), key=lambda x: -len(x[0])):
            result = result.replace(zh, en)
        return result


def _translate_paper(paper: dict) -> dict:
    """Translate a paper's key fields."""
    title = str(paper.get("title", ""))
    abstract = str(paper.get("abstract", ""))
    findings = str(paper.get("key_findings", paper.get("findings", "")))

    src_lang = _detect_lang(title + " " + abstract[:200])
    direction = "en2zh" if src_lang == "en" else "zh2en"

    return {
        "title_original": title,
        "title_translated": _translate_text(title, direction),
        "abstract_original": abstract[:500],
        "abstract_translated": _translate_text(abstract[:500], direction),
        "findings_translated": _translate_text(findings[:300], direction) if findings else "",
        "source_language": src_lang,
        "direction": direction,
    }


def main() -> int:
    params = parse_params(sys.argv[1] if len(sys.argv) > 1 else "{}")
    action = str(params.get("action", "read")).lower()
    max_chars = int(params.get("max_chars") or 12000)

    if action == "extract_methodology":
        papers = normalize_papers(params.get("papers") or [params.get("paper")] or [])
        if not papers and params.get("path"):
            papers = normalize_papers(json.loads(Path(str(params["path"])).read_text(encoding="utf-8", errors="replace")))
        if not papers:
            fail("Provide papers or path for methodology extraction.")
        results = []
        for p in papers:
            reading = reading_from_paper(p)
            abstract = str(p.get("abstract") or reading.get("core_problem") or "")
            # Heuristic extraction of datasets, metrics, frameworks, code links
            datasets = _extract_datasets(abstract, p)
            benchmarks = _extract_benchmarks(abstract, p)
            metrics = _extract_metrics(abstract, p)
            framework = _extract_framework(abstract, p)
            code_info = _extract_code_info(p)
            results.append({
                "title": p.get("title", ""),
                "method_category": reading.get("method_category", ""),
                "datasets": datasets,
                "benchmarks": benchmarks,
                "metrics": metrics,
                "framework": framework,
                "code_available": code_info["available"],
                "code_links": code_info["links"],
                "reproducibility_signals": {
                    "code_public": code_info["available"],
                    "hyperparams_specified": _has_hyperparams(abstract),
                    "dataset_public": all(d.get("public", False) for d in datasets),
                },
            })
        emit({"status": "success", "completed": True, "count": len(results), "methodologies": results})
        return 0
    if action == "translate":
        papers = normalize_papers(params.get("papers") or [params.get("paper")] or [])
        if not papers and params.get("path"):
            papers = normalize_papers(json.loads(Path(str(params["path"])).read_text(encoding="utf-8", errors="replace")))
        if not papers:
            fail("Provide papers or path for translation.")
        translated = [_translate_paper(p) for p in papers if p]
        emit({"status": "success", "completed": True, "count": len(translated), "translations": translated,
              "note": "Keyword-based academic term translation."})
        return 0
    if action == "read_many":
        emit(_maybe_summarize({"status": "success", "completed": True,
                               "reading": reading_from_paper(normalize_paper(params["paper"]))}, action))
        return 0
    if params.get("papers"):
        papers = normalize_papers(params["papers"])
        emit(_maybe_summarize({"status": "success", "completed": True, "count": len(papers),
                               "readings": [reading_from_paper(p) for p in papers]}, action))
        return 0
    if params.get("path"):
        emit(_maybe_summarize(read_file(Path(str(params["path"])).expanduser(), max_chars), action))
        return 0
    if params.get("doi"):
        emit(_maybe_summarize(read_url(str(params["doi"]), max_chars), action))
        return 0
    if params.get("url"):
        emit(_maybe_summarize(read_url(str(params["url"]), max_chars), action))
        return 0
    fail("Provide paper, papers, path, doi, or url.")


if __name__ == "__main__":
    raise SystemExit(main())
