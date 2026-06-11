---
name: paper_reader
version: 1.1.0
description: Read papers from files/DOI/URL/PDF, produce structured notes, generate summaries, and extract methodologies with dataset/benchmark identification.
entry_function: main
parameters:
  type: object
  properties:
    action:
      type: string
      description: "read | read_many | summarize | extract_methodology | translate"
      enum: ["read", "read_many", "summarize", "extract_methodology", "translate"]
    paper:
      type: object
      description: Paper metadata object with title, abstract, doi, url, venue, year.
    papers:
      type: array
      description: List of paper metadata objects.
    path:
      type: string
      description: Local text, markdown, json, or PDF path.
    url:
      type: string
      description: Web page, DOI URL, or OpenAlex URL.
    doi:
      type: string
      description: DOI to resolve through OpenAlex.
    max_chars:
      type: integer
      description: Maximum text characters to keep. Default 12000.
    lang:
      type: string
      description: "Output language for summarize: en | zh | auto. Default auto."
      enum: ["en", "zh", "auto"]
      default: "auto"
  required:
    - action
keywords: [paper, read, pdf, full text, abstract, doi, research notes, summarize, methodology, dataset, benchmark]
---

# Paper Reader v1.1.0

## Actions

### `read` / `read_many`
Original behavior — produce structured research notes with method category, contribution type, limitations, gap hints, evidence strength.

### `summarize` ★ NEW
Generate a structured summary of the paper:
- **One-paragraph summary**: core problem, method, key findings, significance
- **Bullet-point contributions**: 3-5 key contributions
- **Limitations & gaps**: what the paper does NOT address
- **Takeaways for researchers**: actionable insights

### `extract_methodology` ★ NEW
Extract detailed methodology information:
- **Method type**: experimental, theoretical, survey, case study, simulation
- **Datasets used**: names, sizes, sources (e.g., "ImageNet", "CIFAR-10")
- **Benchmarks**: which benchmarks were used for evaluation
- **Metrics**: accuracy, F1, BLEU, etc.
- **Implementation**: framework (PyTorch, TensorFlow), code availability, GitHub links
- **Reproducibility signals**: is code available? Are hyperparameters specified? Is the dataset public?

### `translate` ★ NEW
Translate paper metadata between English and Chinese:
- Translates: title, abstract, key findings, limitations
- Direction: auto-detect (en→zh if source is English, zh→en if source is Chinese)
- Uses keyword-based translation for common academic terms when LLM is unavailable
- Output: original + translated fields side by side
