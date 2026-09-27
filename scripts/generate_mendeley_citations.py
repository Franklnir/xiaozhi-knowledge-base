"""
Script to generate standard RIS and BibTeX reference files for Mendeley / Zotero.
Contains all 25 core journals for Xiaozhi Voice AI Assistant project.
"""
import os

PAPERS = [
    {
        "id": "wu2026retrieval",
        "authors": ["Wu, S.", "Xiong, Y.", "Cui, Y.", "Wu, H.", "Chen, C.", "Yuan, Y.", "Huang, L.", "Liu, X.", "Kuo, T.-W.", "Guan, N.", "Xue, C. J."],
        "title": "Retrieval-augmented generation for natural language processing: A survey",
        "journal": "Artificial Intelligence Review",
        "volume": "59",
        "issue": "",
        "pages": "192",
        "year": "2026",
        "doi": "10.1007/s10462-026-11605-7",
        "url": "https://doi.org/10.1007/s10462-026-11605-7",
        "note": "Fokus: dasar RAG, retriever, retrieval fusion, evaluasi, deployment. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "zhao2026retrieval",
        "authors": ["Zhao, P.", "Zhang, H.", "Yu, Q.", "Wang, Z.", "Geng, Y.", "Fu, F.", "Yang, L.", "Zhang, W.", "Jiang, J.", "Cui, B."],
        "title": "Retrieval-Augmented Generation for AI-Generated Content: A Survey",
        "journal": "Data Science and Engineering",
        "volume": "11",
        "issue": "",
        "pages": "1-29",
        "year": "2026",
        "doi": "10.1007/s41019-025-00335-5",
        "url": "https://doi.org/10.1007/s41019-025-00335-5",
        "note": "Fokus: arsitektur RAG, retrieval, knowledge update, robustness. (Prioritas: Pendukung)"
    },
    {
        "id": "li2025retrieval",
        "authors": ["Li, Z.", "Wang, Z.", "Wang, W.", "Hung, K.", "Xie, H.", "Wang, F. L."],
        "title": "Retrieval-augmented generation for educational application: A systematic survey",
        "journal": "Computers and Education: Artificial Intelligence",
        "volume": "8",
        "issue": "",
        "pages": "100417",
        "year": "2025",
        "doi": "10.1016/j.caeai.2025.100417",
        "url": "https://doi.org/10.1016/j.caeai.2025.100417",
        "note": "Fokus: RAG pada sistem pendidikan/knowledge assistant; hallucination, freshness, computational cost. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "brown2025systematic",
        "authors": ["Brown, A.", "Roman, M.", "Devereux, B."],
        "title": "A Systematic Literature Review of Retrieval-Augmented Generation: Techniques, Metrics, and Challenges",
        "journal": "Big Data and Cognitive Computing",
        "volume": "9",
        "issue": "12",
        "pages": "320",
        "year": "2025",
        "doi": "10.3390/bdcc9120320",
        "url": "https://doi.org/10.3390/bdcc9120320",
        "note": "Fokus: SLR RAG, retrieval metrics, answer metrics, grounding, latency/cost, security. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "huang2024layered",
        "authors": ["Huang, J.", "Wang, M.", "Cui, Y.", "Liu, J.", "Chen, L.", "Wang, T.", "Li, H.", "Wu, J."],
        "title": "Layered Query Retrieval: An Adaptive Framework for Retrieval-Augmented Generation in Complex Question Answering for Large Language Models",
        "journal": "Applied Sciences",
        "volume": "14",
        "issue": "23",
        "pages": "11014",
        "year": "2024",
        "doi": "10.3390/app142311014",
        "url": "https://doi.org/10.3390/app142311014",
        "note": "Fokus: adaptive retrieval, query complexity, retrieval accuracy/efficiency. (Prioritas: Pendukung)"
    },
    {
        "id": "wan2025empowering",
        "authors": ["Wan, Y.", "Chen, Z.", "Liu, Y.", "Chen, C.", "Packianather, M."],
        "title": "Empowering LLMs by hybrid retrieval-augmented generation for domain-centric Q&A in smart manufacturing",
        "journal": "Advanced Engineering Informatics",
        "volume": "65",
        "issue": "B",
        "pages": "103212",
        "year": "2025",
        "doi": "10.1016/j.aei.2025.103212",
        "url": "https://doi.org/10.1016/j.aei.2025.103212",
        "note": "Fokus: hybrid RAG, vector retrieval + structured knowledge, domain-centric QA. (Prioritas: Pendukung)"
    },
    {
        "id": "fukui2025evaluation",
        "authors": ["Fukui, Y.", "Kawata, Y.", "Kobashi, K.", "Nagatani, Y.", "Iguchi, H."],
        "title": "Evaluation of a retrieval-augmented generation system using a Japanese Institutional Nuclear Medicine Manual and large language model-automated scoring",
        "journal": "Radiological Physics and Technology",
        "volume": "18",
        "issue": "",
        "pages": "861-876",
        "year": "2025",
        "doi": "10.1007/s12194-025-00941-y",
        "url": "https://doi.org/10.1007/s12194-025-00941-y",
        "note": "Fokus: hybrid retrieval (dense + BM25), RAGAS, expert evaluation. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "knollmeyer2026evaluating",
        "authors": ["Knollmeyer, S.", "Caymazer, O.", "Koval, L.", "Akmal, M. U.", "Asif, S.", "Mathias, S. G.", "Großmann, D."],
        "title": "Evaluating Retrieval Augmented Generation: A Comprehensive Review of Evaluation Dimensions, Question Types, and Application",
        "journal": "SN Computer Science",
        "volume": "7",
        "issue": "",
        "pages": "576",
        "year": "2026",
        "doi": "10.1007/s42979-026-05134-x",
        "url": "https://doi.org/10.1007/s42979-026-05134-x",
        "note": "Fokus: evaluasi RAG, retrieval quality, answer quality, evaluation dimensions. (Prioritas: Pendukung)"
    },
    {
        "id": "asai2026synthesizing",
        "authors": ["Asai, A.", "He, J.", "Shao, R.", "Shi, W.", "Singh, A.", "Chang, J. C.", "Lo, K.", "Soldaini, L.", "Feldman, S.", "D’Arcy, M.", "Wadden, D.", "Latzke, M."],
        "title": "Synthesizing scientific literature with retrieval-augmented language models",
        "journal": "Nature",
        "volume": "650",
        "issue": "",
        "pages": "857-863",
        "year": "2026",
        "doi": "10.1038/s41586-025-10072-4",
        "url": "https://doi.org/10.1038/s41586-025-10072-4",
        "note": "Fokus: citation-backed generation, retriever, data store, self-feedback loop, grounded synthesis. (Prioritas: Pendukung)"
    },
    {
        "id": "zhang2025survey",
        "authors": ["Zhang, Z.", "Dai, Q.", "Bo, X.", "Ma, C."],
        "title": "A Survey on the Memory Mechanism of Large Language Model-based Agents",
        "journal": "ACM Transactions on Information Systems",
        "volume": "43",
        "issue": "6",
        "pages": "155",
        "year": "2025",
        "doi": "10.1145/3748302",
        "url": "https://doi.org/10.1145/3748302",
        "note": "Fokus: short-term/long-term memory, memory retrieval/update, memory in agents. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "tiwari2026memory",
        "authors": ["Tiwari, A.", "Gupta, V."],
        "title": "A memory fabric for conversational AI agents enabling shared and persistent multiuser memory",
        "journal": "Discover Artificial Intelligence",
        "volume": "6",
        "issue": "",
        "pages": "239",
        "year": "2026",
        "doi": "10.1007/s44163-026-00992-z",
        "url": "https://doi.org/10.1007/s44163-026-00992-z",
        "note": "Fokus: persistent memory, short-term + long-term context, personalization, provenance. (Prioritas: Pendukung)"
    },
    {
        "id": "xu2025llm",
        "authors": ["Xu, W.", "Huang, C.", "Gao, S."],
        "title": "LLM-Based Agents for Tool Learning: A Survey",
        "journal": "Data Science and Engineering",
        "volume": "10",
        "issue": "",
        "pages": "533-563",
        "year": "2025",
        "doi": "10.1007/s41019-025-00296-9",
        "url": "https://doi.org/10.1007/s41019-025-00296-9",
        "note": "Fokus: tool retrieval, tool understanding, tool planning, execution, safety, benchmarks. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "wang2024survey",
        "authors": ["Wang, L.", "Ma, C.", "Feng, X.", "Zhang, Z.", "Yang, H.", "Zhang, J.", "Chen, Z.", "Tang, J.", "Chen, X.", "Lin, Y.", "Zhao, W. X.", "Wei, Z.", "Wen, J."],
        "title": "A survey on large language model based autonomous agents",
        "journal": "Frontiers of Computer Science",
        "volume": "18",
        "issue": "",
        "pages": "186345",
        "year": "2024",
        "doi": "10.1007/s11704-024-40231-1",
        "url": "https://doi.org/10.1007/s11704-024-40231-1",
        "note": "Fokus: agent architecture, reasoning, planning, memory, action. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "chowa2026language",
        "authors": ["Chowa, S. S.", "Alvi, R.", "Rahman, S. S.", "Rahman, M. A.", "Raiaan, M. A. K.", "Islam, M. R.", "Hussain, M."],
        "title": "From language to action: a review of large language models as autonomous agents and tool users",
        "journal": "Artificial Intelligence Review",
        "volume": "59",
        "issue": "",
        "pages": "71",
        "year": "2026",
        "doi": "10.1007/s10462-025-11471-9",
        "url": "https://doi.org/10.1007/s10462-025-11471-9",
        "note": "Fokus: reasoning, planning, memory, tool use, autonomous agents. (Prioritas: Pendukung)"
    },
    {
        "id": "gallo2024conversational",
        "authors": ["Gallo, S.", "Paternò, F.", "Malizia, A."],
        "title": "A conversational agent for creating automations exploiting large language models",
        "journal": "Personal and Ubiquitous Computing",
        "volume": "28",
        "issue": "",
        "pages": "931-946",
        "year": "2024",
        "doi": "10.1007/s00779-024-01825-5",
        "url": "https://doi.org/10.1007/s00779-024-01825-5",
        "note": "Fokus: conversational agent, IoT automation, verification before action, confirmation workflow. (Prioritas: Pendukung)"
    },
    {
        "id": "zhang2026model",
        "authors": ["Zhang, N.", "Mao, H.", "Zhang, S.", "Liu, X.", "Jiang, H."],
        "title": "A Model Context Protocol-Based Retrieval-Augmented Generation Framework for Resource-Constrained Environments",
        "journal": "Neural Processing Letters",
        "volume": "58",
        "issue": "",
        "pages": "51",
        "year": "2026",
        "doi": "10.1007/s11063-026-11857-y",
        "url": "https://doi.org/10.1007/s11063-026-11857-y",
        "note": "Fokus: MCP + RAG + resource-constrained/edge, retrieval fusion, low latency. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "athanasopoulou2026interacting",
        "authors": ["Athanasopoulou, A.", "Fotiou, N.", "Chatzopoulos, A."],
        "title": "Interacting with IoT Data Spaces Using LLMs and the Model Context Protocol",
        "journal": "Sensors",
        "volume": "26",
        "issue": "4",
        "pages": "1193",
        "year": "2026",
        "doi": "10.3390/s26041193",
        "url": "https://doi.org/10.3390/s26041193",
        "note": "Fokus: LLM + MCP + IoT + natural-language interaction. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "hong2026quantitative",
        "authors": ["Hong, S.-H.", "Lee, H.-W."],
        "title": "Quantitative Comparison of Tool-Integration Methods in Large Language Models: Tool-Calling, MCP, and RAG-MCP",
        "journal": "The Journal of KINGComputing",
        "volume": "22",
        "issue": "1",
        "pages": "97-103",
        "year": "2026",
        "doi": "10.23019/kingpc.22.1.202602.008",
        "url": "https://doi.org/10.23019/kingpc.22.1.202602.008",
        "note": "Fokus: perbandingan Tool-Calling vs MCP vs RAG-MCP; accuracy, prompt bloat, tool selection. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "li2025energyplus",
        "authors": ["Li, H.", "Xu, Y.", "Hong, T."],
        "title": "EnergyPlus-MCP: A model-context-protocol server for AI-driven building energy modeling",
        "journal": "SoftwareX",
        "volume": "32",
        "issue": "",
        "pages": "102367",
        "year": "2025",
        "doi": "10.1016/j.softx.2025.102367",
        "url": "https://doi.org/10.1016/j.softx.2025.102367",
        "note": "Fokus: layered MCP server, multiple specialized tools, LLM conversational control, validation. (Prioritas: Pendukung)"
    },
    {
        "id": "zong2025integrating",
        "authors": ["Zong, M.", "Hekmati, A.", "Guastalla, M.", "Li, Y.", "Krishnamachari, B."],
        "title": "Integrating large language models with internet of things: applications",
        "journal": "Discover Internet of Things",
        "volume": "5",
        "issue": "",
        "pages": "2",
        "year": "2025",
        "doi": "10.1007/s43926-024-00083-4",
        "url": "https://doi.org/10.1007/s43926-024-00083-4",
        "note": "Fokus: LLM + IoT, sensor data, IoT programming, natural-language interface. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "devito2025role",
        "authors": ["De Vito, G.", "Palomba, F.", "Ferrucci, F."],
        "title": "The role of Large Language Models in addressing IoT challenges: A systematic literature review",
        "journal": "Future Generation Computer Systems",
        "volume": "171",
        "issue": "",
        "pages": "107829",
        "year": "2025",
        "doi": "10.1016/j.future.2025.107829",
        "url": "https://doi.org/10.1016/j.future.2025.107829",
        "note": "Fokus: SLR LLM + IoT, hardware/software, security, interoperability, resource constraints. (Prioritas: Pendukung)"
    },
    {
        "id": "an2026iotllm",
        "authors": ["An, T.", "Zhou, Y.", "Zou, H.", "Yang, J."],
        "title": "IoT-LLM: A framework for enhancing large language model reasoning from real-world sensor data",
        "journal": "Patterns",
        "volume": "7",
        "issue": "1",
        "pages": "101429",
        "year": "2026",
        "doi": "10.1016/j.patter.2025.101429",
        "url": "https://doi.org/10.1016/j.patter.2025.101429",
        "note": "Fokus: heterogeneous sensor data, expert knowledge retrieval, context-aware reasoning, IoT. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "jeong2025ondevice",
        "authors": ["Jeong, D.", "Woo, H."],
        "title": "On-Device Intent Reasoning for Smart Home Agents Via Ontology-Augmented sLLMs",
        "journal": "IEEE Access",
        "volume": "13",
        "issue": "",
        "pages": "197645-197662",
        "year": "2025",
        "doi": "10.1109/ACCESS.2025.3634621",
        "url": "https://doi.org/10.1109/ACCESS.2025.3634621",
        "note": "Fokus: intent reasoning, user/device context, ambiguous commands, retrieval-augmented on-device agents. (Prioritas: Pendukung)"
    },
    {
        "id": "lazzaroni2024embedded",
        "authors": ["Lazzaroni, L.", "Bellotti, F.", "Berta, R."],
        "title": "An embedded end-to-end voice assistant",
        "journal": "Engineering Applications of Artificial Intelligence",
        "volume": "136",
        "issue": "",
        "pages": "108998",
        "year": "2024",
        "doi": "10.1016/j.engappai.2024.108998",
        "url": "https://doi.org/10.1016/j.engappai.2024.108998",
        "note": "Fokus: embedded voice assistant, ASR, edge processing, latency, offline voice pipeline. (Prioritas: Sangat kuat / inti)"
    },
    {
        "id": "yauri2024generative",
        "authors": ["Yauri, R.", "Espino, R."],
        "title": "Generative Language Model Technology Integrated into an IoT Device for the Development of a Voice Assistant",
        "journal": "WSEAS Transactions on Systems",
        "volume": "23",
        "issue": "",
        "pages": "521-530",
        "year": "2024",
        "doi": "10.37394/23202.2024.23.54",
        "url": "https://doi.org/10.37394/23202.2024.23.54",
        "note": "Fokus: ESP32, INMP441, MAX98357, LLM, speech-to-text, text-to-speech, voice assistant. (Prioritas: Sangat kuat / inti)"
    }
]

