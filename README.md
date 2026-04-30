# Multi-Agent Painter & Critic

This project implements Assignment 3 with the AG2 framework. A Painter agent draws on a 200x200 pixel canvas using registered drawing tools, and a Critic agent visually reviews the actual PNG output after every round. The Critic's feedback is passed into the next Painter step so the image can improve over multiple iterations.

## Subject Prompt

The chosen drawing subject is:

> A very simple, cheerful pixel-art scene with only five large elements: a bright blue sky, a green grass band, one big yellow smiling sun in the upper left, one red flower with a yellow center in the lower right, and two small white clouds. Use bold colors, clean outlines, and uncluttered shapes on the 200x200 canvas. Do not add extra objects or text.

## Design

The implementation uses two AG2 agent instances:

- `Painter`: a multimodal AG2 agent that receives the current canvas image and the latest Critic feedback. It modifies the canvas by calling registered drawing tools.
- `Critic`: a multimodal AG2 agent that receives the saved PNG for the round and returns structured visual feedback.

The canvas tools are executed by an AG2 `UserProxyAgent` named `CanvasToolExecutor`. It executes the Painter's tool calls and then stops that Painter step, which avoids a provider-specific issue where the proxy rejects follow-up LLM calls containing tool-result history. The Painter has five drawing tools available:

- `draw_rectangle` First linear simple area that helps draw bigger parts of the cancas picture.
- `draw_ellipse` Nonlinear area for figures such as the sun, or the flower.
- `draw_line` Second linear tool to "retouch" the initial drawing perform with the rectangles.
- `draw_polygon` A more polivalent figure to improve the detailness of the drawings in case more rounds were provided.
- `draw_text` I didn't know what else to give as an option

The round scheduler keeps the assignment structure simple: each round has exactly two main steps, Painter then Critic. Each step is performed through AG2's `initiate_chat` mechanism, and the script saves every round image as `outputs/round_XX.png`.

## Setup

Python 3.11 or 3.12 is recommended if dependency resolution complains, because AG2 and its provider extras can lag behind very new Python releases.

Create a virtual environment and install the dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Pass the AWS proxy URL when running the script:

```bash
python multi_agent_painter.py --rounds 10 --proxy-url "https://your-proxy-url-here"
```

No API key is required for the assignment proxy. The script sends `"not-needed"` as the placeholder API key because OpenAI-compatible clients usually require a non-empty value.

You can also set the proxy once as an environment variable:

```bash
export AWS_PROXY_BASE_URL="https://your-proxy-url-here"
python multi_agent_painter.py --rounds 10
```

## Run

Run the required 10 rounds:

```bash
python multi_agent_painter.py --rounds 10 --proxy-url "https://your-proxy-url-here"
```

Optional model choices from the assignment:

```bash
python multi_agent_painter.py --rounds 10 --proxy-url "https://your-proxy-url-here" --model openai/gpt-4.1-mini
python multi_agent_painter.py --rounds 10 --proxy-url "https://your-proxy-url-here" --model openai/gpt-4.1-nano
python multi_agent_painter.py --rounds 10 --proxy-url "https://your-proxy-url-here" --model qwen/qwen3.5-flash-02-23
```

The script writes:

- `outputs/round_01.png`
- `outputs/round_05.png`
- `outputs/round_10.png`
- `conversation_log.md`

For a quick local debugging run with fewer than 10 rounds:

```bash
python multi_agent_painter.py --rounds 2 --allow-short-run --proxy-url "https://your-proxy-url-here"
```

## Observations To Record After Running

After the proxy URL is provided and the full run completes, inspect `round_01.png`, `round_05.png`, and `round_10.png`. In the final submission:

- Well, on the first hand, it can be cleary seen that the 10 rounds do an iterative process for improving the final picture. However, the final picture lacks quality and rests far from the initial idea provided in the initial prompt.
- While the initial rounds emphasize the use of linear tools such as the draw rectangles and lines, more ellipses are drawn towards the last rounds of the drawing process.
- As a final note, it would be interesting to see how the picture evolves with a larger limit of rounds (e.g. 50, 100) 
