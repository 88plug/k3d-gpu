---
license: apache-2.0
tags:
- gemma4
- redhat
- neuralmagic
- speculators
- eagle3
---
# gemma4-31B-it-speculator.eagle3

This is a preliminary model release, we will continue to train the model and improve the acceptance rates in the next few days.   

## Model Overview
- **Verifier:** google/gemma-4-26B-A4B-it
- **Speculative Decoding Algorithm:** EAGLE-3
- **Model Architecture:** Eagle3Speculator
- **Release Date:** 04/13/2026
- **Version:** 1.0
- **Model Developers:** RedHat

This is a speculator model designed for use with [google/gemma-4-26B-A4B-it](https://huggingface.co/google/gemma-4-26B-A4B-it), based on the [EAGLE-3](https://arxiv.org/abs/2503.01840) speculative decoding algorithm.
It was trained using the [Speculators](https://github.com/vllm-project/speculators) library on a combination of the [Magpie-Align/Magpie-Llama-3.1-Pro-300K-Filtered](https://huggingface.co/datasets/Magpie-Align/Magpie-Llama-3.1-Pro-300K-Filtered) dataset and the `train_sft` split of the [HuggingFaceH4/ultrachat_200k](https://huggingface.co/datasets/HuggingFaceH4/ultrachat_200k) dataset. Training data used **Magpie + UltraChat with responses from the gemma-4-31B-it model (no reasoning)**.
This model should be used with the [google/gemma-4-26B-A4B-it](https://huggingface.co/google/gemma-4-26B-A4B-it) chat template, specifically through the `/chat/completions` endpoint.

## vLLM version


UPDATE: Now supported on vllm-main!  


## Use with vLLM

```bash
vllm serve google/gemma-4-26B-A4B-it \
  --tensor-parallel-size 2 \
  --speculative-config '{
    "model": "RedHatAI/gemma-4-26B-A4B-it-speculator.eagle3",
    "num_speculative_tokens": 3,
    "method": "eagle3"
  }' \
  --max-num-seqs 64 \
```

## Evaluations

**Model / run:**  
**vLLM:**   UPDATE: Now supported on vllm-main!  

**Training data:** Magpie + UltraChat; responses from the gemma 4 31B it model (no reasoning).

<h3>Use cases</h3>
<table>
  <thead>
    <tr>
      <th>Use Case</th>
      <th>Dataset</th>
      <th>Number of Samples</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Coding</td>
      <td>HumanEval</td>
      <td>164</td>
    </tr>
    <tr>
      <td>Math Reasoning</td>
      <td>math_reasoning</td>
      <td>80</td>
    </tr>
    <tr>
      <td>Question Answering</td>
      <td>qa</td>
      <td>80</td>
    </tr>
    <tr>
      <td>MT_bench (Question)</td>
      <td>question</td>
      <td>80</td>
    </tr>
    <tr>
      <td>RAG</td>
      <td>rag</td>
      <td>80</td>
    </tr>
    <tr>
      <td>Summarization</td>
      <td>summarization</td>
      <td>80</td>
    </tr>
    <tr>
      <td>Translation</td>
      <td>translation</td>
      <td>80</td>
    </tr>
  </tbody>
</table>

<h3>Acceptance lengths (draft length, temperature=default)</h3>
<table>
  <thead>
    <tr>
      <th>Dataset</th>
      <th>k=1</th>
      <th>k=2</th>
      <th>k=3</th>
      <th>k=4</th>
      <th>k=5</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>HumanEval</td>
      <td>1.79</td>
      <td>2.37</td>
      <td>2.75</td>
      <td>3.01</td>
      <td>3.18</td>
    </tr>
    <tr>
      <td>math_reasoning</td>
      <td>1.83</td>
      <td>2.48</td>
      <td>2.95</td>
      <td>3.28</td>
      <td>3.51</td>
    </tr>
    <tr>
      <td>qa</td>
      <td>1.57</td>
      <td>1.85</td>
      <td>1.99</td>
      <td>2.07</td>
      <td>2.09</td>
    </tr>
    <tr>
      <td>question</td>
      <td>1.66</td>
      <td>2.05</td>
      <td>2.29</td>
      <td>2.40</td>
      <td>2.47</td>
    </tr>
    <tr>
      <td>rag</td>
      <td>1.64</td>
      <td>2.02</td>
      <td>2.21</td>
      <td>2.30</td>
      <td>2.36</td>
    </tr>
    <tr>
      <td>summarization</td>
      <td>1.53</td>
      <td>1.77</td>
      <td>1.87</td>
      <td>1.91</td>
      <td>1.93</td>
    </tr>
    <tr>
      <td>translation</td>
      <td>1.61</td>
      <td>1.95</td>
      <td>2.13</td>
      <td>2.23</td>
      <td>2.31</td>
    </tr>
  </tbody>
</table>

<details>