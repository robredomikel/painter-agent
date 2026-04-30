from __future__ import annotations

import argparse
import ast
import base64
import copy
import json
import os
import sys
from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

Image = None
ImageColor = None
ImageDraw = None


CANVAS_SIZE = 200
DEFAULT_ROUNDS = 10
DEFAULT_MODEL = "openai/gpt-4.1-mini"

SUBJECT_PROMPT = (
    "A very simple, cheerful pixel-art scene with only five large elements: "
    "a bright blue sky, a green grass band, one big yellow smiling sun in the "
    "upper left, one red flower with a yellow center in the lower right, and "
    "two small white clouds. Use bold colors, clean outlines, and uncluttered "
    "shapes on the 200x200 canvas. Do not add extra objects or text."
)


def import_ag2() -> tuple[Any, Any, Any, Any | None]:
    try:
        from autogen import AssistantAgent, LLMConfig, UserProxyAgent
    except ImportError as exc:
        raise SystemExit(
            "AG2 is not installed. Install dependencies with:\n"
            "  python -m pip install -r requirements.txt"
        ) from exc

    try:
        from autogen.agentchat.contrib.multimodal_conversable_agent import (
            MultimodalConversableAgent,
        )
    except ImportError:
        MultimodalConversableAgent = None

    return AssistantAgent, LLMConfig, UserProxyAgent, MultimodalConversableAgent


