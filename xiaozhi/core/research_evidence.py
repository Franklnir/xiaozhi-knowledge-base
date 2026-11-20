"""Compare stored research excerpts without inventing conclusions."""

import re
from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlsplit


_WEAK_COVERAGE = {"Judul dan tautan", "Cuplikan hasil pencarian"}
_STOP_WORDS = set(
    "apa cara cari dari dalam dengan hasil jenis pada soal untuk yang dan ini itu the with about butuh tolong tambahkan jelaskan".split()
)
_MEASUREMENT = re.compile(
    r"(?<![\w.,])(-?\d+(?:[.,]\d{1,2})?)\s*"
    r"(%|kwh|wh|mah|ah|kw|watt|volt|ampere|kg|mg|cm|mm|km|menit|jam|hari|°c|w|v|a)(?!\w)",
    re.IGNORECASE,
)


def _words(text):
    return set(re.findall(r"[^\W\d_]{3,}", text.casefold())) - _STOP_WORDS


def _source_origin(source):
    if source.get("metadata", {}).get("doi"):
        return ("doi", source["metadata"]["doi"].casefold())
    if source.get("kind") == "youtube":
        return ("youtube", (source.get("publisher") or "").casefold().strip())
    host = urlsplit(source.get("url") or "").hostname or ""
    return ("web", host.removeprefix("www."))


def _comparison_refs(items):
    seen_numbers, citations = set(), []
    for item, _ in items:
        if item["source_number"] not in seen_numbers:
            seen_numbers.add(item["source_number"])
            citations.append(item)
    return citations


def analyze_research_evidence(sources, question):
    """Report exact textual agreement and candidate differences, with numbered excerpts."""
    exact, quantities = defaultdict(list), defaultdict(list)
    excerpts, metadata_only, usable = [], [], set()
    terms = _words(question)
    selected = sorted(
        sources,
        key=lambda source: (-len(terms & _words(source.get("finding") or source.get("title") or "")), source["number"]),
    )[:24]
    for source in selected:
        text = source.get("excerpt") or ""
        coverage = source.get("coverage") or "Judul dan tautan"
        if not text or coverage in _WEAK_COVERAGE:
            metadata_only.append(source["number"])
            continue
        text = re.sub(r"\[\d{2,}:\d{2}\]|(?:###\s*)?Halaman \d+\s*", "", text)
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if len(_words(part)) >= 3]
        sentences.sort(key=lambda part: -len(terms & _words(part)))
        if sentences:
            usable.add(source["number"])
        for sentence in sentences[:3]:
            if len(sentence) > 360:
                excerpt = sentence[:359].rsplit(" ", 1)[0] + "…"
            else:
                excerpt = sentence
            item = {"source_number": source["number"], "coverage": coverage, "text": excerpt}
            excerpts.append(item)
            if len(sentence) > 360:
                continue
            normalized = " ".join(re.findall(r"\w+|[%°]", sentence.casefold()))
            exact[normalized].append((item, source))
            measurements = list(_MEASUREMENT.finditer(sentence))
            if len(measurements) == 1:
                match = measurements[0]
                context = sentence[:match.start()] + match.group(2) + sentence[match.end():]
                key = " ".join(re.findall(r"\w+|[%°]", context.casefold()))
                quantities[key].append((item, source, Decimal(match.group(1).replace(",", ".")), context))

    agreement = [
        {"excerpt": items[0][0]["text"], "citations": _comparison_refs(items)}
        for items in exact.values()
        if len({_source_origin(source) for _, source in items}) >= 2
    ][:4]
    differences = []
    for items in quantities.values():
        distinct = {(item["source_number"], str(value)): (item, source) for item, source, value, _ in items}
        if (len({value for _, _, value, _ in items}) > 1
                and len({_source_origin(source) for _, source, _, _ in items}) >= 2
                and len(distinct) == len({item["source_number"] for item, _, _, _ in items})):
            differences.append({
                "citations": [item for item, _ in distinct.values()],
                "next_focus": items[0][3].strip(" .")[:160],
            })
    differences = differences[:4]
    open_questions = [
        {"question": "Apa kondisi atau metode yang menjelaskan perbedaan angka ini?",
         "focus": f"perbandingan metode dan kondisi {item['next_focus']}",
         "source_numbers": [citation["source_number"] for citation in item["citations"]]}
        for item in differences
    ]
    if len(usable) < 2:
        open_questions.append({
            "question": ("Adakah sumber tambahan yang menguatkan temuan ini?" if usable
                         else "Sumber mana yang menyediakan teks atau subtitle untuk memeriksa topik ini?"),
            "focus": "bukti dari sumber tambahan" if usable else "sumber dengan teks lengkap atau subtitle",
            "source_numbers": sorted(usable or set(metadata_only))[:8],
        })
    elif not agreement and not differences:
        lead = excerpts[0]
        open_questions.append({
            "question": "Adakah sumber pembanding yang menguji temuan yang sama?",
            "focus": f"sumber pembanding {lead['text'][:140]}",
            "source_numbers": [lead["source_number"]],
        })
    return {"agreement": agreement, "differences": differences, "excerpts": excerpts[:24],
            "metadata_only": metadata_only, "open_questions": open_questions}


def continuation_evidence(continuation, citations):
    """Check whether a continuation found excerpts that match its focus."""
    terms = _words(continuation["focus"])
    threshold = min(2, len(terms))
    return [
        source["number"] for source in citations
        if threshold and (source.get("new") or source.get("changed"))
        and source.get("coverage") not in _WEAK_COVERAGE
        and len(_words(source.get("excerpt") or "") & terms) >= threshold
    ]
