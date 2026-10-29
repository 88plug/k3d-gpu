---
license: apache-2.0
base_model:
  - Qwen/Qwen3.8-27B
base_model_relation: quantized
pipeline_tag: image-text-to-text
library_name: openvino
---

# Qwen3.8-27B-int8-ov

- **Model creator:** [Qwen](https://huggingface.co/Qwen)
- **Original model:** [Qwen/Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B)

## Description

This is the [Qwen3.8-27B](https://huggingface.co/Qwen/Qwen3.8-27B) model converted to the OpenVINO™ IR (Intermediate Representation) format with weights compressed to INT8 by NNCF.

The model includes `openvino_mtp_model.xml` for built-in Multi-Token Prediction (MTP) speculative decoding.

Qwen3.8-27B is a native vision-language model with image and video understanding, flexible thinking control, and support for complex multi-step tasks.

## Quantization Parameters

Weight compression was performed using `nncf.compress_weights` with the following parameters:

- `mode`: **INT8_ASYM**
- `ratio`: **1.0**

For more information about quantization, see the [OpenVINO model optimization guide](https://docs.openvino.ai/2026/openvino-workflow/model-optimization-guide/weight-compression.html).

## Compatibility

The provided OpenVINO IR model is compatible with:

- OpenVINO 2026.4.0 and higher
- OpenVINO GenAI 2026.4.0.0 and higher
- The latest Optimum Intel development version
- Transformers 5.2.0

## Running Model Inference with Optimum Intel

Install the packages required to use Optimum Intel with the OpenVINO backend:

```bash
pip install -U "git+https://github.com/huggingface/optimum-intel.git" torchvision Pillow --extra-index-url https://download.pytorch.org/whl/cpu
pip install -U "openvino==2026.4.0"
pip install -U "transformers==5.2.0"
```

Run model inference:

```python
import requests
from PIL import Image
from transformers import AutoProcessor
from optimum.intel.openvino import OVModelForVisualCausalLM

model_id = "OpenVINO/Qwen3.8-27B-int8-ov"
processor = AutoProcessor.from_pretrained(model_id)
model = OVModelForVisualCausalLM.from_pretrained(model_id)

url = "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/transformers/tasks/ai2d-demo.jpg"
image = Image.open(requests.get(url, stream=True).raw)

messages = [
    {
        "role": "user",
        "content": [
            {"type": "image"},
            {"type": "text", "text": "Describe this image."},
        ],
    }
]

text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
inputs = processor(text=[text], images=[image], return_tensors="pt")

outputs = model.generate(**inputs, max_new_tokens=200)
print(processor.batch_decode(outputs[:, inputs.input_ids.shape[1] :], skip_special_tokens=True)[0])
```

For more examples and possible optimizations, refer to [Inference with Optimum Intel](https://huggingface.co/docs/optimum-intel/openvino/inference).

## Running Model Inference with OpenVINO GenAI

Install the packages required to use OpenVINO GenAI:

```bash
pip install -U huggingface_hub Pillow
pip install -U "openvino==2026.4.0" "openvino-tokenizers==2026.4.0.0" "openvino-genai==2026.4.0.0"
```

Download the model from Hugging Face Hub:

```python
import huggingface_hub as hf_hub

model_id = "OpenVINO/Qwen3.8-27B-int8-ov"
model_path = "Qwen3.8-27B-int8-ov"

hf_hub.snapshot_download(model_id, local_dir=model_path)
```

Run model inference with MTP:

```python
import numpy as np
import openvino as ov
import openvino_genai as ov_genai
import requests
from PIL import Image

device = "CPU"
scheduler_config = ov_genai.SchedulerConfig()
scheduler_config.enable_prefix_caching = False
draft = ov_genai.draft_model(model_path, device)
pipe = ov_genai.VLMPipeline(
    model_path,
    device,
    draft_model=draft,
    scheduler_config=scheduler_config,
)

url = "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/transformers/tasks/ai2d-demo.jpg"
image = Image.open(requests.get(url, stream=True).raw).convert("RGB")
image_tensor = ov.Tensor(np.array(image)[None])

generation_config = ov_genai.GenerationConfig()
generation_config.max_new_tokens = 200
generation_config.do_sample = False
generation_config.num_return_sequences = 1
generation_config.num_assistant_tokens = 2

print(pipe.generate("Describe this image.", image=image_tensor, generation_config=generation_config))
```

OpenVINO GenAI currently supports this MTP path with greedy decoding and prefix caching disabled, as demonstrated in the [Qwen3.8 MTP notebook](https://openvinotoolkit.github.io/openvino_notebooks/?search=qwen3.8-mtp).

More OpenVINO GenAI examples are available in:

- [OpenVINO GenAI documentation and samples](https://openvinotoolkit.github.io/openvino.genai/)
- [Qwen3-VL multimodal chatbot](https://github.com/openvinotoolkit/openvino_notebooks/tree/latest/notebooks/qwen3-vl)
- [Visual-language chatbot](https://github.com/openvinotoolkit/openvino_notebooks/tree/latest/notebooks/vlm-chatbot)

## Running Model with OpenAI client and [OpenVINO Model Server](https://github.com/openvinotoolkit/model_server)

For MTP deployment, see the [OVMS speculative decoding instructions](https://github.com/openvinotoolkit/model_server/tree/main/demos/continuous_batching/speculative_decoding).

1a. Deploy model on Windows using [binary package](https://docs.openvino.ai/ovms_baremetal):
```
curl -o ovms.zip https://storage.openvinotoolkit.org/repositories/openvino_model_server/packages/weekly/latest/ovms_windows_2026.4.0_python_on.zip
tar -xzf ovms.zip
ovms\setupvars.bat
set OVMS_MEDIA_URL_ALLOW_REDIRECTS=1
ovms.exe --rest_port 8000 --source_model OpenVINO/Qwen3.8-27B-int8-ov --model_repository_path C:\models --allowed_media_domains all
```
1b. Deploy model in a Docker container:
```
export GPU_ARGS=$(if ls /dev/dri/render* >/dev/null 2>&1; then echo "--device /dev/dri --group-add $(stat -c '%g' /dev/dri/render* | head -n1)"; fi)
docker run -d ${GPU_ARGS} -e "OVMS_MEDIA_URL_ALLOW_REDIRECTS=1" -u $(id -u):$(id -g) --rm -p 8000:8000 -v ${HOME}/models:/models:rw openvino/model_server:weekly \
--rest_port 8000 --model_repository_path /models --source_model OpenVINO/Qwen3.8-27B-int8-ov --allowed_media_domains all
```
2. Install the client library:

```
pip install openai
```
3. Run the client:
```
from openai import OpenAI

client = OpenAI(
  base_url="http://localhost:8000/v1",
  api_key="unused"
)

image_url = "https://huggingface.co/datasets/huggingface/documentation-images/resolve/main/transformers/tasks/ai2d-demo.jpg"

stream = client.chat.completions.create(
    model="OpenVINO/Qwen3.8-27B-int8-ov",
    messages=[
    {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": image_url}},
            {"type": "text", "text": "Describe this image."},
        ],
    }
    ],
    stream=True,
    extra_body={"chat_template_kwargs": {"reasoning_effort": "medium"}},
    tools=[],
)

printing_reasoning_started = False
printing_content_started = False
for chunk in stream:
    if not chunk.choices:
        continue
    delta = chunk.choices[0].delta

    content = getattr(delta, "content", None)
    reasoning = getattr(delta, "reasoning_content", None)

    if content:
        if not printing_content_started:
            printing_content_started = True
            print("\ncontent:\n", end="", flush=True)
        print(content, end="", flush=True)
    if reasoning:
        if not printing_reasoning_started:
            printing_reasoning_started = True
            print("reasoning_content:\n", end="", flush=True)
        print(reasoning, end="", flush=True)
```

## Limitations

Check the [original model card](https://huggingface.co/Qwen/Qwen3.8-27B) for model limitations and recommended generation settings.

## Legal Information

The original model is distributed under the [Apache License 2.0](https://huggingface.co/Qwen/Qwen3.8-27B/blob/main/LICENSE). This converted model is distributed under the same license.

## Disclaimer

Intel is committed to respecting human rights and avoiding causing or contributing to adverse impacts on human rights. See [Intel's Global Human Rights Principles](https://www.intel.com/content/www/us/en/policy/policy-human-rights.html). Intel's products and software are intended only to be used in applications that do not cause or contribute to adverse impacts to human rights.