def patch_ag2_image_formatter() -> None:
    """Make AG2's multimodal formatter tolerant of already formatted image URLs."""

    try:
        from autogen.agentchat.contrib import img_utils
        from autogen.agentchat.contrib import multimodal_conversable_agent
    except ImportError:
        return

    def to_data_uri(image_value: Any) -> str:
        import_pillow()

        if hasattr(image_value, "save"):
            buffered = BytesIO()
            image_value.save(buffered, format="PNG")
            encoded = base64.b64encode(buffered.getvalue()).decode("ascii")
            return f"data:image/png;base64,{encoded}"

        if isinstance(image_value, str):
            value = image_value.strip()

            # Already valid for OpenAI-compatible vision APIs.
            if value.startswith("data:image/"):
                return value

            if value.startswith(("http://", "https://")):
                return value

            # Support file:// paths.
            if value.startswith("file://"):
                value = value.removeprefix("file://")

            path = Path(value).expanduser()

            # Also try resolving relative paths.
            if not path.exists():
                path = Path.cwd() / value

            if path.exists():
                with Image.open(path) as image:
                    buffered = BytesIO()
                    image.convert("RGB").save(buffered, format="PNG")
                    encoded = base64.b64encode(buffered.getvalue()).decode("ascii")
                    return f"data:image/png;base64,{encoded}"

            # Important: do not crash with an unhelpful type-only error.
            raise TypeError(
                "Unsupported string image_url value for AG2 multimodal message: "
                f"{image_value!r}. Expected a data URI, http(s) URL, or existing file path."
            )

        raise TypeError(
            "Unsupported image_url value for AG2 multimodal message: "
            f"{type(image_value)!r}"
        )

    def safe_message_formatter(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        new_messages = []

        for message in messages:
            if isinstance(message, dict) and isinstance(message.get("content"), list):
                message = copy.deepcopy(message)

                for item in message["content"]:
                    if isinstance(item, dict) and isinstance(item.get("image_url"), dict):
                        item["image_url"]["url"] = to_data_uri(
                            item["image_url"]["url"]
                        )

            new_messages.append(message)

        return new_messages

    img_utils.message_formatter_pil_to_b64 = safe_message_formatter
    multimodal_conversable_agent.message_formatter_pil_to_b64 = safe_message_formatter


def import_pillow() -> None:
    global Image, ImageColor, ImageDraw
    if Image is not None:
        return
    try:
        from PIL import Image as PILImage
        from PIL import ImageColor as PILImageColor
        from PIL import ImageDraw as PILImageDraw
    except ImportError as exc:
        raise SystemExit(
            "Pillow is not installed. Install dependencies with:\n"
            "  python -m pip install -r requirements.txt"
        ) from exc
    Image = PILImage
    ImageColor = PILImageColor
    ImageDraw = PILImageDraw


def is_done_message(message: dict[str, Any]) -> bool:
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip().upper().endswith("DONE")
    return False


def make_one_way_tool_executor(UserProxyAgent: Any) -> Any:
    class OneWayToolExecutor(UserProxyAgent):
        """Execute tool calls and stop before provider-incompatible follow-up calls."""

        def receive(
            self,
            message: dict[str, Any] | str,
            sender: Any,
            request_reply: bool | None = None,
            silent: bool | None = False,
        ) -> None:
            self._process_received_message(message, sender, silent)
            if request_reply is False:
                return

            messages = self.chat_messages[sender]
            last_message = messages[-1] if messages else {}
            if isinstance(last_message, dict) and last_message.get("tool_calls"):
                final, reply = self.generate_tool_calls_reply(messages=messages, sender=sender)
                if final and reply is not None:
                    self._append_oai_message(
                        reply,
                        sender,
                        role=reply.get("role", "assistant"),
                        name=self.name,
                    )
                return

            return None

    return OneWayToolExecutor


@dataclass
class DrawingCanvas:
    output_dir: Path
    width: int = CANVAS_SIZE
    height: int = CANVAS_SIZE
    background: str = "#f7fbff"
    image: Image.Image = field(init=False)
    draw: ImageDraw.ImageDraw = field(init=False)
    operation_count: int = 0

    def __post_init__(self) -> None:
        import_pillow()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.image = Image.new("RGB", (self.width, self.height), self.background)
        self.draw = ImageDraw.Draw(self.image)

    def reset_round_counter(self) -> None:
        self.operation_count = 0

    def save(self, filename: str) -> Path:
        path = self.output_dir / filename
        self.image.save(path)
        return path

    def save_round(self, round_number: int) -> Path:
        return self.save(f"round_{round_number:02d}.png")

    def draw_rectangle(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        fill: str,
        outline: str = "",
        width: int = 1,
    ) -> str:
        try:
            box = self._box(x0, y0, x1, y1)
            fill_color = self._color(fill)
            outline_color = self._optional_color(outline)
            self.draw.rectangle(
                box,
                fill=fill_color,
                outline=outline_color,
                width=self._stroke_width(width),
            )
            self.operation_count += 1
            return f"Rectangle drawn at {box} with fill {fill}."
        except Exception as exc:
            return f"Tool error in draw_rectangle: {exc}"

    def draw_ellipse(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        fill: str,
        outline: str = "",
        width: int = 1,
    ) -> str:
        try:
            box = self._box(x0, y0, x1, y1)
            fill_color = self._color(fill)
            outline_color = self._optional_color(outline)
            self.draw.ellipse(
                box,
                fill=fill_color,
                outline=outline_color,
                width=self._stroke_width(width),
            )
            self.operation_count += 1
            return f"Ellipse drawn at {box} with fill {fill}."
        except Exception as exc:
            return f"Tool error in draw_ellipse: {exc}"

    def draw_line(self, points_json: str, color: str, width: int = 3) -> str:
        try:
            points = self._points(points_json, minimum=2)
            stroke_width = self._stroke_width(width)
            self.draw.line(points, fill=self._color(color), width=stroke_width, joint="curve")
            self.operation_count += 1
            return f"Line drawn through {len(points)} points with color {color}."
        except Exception as exc:
            return f"Tool error in draw_line: {exc}"

    def draw_polygon(
        self,
        points_json: str,
        fill: str,
        outline: str = "",
        width: int = 1,
    ) -> str:
        try:
            points = self._points(points_json, minimum=3)
            self.draw.polygon(points, fill=self._color(fill))
            outline_color = self._optional_color(outline)
            if outline_color is not None:
                closed_points = points + [points[0]]
                self.draw.line(
                    closed_points,
                    fill=outline_color,
                    width=self._stroke_width(width),
                    joint="curve",
                )
            self.operation_count += 1
            return f"Polygon drawn with {len(points)} points and fill {fill}."
        except Exception as exc:
            return f"Tool error in draw_polygon: {exc}"

    def draw_text(self, x: int, y: int, text: str, color: str = "#111111") -> str:
        try:
            point = (self._clamp(x), self._clamp(y))
            label = str(text)[:24]
            self.draw.text(point, label, fill=self._color(color))
            self.operation_count += 1
            return f"Text '{label}' drawn at {point}."
        except Exception as exc:
            return f"Tool error in draw_text: {exc}"

    def _box(self, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
        left = self._clamp(min(x0, x1))
        top = self._clamp(min(y0, y1))
        right = self._clamp(max(x0, x1))
        bottom = self._clamp(max(y0, y1))
        if right == left:
            right = min(self.width - 1, left + 1)
        if bottom == top:
            bottom = min(self.height - 1, top + 1)
        return left, top, right, bottom

    def _points(self, points_json: str, minimum: int) -> list[tuple[int, int]]:
        try:
            raw = json.loads(points_json)
        except json.JSONDecodeError:
            raw = ast.literal_eval(points_json)

        if not isinstance(raw, list) or len(raw) < minimum:
            raise ValueError(f"Expected at least {minimum} points as JSON, e.g. [[10, 10], [20, 20]].")

        points: list[tuple[int, int]] = []
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise ValueError("Each point must be a two-item [x, y] list.")
            points.append((self._clamp(item[0]), self._clamp(item[1])))
        return points

    def _clamp(self, value: Any) -> int:
        return max(0, min(self.width - 1, int(round(float(value)))))

    def _stroke_width(self, value: Any) -> int:
        return max(1, min(40, int(round(float(value)))))

    def _color(self, value: str) -> tuple[int, int, int]:
        if not value:
            raise ValueError("A color is required. Use a CSS color name or #RRGGBB.")
        return ImageColor.getrgb(value)

    def _optional_color(self, value: str) -> tuple[int, int, int] | None:
        if not value:
            return None
        return self._color(value)


def image_path_to_data_uri(path: Path) -> str:

    import_pillow()

    with Image.open(path) as image:

        buffered = BytesIO()

        image.convert("RGB").save(buffered, format="PNG")

        encoded = base64.b64encode(buffered.getvalue()).decode("ascii")

    return f"data:image/png;base64,{encoded}"


def vision_message(text: str, image_path: Path) -> dict[str, Any]:
    return {
        "content": [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {
                    "url": image_path_to_data_uri(image_path),
                    "detail": "high",
                },
            },
        ]
    }


def make_llm_config(LLMConfig: Any, base_url: str, model: str, temperature: float) -> Any:
    model_entry = {
        "api_type": "openai",
        "model": model,
        "base_url": base_url,
        "api_key": "not-needed",
        "price": [0, 0],
    }
    try:
        return LLMConfig(config_list=[model_entry], temperature=temperature, timeout=120)
    except TypeError:
        return LLMConfig(model_entry, temperature=temperature, timeout=120)


def build_agents(
    canvas: DrawingCanvas,
    base_url: str,
    model: str,
    painter_temperature: float,
    critic_temperature: float,
) -> tuple[Any, Any, Any, Any]:
    AssistantAgent, LLMConfig, UserProxyAgent, MultimodalConversableAgent = import_ag2()
    patch_ag2_image_formatter()
    VisionAgent = MultimodalConversableAgent or AssistantAgent
    OneWayToolExecutor = make_one_way_tool_executor(UserProxyAgent)

    painter_config = make_llm_config(LLMConfig, base_url, model, painter_temperature)
    critic_config = make_llm_config(LLMConfig, base_url, model, critic_temperature)

    painter = VisionAgent(
        name="Painter",
        system_message=(
            "You are the Painter agent in a two-agent AG2 painting system. "
            "Your job is to create and refine a 200x200 pixel-art canvas for the subject. "
            "You can see the current rendered canvas in the incoming multimodal message. "
            "Draw by calling the canvas tools; do not merely describe what you would draw. "
            "Use several tool calls per round, preserve strong existing features, and apply "
            "the Critic's feedback directly. Coordinates use x,y with (0,0) at the top-left "
            "and (199,199) at the bottom-right. Prefer broad shapes and clear color blocks "
            "over single pixels. Keep the scene simple and readable: refine the sun, flower, "
            "clouds, sky, and grass instead of adding new objects. After tool calls, reply "
            "with a concise summary ending in DONE."
        ),
        llm_config=painter_config,
        human_input_mode="NEVER",
        max_consecutive_auto_reply=12,
    )

    critic = VisionAgent(
        name="Critic",
        system_message=(
            "You are the Critic agent in a two-agent AG2 painting system. "
            "You receive the actual rendered PNG canvas as a multimodal image. "
            "Base your feedback on what you can visually see, not on assumptions. "
            "Give structured, actionable feedback with these headings: WORKS WELL, "
            "CHANGES NEEDED, NEXT PAINTER INSTRUCTIONS. Be specific about shapes, "
            "colors, approximate coordinates, contrast, and missing subject details."
        ),
        llm_config=critic_config,
        human_input_mode="NEVER",
        max_consecutive_auto_reply=2,
    )

    tool_executor = OneWayToolExecutor(
        name="CanvasToolExecutor",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=is_done_message,
        max_consecutive_auto_reply=16,
        default_auto_reply="Use the registered drawing tools, then summarize the changes.",
    )

    reviewer = UserProxyAgent(
        name="ReviewRequester",
        human_input_mode="NEVER",
        code_execution_config=False,
        max_consecutive_auto_reply=1,
        default_auto_reply="TERMINATE",
    )

    @tool_executor.register_for_execution()
    @painter.register_for_llm(description="Draw a filled rectangle on the 200x200 canvas.")
    def draw_rectangle(
        x0: Annotated[int, "Left x coordinate, 0 to 199"],
        y0: Annotated[int, "Top y coordinate, 0 to 199"],
        x1: Annotated[int, "Right x coordinate, 0 to 199"],
        y1: Annotated[int, "Bottom y coordinate, 0 to 199"],
        fill: Annotated[str, "Fill color as a CSS name or #RRGGBB"],
        outline: Annotated[str, "Optional outline color, or empty string"] = "",
        width: Annotated[int, "Optional outline width in pixels"] = 1,
    ) -> str:
        return canvas.draw_rectangle(x0, y0, x1, y1, fill, outline, width)

    @tool_executor.register_for_execution()
    @painter.register_for_llm(description="Draw a filled ellipse or circle on the 200x200 canvas.")
    def draw_ellipse(
        x0: Annotated[int, "Left x coordinate, 0 to 199"],
        y0: Annotated[int, "Top y coordinate, 0 to 199"],
        x1: Annotated[int, "Right x coordinate, 0 to 199"],
        y1: Annotated[int, "Bottom y coordinate, 0 to 199"],
        fill: Annotated[str, "Fill color as a CSS name or #RRGGBB"],
        outline: Annotated[str, "Optional outline color, or empty string"] = "",
        width: Annotated[int, "Optional outline width in pixels"] = 1,
    ) -> str:
        return canvas.draw_ellipse(x0, y0, x1, y1, fill, outline, width)

    @tool_executor.register_for_execution()
    @painter.register_for_llm(
        description=(
            "Draw a continuous line or polyline. points_json must be valid JSON like "
            "'[[10, 20], [40, 60], [80, 30]]'."
        )
    )
    def draw_line(
        points_json: Annotated[str, "JSON list of [x, y] points"],
        color: Annotated[str, "Line color as a CSS name or #RRGGBB"],
        width: Annotated[int, "Line width in pixels"] = 3,
    ) -> str:
        return canvas.draw_line(points_json, color, width)

    @tool_executor.register_for_execution()
    @painter.register_for_llm(
        description=(
            "Draw a filled polygon. points_json must be valid JSON like "
            "'[[50, 50], [70, 80], [30, 80]]'."
        )
    )
    def draw_polygon(
        points_json: Annotated[str, "JSON list of at least three [x, y] points"],
        fill: Annotated[str, "Fill color as a CSS name or #RRGGBB"],
        outline: Annotated[str, "Optional outline color, or empty string"] = "",
        width: Annotated[int, "Optional outline width in pixels"] = 1,
    ) -> str:
        return canvas.draw_polygon(points_json, fill, outline, width)

    @tool_executor.register_for_execution()
    @painter.register_for_llm(description="Draw a short text label on the canvas.")
    def draw_text(
        x: Annotated[int, "Left x coordinate, 0 to 199"],
        y: Annotated[int, "Top y coordinate, 0 to 199"],
        text: Annotated[str, "Short text, at most 24 characters"],
        color: Annotated[str, "Text color as a CSS name or #RRGGBB"] = "#111111",
    ) -> str:
        return canvas.draw_text(x, y, text, color)

    return painter, critic, tool_executor, reviewer


def painter_prompt(round_number: int, total_rounds: int, feedback: str) -> str:
    return (
        f"Round {round_number} of {total_rounds}.\n"
        f"Subject prompt: {SUBJECT_PROMPT}\n\n"
        "The current canvas image is attached. Modify the image using your drawing tools. "
        "Use at least three meaningful drawing tool calls this round unless the image is already final. "
        "Focus on visible improvement at 200x200 resolution.\n\n"
        f"Critic feedback to apply:\n{feedback}\n\n"
        "When finished, briefly summarize the changes and end with DONE."
    )


def critic_prompt(round_number: int, total_rounds: int) -> str:
    return (
        f"Round {round_number} of {total_rounds} review.\n"
        "The image produced by the Painter is attached. Review the actual image visually. "
        "Return concise feedback under WORKS WELL, CHANGES NEEDED, and NEXT PAINTER INSTRUCTIONS. "
        "The next instructions should be concrete enough for tool-based drawing on a 200x200 canvas. "
        "Prioritize a clean, attractive simple picture over adding more details."
    )


def content_to_log_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif block.get("type") == "image_url":
                parts.append("[attached image data URI omitted from log]")
            else:
                parts.append(json.dumps(block, default=str))
        return "\n".join(part for part in parts if part)
    return json.dumps(content, default=str)


def format_message(message: dict[str, Any]) -> str:
    role = message.get("role", "message")
    name = message.get("name")
    label = f"{role} ({name})" if name else role
    chunks = [f"**{label}**"]

    if "content" in message and message["content"] not in (None, ""):
        chunks.append(content_to_log_text(message["content"]))
    if "tool_calls" in message:
        chunks.append("Tool calls:")
        chunks.append(f"```json\n{json.dumps(message['tool_calls'], indent=2, default=str)}\n```")
    if "function_call" in message:
        chunks.append("Function call:")
        chunks.append(f"```json\n{json.dumps(message['function_call'], indent=2, default=str)}\n```")
    if len(chunks) == 1:
        chunks.append(f"```json\n{json.dumps(message, indent=2, default=str)}\n```")
    return "\n\n".join(chunks)


def append_chat_log(lines: list[str], heading: str, chat_result: Any) -> None:
    lines.append(f"### {heading}")
    history = getattr(chat_result, "chat_history", None) or []
    if not history:
        lines.append("_No chat history returned by AG2._")
        return
    for message in history:
        lines.append(format_message(message))


def last_text_from_chat(chat_result: Any) -> str:
    history = getattr(chat_result, "chat_history", None) or []
    for message in reversed(history):
        content = message.get("content")
        text = content_to_log_text(content).strip() if content is not None else ""
        if text and not text.startswith("[attached image"):
            return text
    summary = getattr(chat_result, "summary", "")
    return str(summary).strip()


def validate_proxy_url(proxy_url: str) -> str:
    if not proxy_url:
        raise SystemExit(
            "Missing AWS proxy URL. Pass it at execution time, for example:\n"
            "  python multi_agent_painter.py --rounds 10 --proxy-url https://your-proxy-url"
        )
    return proxy_url


def run(args: argparse.Namespace) -> None:
    if args.rounds < DEFAULT_ROUNDS and not args.allow_short_run:
        raise SystemExit("The assignment requires at least 10 rounds. Use --allow-short-run only for debugging.")

    proxy_url = validate_proxy_url(args.proxy_url or os.getenv("AWS_PROXY_BASE_URL", ""))
    output_dir = Path(args.output_dir)
    canvas = DrawingCanvas(output_dir=output_dir)
    painter, critic, tool_executor, reviewer = build_agents(
        canvas=canvas,
        base_url=proxy_url,
        model=args.model,
        painter_temperature=args.painter_temperature,
        critic_temperature=args.critic_temperature,
    )

    log_lines = [
        "# Multi-Agent Painter & Critic Conversation Log",
        "",
        f"Model: `{args.model}`",
        f"Rounds: `{args.rounds}`",
        f"Subject prompt: {SUBJECT_PROMPT}",
        "",
    ]

    current_image = canvas.save("round_00_initial.png")
    feedback = (
        "No previous critique yet. Establish the whole simple scene with large shapes: "
        "blue sky, green grass, big smiling yellow sun, one red flower, and two white clouds."
    )

    for round_number in range(1, args.rounds + 1):
        canvas.reset_round_counter()
        log_lines.append(f"## Round {round_number:02d}")

        painter_chat = tool_executor.initiate_chat(
            painter,
            message=vision_message(painter_prompt(round_number, args.rounds, feedback), current_image),
            clear_history=True,
            summary_method="last_msg",
            silent=args.silent,
        )
        append_chat_log(log_lines, "Step 1: Painter Produces Or Updates The Drawing", painter_chat)
        if canvas.operation_count == 0:
            log_lines.append(
                "**Warning:** The Painter did not execute any drawing tools in this round. "
                "Try rerunning or adjusting the Painter prompt/model."
            )

        current_image = canvas.save_round(round_number)
        log_lines.append(f"Saved image: `{current_image}`")

        critic_chat = reviewer.initiate_chat(
            critic,
            message=vision_message(critic_prompt(round_number, args.rounds), current_image),
            clear_history=True,
            max_turns=1,
            summary_method="last_msg",
            silent=args.silent,
        )
        append_chat_log(log_lines, "Step 2: Critic Reviews The Actual Image", critic_chat)
        feedback = last_text_from_chat(critic_chat) or "No critic feedback was returned; continue improving the subject."

        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        args.log_file.write_text("\n\n".join(log_lines) + "\n", encoding="utf-8")

    print(f"Done. Images saved in: {output_dir}")
    print(f"Conversation log saved to: {args.log_file}")
    print("Key deliverables: round_01.png, round_05.png, round_10.png, conversation_log.md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AG2 multi-agent Painter and Critic assignment.")
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS, help="Number of Painter/Critic rounds.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model name.")
    parser.add_argument(
        "--proxy-url",
        "--base-url",
        dest="proxy_url",
        default="",
        help="AWS proxy URL for the OpenAI-compatible endpoint.",
    )
    parser.add_argument("--output-dir", default="outputs", help="Directory where round images are saved.")
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path("conversation_log.md"),
        help="Markdown file for the full conversation log.",
    )
    parser.add_argument("--painter-temperature", type=float, default=0.35, help="Painter model temperature.")
    parser.add_argument("--critic-temperature", type=float, default=0.2, help="Critic model temperature.")
    parser.add_argument("--silent", action="store_true", help="Suppress AG2 console output.")
    parser.add_argument(
        "--allow-short-run",
        action="store_true",
        help="Allow fewer than 10 rounds for local debugging only.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        run(parse_args())
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
