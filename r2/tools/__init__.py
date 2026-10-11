from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from r2 import claude_bridge, runctx
from r2.memory import Memory, SecretRejected
from r2.policy import apply_policy
from r2.privacy import EgressLedger, Gateway
from r2.skills import SkillError, SkillStore
from pathlib import Path

from r2.tools import apps, files, inputs, screen, shell, web
from r2.tools.browser import BrowserSession, classify_click as classify_browser_click
from r2.types import Risk, RiskInfo


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    func: Callable[..., str]
    risk: RiskInfo
    # Optional: decide the risk from the call's arguments (code, never the model).
    classify: Callable[[dict], RiskInfo] | None = None

    def risk_for(self, args: dict) -> RiskInfo:
        if self.classify is None:
            return self.risk
        try:
            return self.classify(args)
        except Exception:
            return RiskInfo(Risk.CONFIRM, "unclassified")

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self.browser: BrowserSession | None = None
        self.skills: SkillStore | None = None

    def close(self) -> None:
        if self.browser is not None:
            self.browser.close()
            self.browser = None

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [spec.schema() for spec in self._tools.values()]


def default_registry(memory: Memory) -> ToolRegistry:
    def remember(text: str) -> str:
        try:
            fact_id = memory.save_fact(text, "agent")
        except SecretRejected:
            return "Not saved: that looks like a password or card number."
        return f"Saved fact #{fact_id}"

    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            "list_dir",
            "List the files and folders in a directory.",
            {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path"}},
                "required": ["path"],
            },
            files.list_dir,
            RiskInfo(Risk.SAFE),
        )
    )
    reg.register(
        ToolSpec(
            "read_file",
            "Read a text file (first 4000 characters by default).",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["path"],
            },
            files.read_file,
            RiskInfo(Risk.SAFE),
            files.classify_read,
        )
    )
    reg.register(
        ToolSpec(
            "remember",
            "Save a fact the user asked you to remember. Never use for passwords or card numbers.",
            {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "The fact"}},
                "required": ["text"],
            },
            remember,
            RiskInfo(Risk.SAFE),
        )
    )
    return reg


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


_STR = {"type": "string"}
_INT = {"type": "integer"}


