# -*- coding: utf-8 -*-
"""final_rag_faiss_hotpotqa.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1WkHAJBIKW4LpAwDCsUTbMcO-j_iYW7Tb

# RAG + FAISS Tabanlı Soru-Cevap Sistemi (HotpotQA)

Bu notebook, FAISS kullanarak belgeleri hızlı bir şekilde sorgulayan bir RAG sistemini HotpotQA veriseti üzerinde değerlendirir. Modeller:
- Retriever: `intfloat/e5-large-v2`
- Generator: `google/flan-t5-large`

Metrikler:
- F1 Score
- Cosine Similarity
- ROUGE-L
- Supporting Fact Match Score

# Flan-T5 Large Modeli
"""

import os
import json
import torch
import faiss
import numpy as np
from tqdm import tqdm
from datasets import load_dataset
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
from sentence_transformers import SentenceTransformer, util
from sklearn.metrics import f1_score
from rouge_score import rouge_scorer

with open("/content/drive/MyDrive/hotpotQA-data/hotpot_dev_distractor_v1.json", "r") as f:
    data = json.load(f)

retriever = SentenceTransformer("intfloat/e5-large-v2")

# Eğer dökümanlar daha önce kaydedilmişse yüklenir, yoksa hesaplanır
if os.path.exists("faiss_index.index") and os.path.exists("paragraph_embeddings.npy") and os.path.exists("paragraph_map.json"):
    print("Mevcut index ve veriler yükleniyor...")
    index = faiss.read_index("faiss_index.index")
    embeddings = np.load("paragraph_embeddings.npy")
    with open("paragraph_map.json", "r") as f:
        paragraph_map = json.load(f)

    # Paragraflar birleştirilir
    paragraphs_all = [" ".join(para) for example in data for (title, para) in example["context"]]
    print("Mevcut index ve veriler yüklendi.")

else:
    print("Veriler bulunamadı, oluşturuluyor...")
    paragraphs_all = []
    paragraph_map = []

    for i, ex in enumerate(data):
        for j, (title, para) in enumerate(ex["context"]):
            paragraphs_all.append(" ".join(para))
            paragraph_map.append((i, j))

    embeddings = retriever.encode(paragraphs_all, convert_to_numpy=True, show_progress_bar=True)
    index = faiss.IndexFlatL2(embeddings.shape[1])
    index.add(embeddings)

    faiss.write_index(index, "faiss_index.index")
    np.save("paragraph_embeddings.npy", embeddings)
    with open("paragraph_map.json", "w") as f:
        json.dump(paragraph_map, f)
    print("Veriler oluşturuldu.")

gen_tokenizer = AutoTokenizer.from_pretrained("google/flan-t5-large")
gen_model = AutoModelForSeq2SeqLM.from_pretrained("google/flan-t5-large").to("cuda" if torch.cuda.is_available() else "cpu")

def answer_question_with_rag(question, top_k=3):
    q_embedding = retriever.encode(question, convert_to_numpy=True)
    _, I = index.search(np.array([q_embedding]), top_k)
    selected_paragraphs = [paragraphs_all[i] for i in I[0]]
    input_text = f"question: {question} context: {' '.join(selected_paragraphs)}"
    inputs = gen_tokenizer(input_text, return_tensors="pt", truncation=True).to(gen_model.device)
    outputs = gen_model.generate(**inputs, max_new_tokens=64)
    return gen_tokenizer.decode(outputs[0], skip_special_tokens=True), I[0], selected_paragraphs

def compute_supporting_fact_match(example, top_k_indices, paragraph_map):
    supporting = example["supporting_facts"]
    support_indices = []
    for title, para_idx in supporting:
        for j, (ctx_title, _) in enumerate(example["context"]):
            if ctx_title == title:
                support_indices.append(j)
    top_hit = [pid for pid in top_k_indices if paragraph_map[pid][0] == example_id and paragraph_map[pid][1] in support_indices]
    return len(top_hit) / len(support_indices) if support_indices else 0.0

f1s, sims, rouges, sfm_scores = [], [], [], []
scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)

for example_id, example in tqdm(enumerate(data)):
    question = example["question"]
    gold_answer = example["answer"]
    pred, top_k_indices, selected_paragraphs = answer_question_with_rag(question)

    # F1
    pred_tokens = pred.lower().split()
    ans_tokens = gold_answer.lower().split()
    common = set(pred_tokens) & set(ans_tokens)
    if len(pred_tokens) == 0 or len(ans_tokens) == 0:
        f1 = 0
    else:
        precision = len(common) / len(pred_tokens)
        recall = len(common) / len(ans_tokens)
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    f1s.append(f1)

    # Cosine Similarity
    sim = util.cos_sim(retriever.encode(pred), retriever.encode(gold_answer)).item()
    sims.append(sim)

    # ROUGE-L
    rougeL = scorer.score(gold_answer, pred)["rougeL"].fmeasure
    rouges.append(rougeL)

    # Supporting Fact Match
    sfm = compute_supporting_fact_match(example, top_k_indices, paragraph_map)
    sfm_scores.append(sfm)

#Large
print(f"Avg F1 Score: {np.mean(f1s):.4f}")
print(f"Avg Cosine Similarity: {np.mean(sims):.4f}")
print(f"Avg ROUGE-L: {np.mean(rouges):.4f}")
print(f"Supporting Fact Match Score: {np.mean(sfm_scores):.4f}")

answer_question_with_rag("Where is the capital of France?",3)