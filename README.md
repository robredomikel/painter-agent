# Multi-Agent Painter & Critic

This project implements Assignment 3 with the AG2 framework. A Painter agent draws on a 200x200 pixel canvas using registered drawing tools, and a Critic agent visually reviews the actual PNG output after every round. The Critic's feedback is passed into the next Painter step so the image can improve over multiple iterations.

## Subject Prompt

The chosen drawing subject is:

> A cheerful red-and-white lighthouse on a tiny green island at sunset, with blue sea waves, warm sky bands, a glowing lantern, clouds, and a few birds. The final image should read clearly as pixel-art style on a 200x200 canvas.

## Design

The implementation uses two AG2 agent instances:

- `Painter`: a multimodal AG2 agent that receives the current canvas image and the latest Critic feedback. It modifies the canvas by calling registered drawing tools.
- `Critic`: a multimodal AG2 agent that receives the saved PNG for the round and returns structured visual feedback.

The canvas tools are executed by an AG2 `UserProxyAgent` named `CanvasToolExecutor`. The Painter has five drawing tools available:

- `draw_rectangle`
- `draw_ellipse`
- `draw_line`
- `draw_polygon`
- `draw_text`

The round scheduler keeps the assignment structure simple: each round has exactly two main steps, Painter then Critic. Each step is performed through AG2's `initiate_chat` mechanism, and the script saves every round image as `outputs/round_XX.png`.

## Setup

Python 3.11 or 3.12 is recommended if dependency resolution complains, because AG2 and its provider extras can lag behind very new Python releases.

Create a virtual environment and install the dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Paste the AWS proxy URL into `AWS_PROXY_BASE_URL` in `multi_agent_painter.py`, replacing:

```python
AWS_PROXY_BASE_URL = "PASTE_AWS_PROXY_URL_HERE"
```

You can also avoid editing the file by setting an environment variable:

```bash
export AWS_PROXY_BASE_URL="https://your-proxy-url-here"
```

No API key is required for the assignment proxy. The script sends `"not-needed"` as the placeholder API key because OpenAI-compatible clients usually require a non-empty value.

## Run

Run the required 10 rounds:

```bash
python multi_agent_painter.py --rounds 10
```

Optional model choices from the assignment:

```bash
python multi_agent_painter.py --model openai/gpt-4.1-mini
python multi_agent_painter.py --model openai/gpt-4.1-nano
python multi_agent_painter.py --model qwen/qwen3.5-flash-02-23
```

The script writes:

- `outputs/round_01.png`
- `outputs/round_05.png`
- `outputs/round_10.png`
- `conversation_log.md`

For a quick local debugging run with fewer than 10 rounds:

```bash
python multi_agent_painter.py --rounds 2 --allow-short-run
```

## Observations To Record After Running

After the proxy is pasted and the full run completes, inspect `round_01.png`, `round_05.png`, and `round_10.png`. In the final submission, summarize:

- Whether the lighthouse, island, sea, and sunset are recognizable.
- Whether Critic feedback led to visible changes between rounds.
- Any recurring tool-use problems, such as shapes covering earlier details or the Painter making changes that are too small for a 200x200 image.
- Whether the final image has enough contrast and simple readable pixel-art forms.