def full_registry(
    memory: Memory,
    data_dir=Path("data"),
    headless: bool = False,
    claude_dirs=(),
    claude_finder=claude_bridge.find_claude,
    profile: str | None = None,
) -> ToolRegistry:
    """Every tool R2 has. The browser is created lazily: Chrome only starts on first use."""
    data_dir = Path(data_dir)
    reg = default_registry(memory)
    reg.browser = session = BrowserSession(data_dir / "browser-profile", headless=headless)

    def add(name, desc, props, required, func, risk, classify=None):
        reg.register(ToolSpec(name, desc, _obj(props, required), func, risk, classify))

    safe = RiskInfo(Risk.SAFE)

    reg.skills = skills = SkillStore(data_dir)

    def use_skill(name: str) -> str:
        skill = skills.get(name.strip())
        if skill is None:
            known = ", ".join(s.name for s in skills.all()) or "none"
            return f"ERROR: no skill called '{name}'. Available: {known}"
        return f"Skill '{skill.name}' (follow these steps, using your normal tools):\n{skill.body}"

    def propose_skill(name: str, description: str, instructions: str) -> str:
        try:
            return skills.propose(name.strip(), description.strip(), instructions)
        except SkillError as exc:
            return f"ERROR: {exc}"

    add("use_skill", "Read the full instructions of one of your skills by name, then follow them.",
        {"name": _STR}, ["name"], use_skill, safe)
    add("propose_skill",
        "After finishing a task the user is likely to repeat, propose a reusable skill. It stays inactive until the user approves it.",
        {"name": {"type": "string", "description": "lowercase-with-hyphens"}, "description": _STR, "instructions": _STR},
        ["name", "description", "instructions"], propose_skill, safe)
    add("find_files", "Find files by name pattern (for example *.pdf) under a folder.",
        {"root": _STR, "pattern": _STR, "limit": _INT}, ["root", "pattern"], files.find_files, safe)
    add("write_file", "Write text to a file (creates or overwrites).",
        {"path": _STR, "content": _STR}, ["path", "content"], files.write_file,
        RiskInfo(Risk.CONFIRM, "write"))
    add("delete_file", "Delete a single file (never a folder).",
        {"path": _STR}, ["path"], files.delete_file, RiskInfo(Risk.CONFIRM, "delete"))
    add("run_shell", "Run a Windows shell command and return its output.",
        {"command": _STR, "timeout": _INT}, ["command"], shell.run_shell,
        RiskInfo(Risk.CONFIRM, "shell"), shell.classify_shell)
    add("open_app", "Open an application by plain name, such as notepad or chrome.",
        {"name": _STR}, ["name"], apps.open_app, safe, apps.classify_app)
    add("screenshot", "Capture the screen to a PNG file.",
        {}, [], lambda: screen.screenshot(str(data_dir / "screens" / "latest.png")), safe)
    add("list_windows", "List open windows; the active one is marked with *.",
        {}, [], screen.list_windows, safe)
    add("ui_elements", "List the named controls (buttons, fields) of the active window.",
        {"limit": _INT}, [], screen.ui_elements, safe)
    add("type_text", "Type text into the focused field.",
        {"text": _STR}, ["text"], inputs.type_text, safe)
    add("scroll", "Scroll the mouse wheel (positive up, negative down).",
        {"amount": _INT}, ["amount"], inputs.scroll, safe)
    add("click", "Click at screen coordinates. Prefer browser_click or keyboard navigation.",
        {"x": _INT, "y": _INT}, ["x", "y"], inputs.click,
        RiskInfo(Risk.CONFIRM, "click"), inputs.classify_click)
    add("press_key", "Press a key or combination such as tab, enter or ctrl+l.",
        {"keys": _STR}, ["keys"], inputs.press_key,
        RiskInfo(Risk.CONFIRM, "keys"), inputs.classify_key)
    tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
    gateway = Gateway(EgressLedger(data_dir / "egress.jsonl"))

    def search(query: str) -> str:
        if tavily_key:
            # Tavily is a hosted service, so the query is masked first and the call is written to the ledger.
            state = gateway.prepare([{"role": "user", "content": query}])
            masked = state.messages[0]["content"]
            try:
                out = web.tavily_search(masked, tavily_key)
                gateway.record(state, web.TAVILY_URL, "tavily-search", len(masked.encode("utf-8")), "ok")
                return out
            except Exception:
                gateway.record(state, web.TAVILY_URL, "tavily-search", len(masked.encode("utf-8")), "failed, used fallback")
        return web.web_search(query)

    add("web_search", "Search the web and return the top results.",
        {"query": _STR}, ["query"], search, safe, web.classify_query)
    add("web_fetch", "Fetch a public web page and return its text.",
        {"url": _STR, "max_chars": _INT}, ["url"], web.web_fetch, safe, web.classify_url)
    add("browser_open", "Open a URL in the browser.",
        {"url": _STR}, ["url"], session.open, safe, web.classify_url)
    add("browser_read", "Read the current page: text, form fields, buttons, links.",
        {}, [], session.read, safe)
    add("browser_fill", "Fill a form field, dropdown or checkbox by its label.",
        {"label": _STR, "text": _STR}, ["label", "text"], session.fill, safe)
    add("browser_click", "Click a button or link by its visible text.",
        {"text": _STR}, ["text"], session.click,
        RiskInfo(Risk.CONFIRM, "click"), classify_browser_click)

    if claude_finder():
        def ask_claude_tool(task: str, browser=False) -> str:
            stop = runctx.current_stop.get()
            return claude_bridge.ask_claude(
                task,
                browser=str(browser).lower() in ("true", "1", "yes"),
                dirs=claude_dirs,
                workdir=data_dir / "claude-work",
                find=claude_finder,
                should_stop=stop.is_set if stop is not None else None,
            )

        add("ask_claude",
            "Hand a task to Claude Code (uses the user's own Claude plan): browsing in the user's real Chrome, "
            "reading their files, web research. Set browser=true for tasks on web pages.",
            {"task": _STR, "browser": {"type": "boolean"}}, ["task"], ask_claude_tool,
            RiskInfo(Risk.CONFIRM, "delegate"))
    apply_policy(reg, data_dir, profile)  # tools you switched off (policy.json) or the demo profile are removed here
    return reg