def generate_ris(papers, output_path):
    lines = []
    for p in papers:
        lines.append("TY  - JOUR")
        for au in p["authors"]:
            lines.append(f"AU  - {au}")
        lines.append(f"TI  - {p['title']}")
        lines.append(f"JO  - {p['journal']}")
        lines.append(f"PY  - {p['year']}")
        if p["volume"]:
            lines.append(f"VL  - {p['volume']}")
        if p["issue"]:
            lines.append(f"IS  - {p['issue']}")
        if p["pages"]:
            if "-" in p["pages"]:
                sp, ep = p["pages"].split("-", 1)
                lines.append(f"SP  - {sp}")
                lines.append(f"EP  - {ep}")
            else:
                lines.append(f"SP  - {p['pages']}")
        lines.append(f"DO  - {p['doi']}")
        lines.append(f"UR  - {p['url']}")
        lines.append(f"N2  - {p['note']}")
        lines.append(f"KW  - Voice AI")
        lines.append(f"KW  - IoT")
        lines.append(f"KW  - Xiaozhi")
        lines.append(f"KW  - RAG")
        lines.append(f"KW  - Tool Calling")
        lines.append(f"KW  - Contextual Memory")
        lines.append("ER  - \n")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

def generate_bib(papers, output_path):
    lines = []
    for p in papers:
        authors_bib = " and ".join(p["authors"])
        lines.append(f"@{ 'article' }{{{p['id']},")
        lines.append(f"  author    = {{{authors_bib}}},")
        lines.append(f"  title     = {{{p['title']}}},")
        lines.append(f"  journal   = {{{p['journal']}}},")
        lines.append(f"  year      = {{{p['year']}}},")
        if p["volume"]:
            lines.append(f"  volume    = {{{p['volume']}}},")
        if p["issue"]:
            lines.append(f"  number    = {{{p['issue']}}},")
        if p["pages"]:
            lines.append(f"  pages     = {{{p['pages']}}},")
        lines.append(f"  doi       = {{{p['doi']}}},")
        lines.append(f"  url       = {{{p['url']}}},")
        lines.append(f"  note      = {{{p['note']}}}")
        lines.append("}\n")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ris_file = os.path.join(base_dir, "xiaozhi_25_jurnal_mendeley.ris")
    bib_file = os.path.join(base_dir, "xiaozhi_25_jurnal_mendeley.bib")
    generate_ris(PAPERS, ris_file)
    generate_bib(PAPERS, bib_file)
    print(f"Generated RIS: {ris_file}")
    print(f"Generated BibTeX: {bib_file}")
