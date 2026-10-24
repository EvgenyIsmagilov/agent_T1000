"""Tests for gen_dsh.py through main(): a synthetic asar, agent files and patch file, fake secrets only."""
import json
import os
import re
import shutil
import stat
import struct
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Self

import pytest
from ruamel.yaml import YAML

import gen_dsh

PRESET_PATH = "/dsh/node_modules/@deepseek-ai/dsh-web-app/presets/standard.patch.yml"
WEB_PACKAGE_PATH = "/dsh/node_modules/@deepseek-ai/dsh-web-app/package.json"
BASE_PATCH_PATH = "/dsh/node_modules/@deepseek-ai/dsh-base/cordis.patch.yml"
CATALOG_PATH = "/dsh/node_modules/@earendil-works/pi-ai/dist/providers/data/openrouter.json"

AGENTS = [
  "reviewer", "planner", "finding-verifier", "bug-investigator", "test-runner",
  "web-researcher", "docs-researcher", "code-writer-t1", "code-writer-t2", "code-writer-t3",
]
AGENT_TOOLS = {  # `tools:` line of the fake agent files; the other agents get "Read, Grep, Glob"
  "reviewer": "Read, Bash",
  "web-researcher": "WebSearch, WebFetch, Read",
  "code-writer-t1": "Read, Grep, Glob, Edit, Write, Bash, Skill",
}
TRINO_TOOL = "mcp__trino__explain_query"
NO_GUARDS = "No Claude Code frontmatter hooks or permission modes apply in DSH"

# The shipped delegation group lets the orchestrator fork itself; the generator switches that row off.
SHIPPED_FORK = """\
              - id: tool-subagent-fork
                name: '@deepseek-ai/dsh-tool-subagent'
                config:
                  provider: fork
                  toolName: subagent_fork
                  backgroundMode: continuable
"""
# The shipped preset keeps what the generator touches, a row the user may delete (tool-fs), a row the
# user may switch on (tool-ralph), the fork row and one `!!js` row (the app's own tag).
PRESET = f"""\
# Shipped preset (test copy).
- insert:
    - id: preset-standard
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: standard
        order: 1
        plugins:
          - id: tool-bash
            name: '@deepseek-ai/dsh-tool-bash'
            disabled: !!js process.platform === 'win32'
          - id: tool-fs
            name: '@deepseek-ai/dsh-tool-fs'
          - id: skill-filesystem
            name: '@deepseek-ai/dsh-skill-filesystem'
          - id: delegation
            name: cordis:group
            group: true
            config:
              - id: tool-subagent
                name: '@deepseek-ai/dsh-tool-subagent'
                config:
                  provider: spawn
                  toolName: subagent
{SHIPPED_FORK}\
              - id: tool-ralph
                name: '@deepseek-ai/dsh-tool-ralph'
                disabled: true
                config:
                  maxRounds: 64
"""
PRESET_WITHOUT_FORK = PRESET.replace(SHIPPED_FORK, "")

OTHER_PRESETS = {
  name: (
    '- insert:\n    - id: preset-' + name + '\n      name: "@deepseek-ai/dsh-agent-preset"\n'
    '      config:\n        id: ' + name + '\n        order: 3\n        plugins:\n'
    '          - id: original-tool\n            name: "@deepseek-ai/dsh-tool-todo"\n'
    '            config: {allowParallelInProgress: true}\n'
  ) for name in ("ptc", "minimal", "cordis")
}

HEADER = """\
# Profile patch (test fixture, fake data only).
# Second header line.
"""
UI_CHAT = """\
- id: ui-chat
  name: "@deepseek-ai/dsh-client-ui-chat"
  config:
    theme: dark  # my inline comment

# comment between entries
"""
LLM = """\
- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      openrouter:
        models:
          - id: "~google/gemini-pro-latest"
          - id: "~z-ai/glm-latest"
            maxTokens: 4096
        apiKey: "sk-or-FAKE-OPENROUTER-0001"
      custom:
        displayName: Custom
        baseURL: https://custom.invalid/v1
        models:
          - id: custom-1
        apiKey: sk-FAKE-CUSTOM-0002
"""
# A dspark route somebody has edited: a key the tool never sets (apiKey) and stale values of its own.
DSPARK_EDITED = """\
      dspark:
        displayName: Old Spark
        apiKey: sk-FAKE-DSPARK-0004
"""
# The same route with an endpoint of the user's own.
DSPARK_OWN_URL = DSPARK_EDITED + "        baseURL: https://own.example.invalid/v1\n"
DSPARK_URL = "https://model.example.invalid/v1"  # reserved TLD: never resolves
DEEPSEEK = """\
- id: llm-deepseek
  name: "@deepseek-ai/dsh-llm-deepseek-api-key"
  config:
    apiKey: sk-FAKE-DEEPSEEK-0003
    models: []
"""
PATCH = HEADER + UI_CHAT + LLM + DEEPSEEK

MCP_TOP_LEVEL = """\
- id: mcp-trino
  name: "@deepseek-ai/dsh-mcp-client"
  config:
    serverName: trino
"""
MCP_INSERTED = """\
- insert:
    - id: mcp-trino
      name: "@deepseek-ai/dsh-mcp-client"
      config:
        serverName: trino
"""
MCP_OTHER_SERVER = MCP_TOP_LEVEL.replace("trino", "other")

# The fork row as the Web editor saved it: switched on, otherwise as shipped.
EXISTING_FORK = """\
          - id: tool-subagent-fork
            name: "@deepseek-ai/dsh-tool-subagent"
            disabled: false
            config:
              provider: fork
              toolName: subagent_fork
              backgroundMode: continuable
"""
# The same row in the shapes the tool meets besides that: no `disabled` key (an entry saved before
# the tool owned the field) and another field edited by the user.
FORK_WITHOUT_DISABLED = EXISTING_FORK.replace("            disabled: false\n", "")
FORK_EDITED = EXISTING_FORK.replace("backgroundMode: continuable", "backgroundMode: one-shot")
# A preset-standard entry the Web editor has saved: tool-fs deleted, tool-ralph and the fork row
# switched on, a row of the user's own, a skills dir of the user's own. The delegation group comes
# last, so more rows can be appended to the text.
EXISTING_PRESET = f"""\
- id: preset-standard
  name: "@deepseek-ai/dsh-agent-preset"
  config:
    id: standard
    order: 1
    plugins:
      - id: tool-bash
        name: "@deepseek-ai/dsh-tool-bash"
        disabled: !!js process.platform === 'win32'
      - id: skill-filesystem
        name: "@deepseek-ai/dsh-skill-filesystem"
        config:
          customSkillDirs:
            - /opt/my-skills
      - id: my-extra
        name: "@deepseek-ai/dsh-tool-todo"
      - id: delegation
        name: cordis:group
        group: true
        config:
          - id: tool-subagent
            name: "@deepseek-ai/dsh-tool-subagent"
            config:
              provider: spawn
              toolName: subagent
{EXISTING_FORK}\
          - id: tool-ralph
            name: "@deepseek-ai/dsh-tool-ralph"
            disabled: false
            config:
              maxRounds: 64
"""
# A generator row that is no longer produced: the run removes it, so every line lands in the diff as a
# removed line. It carries a secret in every shape the printed diff has to hide.
STALE_ROW = """\
          - id: cc-agent-legacy
            name: "@deepseek-ai/dsh-tool-subagent"
            config:
              toolName: legacy
              apiKey: FAKE-apiKey
              api_key: FAKE-api_key
              "token": FAKE-token
              secret: FAKE-secret
              password: FAKE-password
              key: FAKE-key
              credential: FAKE-credential
              auth: FAKE-auth
              apiKeyValue: FAKE-apiKeyValue
              headers:
                Authorization: Bearer FAKE-authorization
              args: [--api-key, FAKE-args]
              url: https://user:FAKE-url-pass@host.invalid/path?key=FAKE-query
              baseURL: https://user:FAKE-baseurl-pass@host.invalid:8000/v1/FAKE-baseurl-path/x;FAKE-baseurl-param?key=FAKE-baseurl-query
              env:
                - OPENAI_API_KEY=FAKE-env-item
                - {name: a, apiKey: FAKE-flow-item}
              inline: {token: FAKE-inline-token, maxTokens: 777}
              glued_seq: [x,#FAKE-glued-seq-comment
                y]
              glued_map: {k: v,#FAKE-glued-map-comment
                z: w}
              note: |
                FAKE-block-line
              persona: |  # FAKE-header-comment
                Persona line kept.
              credentials:
                secret:
                  - FAKE-list-1
                  - FAKE-list-2
              apiKeyEnv: LEGACY_API_KEY
              maxTokens: 555
              extra: x  # FAKE-comment
"""
FAKE_SECRETS = [
  "FAKE-apiKey", "FAKE-api_key", "FAKE-token", "FAKE-secret", "FAKE-password", "FAKE-key",
  "FAKE-credential", "FAKE-auth", "FAKE-apiKeyValue", "FAKE-authorization", "FAKE-args",
  "FAKE-url-pass", "FAKE-query", "FAKE-baseurl-pass", "FAKE-baseurl-query", "FAKE-env-item",
  "FAKE-flow-item", "FAKE-inline-token", "FAKE-block-line", "FAKE-list-1", "FAKE-list-2",
  "FAKE-comment", "FAKE-baseurl-path", "FAKE-baseurl-param", "FAKE-glued-seq-comment",
  "FAKE-glued-map-comment", "FAKE-header-comment",
  "sk-or-FAKE-OPENROUTER-0001", "sk-FAKE-CUSTOM-0002", "sk-FAKE-DEEPSEEK-0003",
]
HOOKS = """\
hooks:
  PreToolUse:
    - matcher: Bash
      hooks:
        - type: command
          command: guard.sh
"""

# The hooks row as an earlier run wrote it (stale path, a key of the user's own), and the override the
# app's plugin manager appends when the user switches the row off.
HOOKS_INSERT = """\
- insert:
    - id: hooks-claude-code
      name: "@deepseek-ai/dsh-hooks-claude-code"
      config:
        configPath: /old/dsh-hooks.json
        defaultTimeoutMs: 5000
"""
HOOKS_OVERRIDE = """\
- id: hooks-claude-code
  disabled: true
"""
# An mcp-client row of another preset: it mounts for that preset's agents, not for the standard ones.
PTC_PRESET_WITH_MCP = """\
- id: preset-ptc
  name: "@deepseek-ai/dsh-agent-preset"
  config:
    id: ptc
    plugins:
      - id: mcp-trino
        name: "@deepseek-ai/dsh-mcp-client"
        config:
          serverName: trino
"""

# Skills the tool never links from ~/.claude/skills, whatever they contain.
EXCLUDED_SKILLS = (
  "task-orchestration", "code-tier-assessment", "mcp-servers", "magi", "writing-great-agents",
  "writing-great-skills", "synced",
)
CLAUDE_MD = """\
# Global instructions
Reply in Russian.
@~/.claude/persona.md
  @not-an-import.md
Mention @notes.md inline.
Last line.
"""
PERSONA = "FAKE-PERSONA-LINE one\nFAKE-PERSONA-LINE two\n"
AGENTS_HEADER = (
  "<!-- generated by ~/.claude/tools/gen_dsh.py from ~/.claude/CLAUDE.md, do not edit: rerun the tool -->\n\n"
)
# What the tool must write for CLAUDE_MD and PERSONA, spelled out by hand: the header, the persona
# in place of the import line, and every other line (an indented `@` line, an inline mention) as it was.
AGENTS_MD = (
  AGENTS_HEADER + "# Global instructions\nReply in Russian.\n"
  "FAKE-PERSONA-LINE one\nFAKE-PERSONA-LINE two\n"
  "  @not-an-import.md\nMention @notes.md inline.\nLast line.\n"
)


def model(*levels: str) -> dict[str, Any]:
  """Catalog entry of a reasoning model that supports exactly `levels`, shaped like the real catalog."""
  every = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
  return {"reasoning": True, "thinkingLevelMap": {name: name if name in levels else None for name in every}}


CATALOG = {
  "anthropic-messages": {"~google/gemini-flash-latest": model("low", "medium", "high")},
  "openai-completions": {
    "qwen/qwen3.8-max-0902": model("minimal", "low", "medium", "high"),  # no xhigh: reviewer is clamped
    "~google/gemini-pro-latest": model("low", "medium", "high"),
    "~z-ai/glm-latest": model("low", "high", "max"),
    "~deepseek/deepseek-v4-flash-latest": model("low", "high", "max"),
  },
}


def make_asar(files: dict[str, bytes]) -> bytes:
  """Minimal asar: size pickle, header pickle holding the JSON index padded to 4 bytes, then file data."""
  tree: dict[str, Any] = {"files": {}}
  blob = b""
  for path, content in files.items():
    *dirs, name = path.strip("/").split("/")
    node = tree
    for part in dirs:
      node = node["files"].setdefault(part, {"files": {}})
    node["files"][name] = {"size": len(content), "offset": str(len(blob))}
    blob += content
  index = json.dumps(tree).encode()
  payload = struct.pack("<I", len(index)) + index + b"\0" * (-len(index) % 4)
  header = struct.pack("<I", len(payload)) + payload
  return struct.pack("<II", 4, len(header)) + header + blob


class World:
  """A throwaway DSH profile (patch file, app.asar, agents dir, skills, instructions) driven through main().

  The root doubles as the home dir (the fixture sets HOME), so `~` in CLAUDE.md imports lands in it."""

  def __init__(self, root: Path) -> None:
    self.root = root
    self.patch = root / "cordis.patch.yml"
    self.asar = root / "app.asar"
    self.agents = root / "agents"
    self.claude_skills = root / ".claude/skills"
    self.local_skills = root / ".dsh/skills-local"
    self.farm = root / ".dsh/skills-shared"
    self.claude_md = root / ".claude/CLAUDE.md"
    self.agents_md = root / ".dsh/AGENTS.md"
    self.hooks_config = root / ".claude/hooks/dsh-hooks.json"
    self.agents.mkdir()
    self.patch.write_text(PATCH, encoding="utf-8")
    self.set_catalog(CATALOG)
    for name in AGENTS:
      self.set_tools(name, AGENT_TOOLS.get(name, "Read, Grep, Glob"))
    # skills: two plain ones, every excluded name (with a SKILL.md, so only the exclusion keeps it out),
    # a dir and a file that are no skills, and a local skill beside two that shadow excluded names
    for name in ("alpha", "beta", *EXCLUDED_SKILLS):
      self.add_skill(self.claude_skills, name)
    (self.claude_skills / "notes").mkdir()
    (self.claude_skills / "README.md").write_text("not a skill\n", encoding="utf-8")
    for name in ("task-orchestration", "code-tier-assessment", "local-only"):
      self.add_skill(self.local_skills, name)
    self.claude_md.write_text(CLAUDE_MD, encoding="utf-8")
    (root / ".claude/persona.md").write_text(PERSONA, encoding="utf-8")
    self.hooks_config.parent.mkdir(parents=True)
    self.hooks_config.write_text("{}\n", encoding="utf-8")

  def add_skill(self, base: Path, name: str) -> Path:
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    return path

  def links(self) -> dict[str, str]:
    """The symlinks of the farm by name, each with the text of its target."""
    return {p.name: os.readlink(p) for p in sorted(self.farm.iterdir()) if p.is_symlink()}

  def set_catalog(
    self, catalog: dict[str, Any], preset: str = PRESET, *, base_extra: str = "", web_extra: str = ""
  ) -> None:
    files = {PRESET_PATH: preset.encode(), CATALOG_PATH: json.dumps(catalog).encode()}
    patches = ["./cordis.patch.yml", "./presets/standard.patch.yml", *(f"./presets/{name}.patch.yml" for name in OTHER_PRESETS)]
    files[WEB_PACKAGE_PATH] = json.dumps({"dsh": {"bundle": {"patch": patches}}}).encode()
    files[BASE_PATCH_PATH] = (
      '- insert:\n    - id: tool-working-directory\n'
      '      name: "@deepseek-ai/dsh-tool-working-directory"\n'
      '    - id: tool-fs\n      name: "@deepseek-ai/dsh-tool-fs"\n'
      + base_extra
    ).encode()
    files["/dsh/node_modules/@deepseek-ai/dsh-web-app/cordis.patch.yml"] = (
      '- id: tool-fs\n  disabled: true\n' + web_extra
    ).encode()
    for name, text in OTHER_PRESETS.items():
      files[f"/dsh/node_modules/@deepseek-ai/dsh-web-app/presets/{name}.patch.yml"] = text.encode()
    self.asar.write_bytes(make_asar(files))

  def set_tools(self, name: str, tools: str, frontmatter: str = "") -> None:
    text = f"---\nname: {name}\ndescription: fake\ntools: {tools}\n{frontmatter}---\n\nBody of {name}.\n"
    (self.agents / f"{name}.md").write_text(text, encoding="utf-8")

  def run(
    self, flag: str, *, app_running: Callable[[], bool] = lambda: False, dspark_url: str | None = DSPARK_URL
  ) -> int:
    argv = [
      flag, "--patch-file", str(self.patch), "--asar", str(self.asar), "--agents-dir", str(self.agents),
      "--claude-md", str(self.claude_md), "--agents-md", str(self.agents_md),
      "--claude-skills", str(self.claude_skills), "--dsh-skills-local", str(self.local_skills),
      "--skills-farm", str(self.farm), "--hooks-config", str(self.hooks_config),
    ]
    if dspark_url is not None:
      argv += ["--dspark-url", dspark_url]
    return gen_dsh.main(argv, app_running=app_running)

  def backups(self) -> list[Path]:
    return sorted(self.root.glob("cordis.patch.yml.bak-*"))

  def assert_untouched(self, before: bytes) -> None:
    assert self.patch.read_bytes() == before
    assert list(self.root.glob("cordis.patch.yml*")) == [self.patch]  # no backup, no temp file
    assert not self.farm.exists()  # and none of the other outputs
    assert not self.agents_md.exists()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
  monkeypatch.setenv("HOME", str(tmp_path))  # the skills dir (and any default path) stays inside tmp_path
  monkeypatch.delenv("DSPARK_BASE_URL", raising=False)  # a developer's own setting must not leak into the tests
  return World(tmp_path)


def parse(text: str) -> list[dict[str, Any]]:
  # the safe loader does not know the app's `!!js` tag, so it is dropped first
  result: list[dict[str, Any]] = YAML(typ="safe").load(text.replace("!!js ", ""))
  return result


def entry(text: str, row_id: str) -> dict[str, Any]:
  return next(e for e in parse(text) if e.get("id") == row_id)


def preset_rows(text: str) -> dict[str, dict[str, Any]]:
  return {row["id"]: row for row in entry(text, "preset-standard")["config"]["plugins"]}


def agent_rows(text: str) -> dict[str, dict[str, Any]]:
  return {row["id"]: row for row in preset_rows(text)["delegation"]["config"]}


def role_presets(text: str) -> dict[str, dict[str, Any]]:
  return {
    row["config"]["id"]: row for e in parse(text) for row in e.get("insert", [])
    if row.get("name") == "@deepseek-ai/dsh-agent-preset"
    and row.get("config", {}).get("id", "").startswith("cc-agent-")
  }


def role_plugins(world: World, name: str) -> dict[str, dict[str, Any]]:
  preset = role_presets(world.patch.read_text(encoding="utf-8"))[f"cc-agent-{name}"]
  return {row["id"]: row for row in preset["config"]["plugins"]}


def role_persona(world: World, name: str) -> str:
  text: str = role_plugins(world, name)["persona"]["config"]["prefix"]
  return text


def role_tools(world: World, name: str) -> set[str]:
  tools: set[str] = set()
  for row in role_plugins(world, name).values():
    plugin = row["name"]
    config = row.get("config", {})
    if plugin == "@deepseek-ai/dsh-tool-fs":
      assert set(config) == {"read", "readImage", "write", "edit"}
      assert config["readImage"] is False
      tools.update(t for t in ("read", "write", "edit") if config[t])
    elif plugin == "@deepseek-ai/dsh-tool-fs-search":
      assert config == {"sampleOverCapGlobResults": False}
      tools.update(("grep", "glob"))
    elif plugin == "@deepseek-ai/dsh-tool-bash":
      tools.add("bash")
    elif plugin == "@deepseek-ai/dsh-tool-skill":
      tools.add("skill")
    elif plugin == "@deepseek-ai/dsh-tool-web":
      assert set(config) == {"search", "fetch"}
      tools.update(t for t, key in (("web_search", "search"), ("web_fetch", "fetch")) if config[key])
    else:
      assert plugin in {
        "@deepseek-ai/dsh-persona", "@deepseek-ai/dsh-agent-instructions",
        "@deepseek-ai/dsh-skill-filesystem",
      }, f"Unexpected plugin: {plugin}"
  return tools


def with_fork(row: str) -> str:
  """The patch file with the Web editor's preset entry, its fork row replaced by `row` ('' deletes it)."""
  return PATCH + EXISTING_PRESET.replace(EXISTING_FORK, row)


def hooks_rows(text: str) -> list[dict[str, Any]]:
  """Every row with the hooks row's id that an `insert` entry of the patch file holds."""
  return [row for e in parse(text) for row in e.get("insert", []) if row.get("id") == "hooks-claude-code"]


def warnings_of(out: str) -> str:
  """The Warnings section of a printed plan, empty when there is none."""
  return out.partition("Warnings:")[2].partition("Owned entries:")[0]


def test_agent_rows_carry_tools_model_and_clamped_effort(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  assert world.run("--write") == 0
  rows = agent_rows(world.patch.read_text(encoding="utf-8"))

  assert list(rows) == [
    "tool-subagent", "tool-subagent-fork", "tool-ralph", *(f"cc-agent-{name}" for name in AGENTS),
  ]
  reviewer = rows["cc-agent-reviewer"]["config"]
  assert reviewer["toolName"] == "reviewer"
  assert reviewer["agentPreset"] == "cc-agent-reviewer"
  assert "persona" not in reviewer and "toolFilter" not in reviewer
  assert role_tools(world, "reviewer") == {"read", "bash"}
  # reviewer asks for xhigh, the catalog model tops out at high
  assert reviewer["agentOptions"] == {
    "provider": "openrouter", "model": "qwen/qwen3.8-max-0902", "reasoningEffort": "high",
  }
  assert role_persona(world, "reviewer").endswith("\n\nBody of reviewer.\n")
  assert "reviewer: effort 'xhigh' is not supported by qwen/qwen3.8-max-0902; using 'high'" in (
    capsys.readouterr().out
  )

  researcher = rows["cc-agent-web-researcher"]["config"]
  assert researcher["agentPreset"] == "cc-agent-web-researcher"
  assert role_tools(world, "web-researcher") == {"web_search", "web_fetch", "read"}
  assert researcher["agentOptions"] == {"provider": "dspark", "model": "deepseek-v4-flash"}

  writer = rows["cc-agent-code-writer-t1"]["config"]
  assert role_tools(world, "code-writer-t1") == {"read", "grep", "glob", "edit", "write", "bash", "skill"}
  assert writer["agentOptions"] == {
    "provider": "openrouter", "model": "~deepseek/deepseek-v4-flash-latest", "reasoningEffort": "max",
  }
  assert rows["cc-agent-planner"]["config"]["agentOptions"]["reasoningEffort"] == "high"


def test_preset_keeps_shipped_rows_and_registers_the_skills_dir(world: World) -> None:
  assert world.run("--write") == 0
  text = world.patch.read_text(encoding="utf-8")

  assert list(preset_rows(text)) == ["tool-bash", "tool-fs", "skill-filesystem", "delegation", "cc-inherited-working-directory"]
  assert agent_rows(text)["tool-ralph"]["disabled"] is True  # no entry yet: the shipped row as it is
  assert preset_rows(text)["skill-filesystem"]["config"] == {
    "customSkillDirs": [str(world.root / ".dsh/skills-shared")],
  }
  assert "disabled: !!js process.platform === 'win32'" in text  # the app's tag is written back as it was


def test_fork_row_is_switched_off_and_otherwise_as_shipped(world: World) -> None:
  assert world.run("--write") == 0
  rows = agent_rows(world.patch.read_text(encoding="utf-8"))

  assert rows["tool-subagent-fork"] == {
    "id": "tool-subagent-fork", "name": "@deepseek-ai/dsh-tool-subagent", "disabled": True,
    "config": {"provider": "fork", "toolName": "subagent_fork", "backgroundMode": "continuable"},
  }
  assert "disabled" not in rows["tool-subagent"]  # the spawn row the roles go through stays on


def test_existing_preset_keeps_the_users_rows_and_warns_where_they_differ(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.patch.write_text(PATCH + EXISTING_PRESET + STALE_ROW, encoding="utf-8")

  assert world.run("--write") == 0
  out = capsys.readouterr().out
  text = world.patch.read_text(encoding="utf-8")

  rows = preset_rows(text)
  assert list(rows) == ["tool-bash", "skill-filesystem", "my-extra", "delegation", "cc-inherited-working-directory"]  # the user's layout
  assert rows["skill-filesystem"]["config"]["customSkillDirs"] == [
    "/opt/my-skills", str(world.root / ".dsh/skills-shared"),
  ]
  children = agent_rows(text)
  assert children["tool-ralph"]["disabled"] is False  # the Web editor's edit survives
  # the stale generator row is gone, the current ones are in role order after the user's rows
  assert list(children) == [
    "tool-subagent", "tool-subagent-fork", "tool-ralph", *(f"cc-agent-{name}" for name in AGENTS),
  ]
  warnings = out.split("Warnings:")[1].split("Owned entries:")[0]
  assert "row 'tool-ralph' differs from the shipped preset" in warnings
  assert "shipped row 'tool-fs' is missing from the existing entry" in warnings
  assert "row 'my-extra' of the existing entry is not in the shipped preset" in warnings
  assert warnings.count("preset-standard:") == 3

  assert world.run("--dry-run") == 0  # the written entry needs nothing more
  assert "No changes." in capsys.readouterr().out


@pytest.mark.parametrize(
  "fork, before", [(EXISTING_FORK, False), (FORK_WITHOUT_DISABLED, None)], ids=["switched-on", "no-key"]
)
def test_existing_fork_row_is_switched_off_without_a_differs_warning(
  world: World, capsys: pytest.CaptureFixture[str], fork: str, before: bool | None
) -> None:
  world.patch.write_text(with_fork(fork), encoding="utf-8")
  saved = agent_rows(world.patch.read_text(encoding="utf-8"))["tool-subagent-fork"]
  assert saved.get("disabled") == before  # the starting point is the one the case names

  assert world.run("--write") == 0
  out = capsys.readouterr().out
  written = world.patch.read_bytes()

  row = agent_rows(written.decode("utf-8"))["tool-subagent-fork"]
  assert row["disabled"] is True
  assert row["config"] == {"provider": "fork", "toolName": "subagent_fork", "backgroundMode": "continuable"}
  assert "row 'tool-ralph' differs from the shipped preset" in warnings_of(out)  # the entry is still compared
  assert "tool-subagent-fork" not in warnings_of(out)
  assert re.search(r"tool-subagent-fork +update", out)  # the owned-entries table names the change

  assert world.run("--write") == 0
  out = capsys.readouterr().out
  assert "already up to date" in out
  assert "tool-subagent-fork" not in warnings_of(out)  # the tool's own edit is no departure from shipped
  assert re.search(r"tool-subagent-fork +unchanged", out)
  assert world.patch.read_bytes() == written


def test_existing_fork_row_keeps_its_other_edits_and_still_differs(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.patch.write_text(with_fork(FORK_EDITED), encoding="utf-8")

  assert world.run("--write") == 0
  out = capsys.readouterr().out

  row = agent_rows(world.patch.read_text(encoding="utf-8"))["tool-subagent-fork"]
  assert row["disabled"] is True
  assert row["config"]["backgroundMode"] == "one-shot"  # the user's edit stays
  assert "row 'tool-subagent-fork' differs from the shipped preset; kept as it is" in warnings_of(out)


@pytest.mark.parametrize(
  "shipped, patch",
  [
    pytest.param(PRESET_WITHOUT_FORK, PATCH, id="fresh-shipped-lacks-it"),
    pytest.param(PRESET, with_fork(""), id="entry-lacks-it"),
    pytest.param(PRESET_WITHOUT_FORK, with_fork(""), id="both-lack-it"),
  ],
)
def test_missing_fork_row_is_reported_and_not_invented(
  world: World, capsys: pytest.CaptureFixture[str], shipped: str, patch: str
) -> None:
  world.set_catalog(CATALOG, shipped)
  world.patch.write_text(patch, encoding="utf-8")

  assert world.run("--write") == 0
  out = capsys.readouterr().out

  rows = agent_rows(world.patch.read_text(encoding="utf-8"))
  assert list(rows) == ["tool-subagent", "tool-ralph", *(f"cc-agent-{name}" for name in AGENTS)]
  assert "row 'tool-subagent-fork' is missing from the delegation group" in warnings_of(out)

  assert world.run("--dry-run") == 0  # what was written needs nothing more
  assert "No changes." in capsys.readouterr().out


def test_rerun_after_an_agent_changed_updates_the_entry_in_place(
  world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
  class TickingClock(datetime):  # two writes in one test need two distinct backup names
    ticks = 0

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
      cls.ticks += 1
      return cls(2026, 1, 1, tzinfo=tz) + timedelta(seconds=cls.ticks)

  monkeypatch.setattr(gen_dsh, "datetime", TickingClock)
  assert world.run("--write") == 0
  first_tools = role_tools(world, "planner")
  world.set_tools("planner", "Read, Grep, Glob, Bash")
  assert world.run("--write") == 0
  second = world.patch.read_text(encoding="utf-8")

  assert role_tools(world, "planner") == {"read", "grep", "glob", "bash"}
  assert first_tools == {"read", "grep", "glob"}
  assert second.count("generated by ~/.claude/tools/gen_dsh.py") == 16  # one per generated entry, not stacked
  assert second.startswith(HEADER + UI_CHAT)
  assert len(world.backups()) == 2


@pytest.mark.parametrize("patch", [PATCH, PATCH + PTC_PRESET_WITH_MCP, PATCH + EXISTING_PRESET.replace(
  "      - id: my-extra\n",
  '      - id: mcp-trino\n        name: "@deepseek-ai/dsh-mcp-client"\n'
  "        config:\n          serverName: trino\n      - id: my-extra\n",
)])
def test_mcp_tools_are_always_omitted_from_role_presets(
  world: World, capsys: pytest.CaptureFixture[str], patch: str
) -> None:
  world.set_tools("reviewer", f"Read, Bash, {TRINO_TOOL}")
  world.patch.write_text(patch, encoding="utf-8")

  assert world.run("--write") == 0

  out = capsys.readouterr().out
  assert role_tools(world, "reviewer") == {"read", "bash"}
  assert f"reviewer: omitted {TRINO_TOOL}" in warnings_of(out)
  assert "MCP tools are not included in generated role presets" in warnings_of(out)
  assert TRINO_TOOL in role_persona(world, "reviewer")
  assert "Not available to you here" in role_persona(world, "reviewer")
  assert "Not available" not in role_persona(world, "planner")
  for preset in role_presets(world.patch.read_text(encoding="utf-8")).values():
    assert all(row["name"] != "@deepseek-ai/dsh-mcp-client" for row in preset["config"]["plugins"])
  if "preset-ptc" in patch:
    assert entry(world.patch.read_text(encoding="utf-8"), "preset-ptc")["config"]["plugins"][:-1] == entry(patch, "preset-ptc")["config"]["plugins"]


@pytest.mark.parametrize("client_rows", [MCP_TOP_LEVEL, MCP_INSERTED, MCP_OTHER_SERVER])
def test_global_mcp_is_refused_before_writing_because_children_would_inherit_its_tools(
  world: World, capsys: pytest.CaptureFixture[str], client_rows: str
) -> None:
  world.patch.write_text(PATCH + client_rows, encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "global MCP tools would be inherited" in capsys.readouterr().err
  world.assert_untouched(before)


@pytest.mark.parametrize("client_rows", [MCP_TOP_LEVEL, MCP_INSERTED])
def test_disabled_global_mcp_is_kept_and_does_not_grant_tools(world: World, client_rows: str) -> None:
  indent = "      " if client_rows == MCP_INSERTED else "  "
  patch = PATCH + client_rows + indent + "disabled: true\n"
  world.patch.write_text(patch, encoding="utf-8")

  assert world.run("--write") == 0

  assert client_rows + indent + "disabled: true\n" in world.patch.read_text(encoding="utf-8")
  assert role_tools(world, "reviewer") == {"read", "bash"}


def test_role_left_without_any_tool_is_an_error(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.set_tools("reviewer", TRINO_TOOL)
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "reviewer: no tools left after omitting MCP tools" in capsys.readouterr().err
  world.assert_untouched(before)


def test_agent_hooks_are_reported_and_every_persona_says_no_guard_applies(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.set_tools("reviewer", "Read, Bash", frontmatter=HOOKS)

  assert world.run("--write") == 0

  out = capsys.readouterr().out
  assert "reviewer: frontmatter hooks are not ported" in out
  assert out.count("frontmatter hooks") == 1  # only the agent that has them
  assert NO_GUARDS in role_persona(world, "reviewer")
  assert NO_GUARDS in role_persona(world, "planner")
  assert role_tools(world, "reviewer") == {"read", "bash"}  # bash stays


def test_providers_merge_adds_dspark_and_missing_models_and_keeps_the_rest(world: World) -> None:
  old = entry(PATCH, "llm-pi-ai")["config"]["providers"]
  assert world.run("--write") == 0
  text = world.patch.read_text(encoding="utf-8")
  providers = entry(text, "llm-pi-ai")["config"]["providers"]

  assert providers["custom"] == old["custom"]
  assert providers["openrouter"]["apiKey"] == "sk-or-FAKE-OPENROUTER-0001"
  models = providers["openrouter"]["models"]
  assert models[:2] == old["openrouter"]["models"]  # existing ones stay in place, extras included
  assert len(models) == 5
  assert {m["id"] for m in models[2:]} == {
    "qwen/qwen3.8-max-0902", "~deepseek/deepseek-v4-flash-latest", "~google/gemini-flash-latest",
  }
  assert providers["dspark"]["apiKeyEnv"] == "DSPARK_API_KEY"
  assert providers["dspark"]["baseURL"] == DSPARK_URL
  assert "apiKey" not in providers["dspark"]
  assert providers["dspark"]["models"][0]["id"] == "deepseek-v4-flash"
  assert entry(text, "llm-deepseek") == entry(PATCH, "llm-deepseek")
  # literal keys are written exactly as they were: same value, same quoting, never masked
  for line in (
    'apiKey: "sk-or-FAKE-OPENROUTER-0001"', "apiKey: sk-FAKE-CUSTOM-0002", "apiKey: sk-FAKE-DEEPSEEK-0003",
  ):
    assert line in text
  assert "<redacted>" not in text


def test_existing_dspark_route_keeps_keys_the_tool_does_not_set_and_names_them(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.patch.write_text(HEADER + UI_CHAT + LLM + DSPARK_EDITED + DEEPSEEK, encoding="utf-8")

  assert world.run("--write") == 0
  out = capsys.readouterr().out

  dspark = entry(world.patch.read_text(encoding="utf-8"), "llm-pi-ai")["config"]["providers"]["dspark"]
  assert dspark["apiKey"] == "sk-FAKE-DSPARK-0004"  # the user's key stays
  assert dspark["apiKeyEnv"] == "DSPARK_API_KEY"  # the tool's own keys are set
  assert dspark["displayName"] != "Old Spark"
  assert "dspark route: kept key 'apiKey'" in out
  assert "sk-FAKE-DSPARK-0004" not in out  # the key is named, its value is not


def test_merge_providers_preserves_user_endpoint_and_credentials(world: World) -> None:
  world.patch.write_text(HEADER + UI_CHAT + LLM + DSPARK_OWN_URL + DEEPSEEK, encoding="utf-8")

  assert world.run("--write") == 0  # the option is given, the user's own endpoint still wins

  dspark = entry(world.patch.read_text(encoding="utf-8"), "llm-pi-ai")["config"]["providers"]["dspark"]
  assert dspark["baseURL"] == "https://own.example.invalid/v1"
  assert dspark["apiKey"] == "sk-FAKE-DSPARK-0004"
  assert dspark["apiKeyEnv"] == "DSPARK_API_KEY"


def test_dspark_url_comes_from_the_environment_and_the_option_wins(
  world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.setenv("DSPARK_BASE_URL", "https://env.example.invalid/v1")
  assert world.run("--dry-run", dspark_url=None) == 0  # dry-run shares the code path
  assert world.run("--write", dspark_url=None) == 0
  providers = entry(world.patch.read_text(encoding="utf-8"), "llm-pi-ai")["config"]["providers"]
  assert providers["dspark"]["baseURL"] == "https://env.example.invalid/v1"

  world.patch.write_text(PATCH, encoding="utf-8")
  for backup in world.backups():  # the backup name has one-second resolution
    backup.unlink()
  assert world.run("--write") == 0  # the option is given as well as the variable
  providers = entry(world.patch.read_text(encoding="utf-8"), "llm-pi-ai")["config"]["providers"]
  assert providers["dspark"]["baseURL"] == DSPARK_URL


@pytest.mark.parametrize("flag", ["--dry-run", "--write"])
def test_no_dspark_endpoint_stops_before_anything_is_written(
  world: World, capsys: pytest.CaptureFixture[str], flag: str
) -> None:
  before = world.patch.read_bytes()

  assert world.run(flag, dspark_url=None) == 1

  err = capsys.readouterr().err
  assert "--dspark-url" in err and "DSPARK_BASE_URL" in err
  world.assert_untouched(before)


def test_rerun_leaves_the_file_byte_identical(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  assert world.run("--write") == 0
  first = world.patch.read_bytes()
  capsys.readouterr()

  assert world.run("--write") == 0
  assert "already up to date" in capsys.readouterr().out
  assert world.patch.read_bytes() == first
  assert len(world.backups()) == 1  # only the first run backed anything up

  assert world.run("--dry-run") == 0
  assert "No changes." in capsys.readouterr().out


def test_foreign_entries_and_header_comments_stay_byte_for_byte(world: World) -> None:
  assert world.run("--write") == 0
  text = world.patch.read_text(encoding="utf-8")

  assert text.startswith(HEADER)
  assert UI_CHAT in text
  assert DEEPSEEK in text
  # Новые записи идут после исходных; у корневых insert нет собственного id.
  assert [e.get("id") for e in parse(text)] == [
    "ui-chat", "llm-pi-ai", "llm-deepseek", "preset-standard", *([None] * 11),
    "preset-ptc", "preset-minimal", "preset-cordis", "tool-working-directory",
  ]
  assert len(hooks_rows(text)) == 1


def test_unknown_tool_stops_the_run_before_anything_is_written(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.set_tools("planner", "Read, Telepathy")
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "planner: unknown tool 'Telepathy'" in capsys.readouterr().err
  world.assert_untouched(before)


def test_unknown_model_stops_the_run_before_anything_is_written(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  catalog = {api: dict(models) for api, models in CATALOG.items()}
  del catalog["openai-completions"]["~z-ai/glm-latest"]
  world.set_catalog(catalog)
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "finding-verifier: model '~z-ai/glm-latest' is not in the OpenRouter catalog" in (
    capsys.readouterr().err
  )
  world.assert_untouched(before)


def test_catalog_chat_prefix_is_stripped_and_image_records_are_skipped(tmp_path: Path) -> None:
  catalog = {
    "openai-completions": {
      "chat:qwen/qwen3.8-max-0902": model("low", "high"),
      "image:qwen/qwen-image-3": {},
    },
  }
  asar = tmp_path / "app.asar"
  asar.write_bytes(make_asar({CATALOG_PATH: json.dumps(catalog).encode()}))

  assert gen_dsh.load_catalog(asar) == {"qwen/qwen3.8-max-0902": model("low", "high")}


def test_dry_run_shows_only_allowlisted_values(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.patch.write_text(PATCH + EXISTING_PRESET + STALE_ROW, encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--dry-run") == 0
  out, err = capsys.readouterr()

  for secret in FAKE_SECRETS:
    assert secret not in out + err
  # every line is in the diff, it is the values that are hidden
  assert "apiKey: <redacted>" in out
  assert "Authorization: <redacted>" in out
  assert "args: [<redacted>, <redacted>]" in out
  assert "- <redacted>" in out
  assert "- {name: a, apiKey: <redacted>}" in out
  assert "extra: <redacted>  # <redacted>" in out
  assert "note: <redacted>\n" in out  # a hidden block scalar does not swallow its line break
  assert "baseURL: https://host.invalid:8000\n" in out  # scheme and host only: no userinfo, path, params, query
  assert "glued_seq: [<redacted>,# <redacted>" in out  # a comment glued to a flow indicator is a comment
  assert "glued_map: {k: <redacted>,# <redacted>" in out
  # the text of an allowlisted block scalar is shown, a comment on its header line is not
  assert "persona: <redacted>\n" in out
  assert "Persona line kept." not in out
  # allowlisted values and the generator's own marker stay readable
  assert "maxTokens: 555" in out
  assert "inline: {token: <redacted>, maxTokens: 777}" in out
  assert "maxTokens: 8192" in out  # an added line (the dspark route)
  assert "apiKeyEnv: LEGACY_API_KEY" in out  # an env var name is not a secret
  assert "apiKeyEnv: DSPARK_API_KEY" in out
  assert "Body of reviewer." not in out
  assert "agentPreset: cc-agent-reviewer" in out
  assert "prefix: <redacted>" in out
  assert "generated by ~/.claude/tools/gen_dsh.py" in out
  world.assert_untouched(before)


def test_duplicate_key_error_does_not_echo_the_values(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  twice = 'apiKey: "sk-FAKE-OLD-1111"\n        apiKey: "sk-FAKE-NEW-2222"'
  duplicated = LLM.replace('apiKey: "sk-or-FAKE-OPENROUTER-0001"', twice)
  assert duplicated != LLM
  world.patch.write_text(HEADER + UI_CHAT + duplicated + DEEPSEEK, encoding="utf-8")

  assert world.run("--dry-run") == 1

  out, err = capsys.readouterr()
  assert "DuplicateKeyError" in err
  for value in ("sk-FAKE-OLD-1111", "sk-FAKE-NEW-2222"):
    assert value not in out + err


def test_oserror_without_a_message_is_reported_not_raised(
  world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  def broken(self: Path) -> bytes:
    raise OSError()

  monkeypatch.setattr(Path, "read_bytes", broken)

  assert world.run("--dry-run") == 1

  err = capsys.readouterr().err
  assert "cannot read" in err
  assert "OSError" in err


def test_write_is_refused_while_the_app_runs_but_dry_run_is_not(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  before = world.patch.read_bytes()

  assert world.run("--write", app_running=lambda: True) == 1
  assert "DeepSeek Harness is running" in capsys.readouterr().err
  world.assert_untouched(before)

  assert world.run("--dry-run", app_running=lambda: True) == 0
  world.assert_untouched(before)


def test_app_started_while_the_plan_was_built_stops_the_write(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  checks = iter([False, True])  # not running at the start, running by the time of the write
  before = world.patch.read_bytes()

  assert world.run("--write", app_running=lambda: next(checks)) == 1

  assert "DeepSeek Harness is running" in capsys.readouterr().err
  world.assert_untouched(before)


def test_file_changed_after_planning_is_not_overwritten(
  world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  concurrent = PATCH + "# saved by the app while the plan was built\n"
  real_build_plan = gen_dsh.build_plan

  def racing(*args: Any) -> gen_dsh.Plan:
    plan = real_build_plan(*args)
    world.patch.write_text(concurrent, encoding="utf-8")
    return plan

  monkeypatch.setattr(gen_dsh, "build_plan", racing)

  assert world.run("--write") == 1

  assert "changed after it was read" in capsys.readouterr().err
  world.assert_untouched(concurrent.encode("utf-8"))  # no backup of a stale text either


def test_file_changed_just_before_the_replace_is_not_overwritten(
  world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  concurrent = PATCH + "# saved by the app just before the replace\n"
  real_mkstemp = tempfile.mkstemp

  def racing(*args: Any, **kwargs: Any) -> tuple[int, str]:
    world.patch.write_text(concurrent, encoding="utf-8")  # after the first check, before the last one
    return real_mkstemp(*args, **kwargs)

  monkeypatch.setattr(tempfile, "mkstemp", racing)

  assert world.run("--write") == 1

  assert "changed after it was read" in capsys.readouterr().err
  assert world.patch.read_text(encoding="utf-8") == concurrent
  assert list(world.root.glob("cordis.patch.yml.*.tmp")) == []  # the half-written copy is removed


def test_write_backs_up_the_old_file_with_mode_600_and_keeps_the_file_mode(world: World) -> None:
  world.patch.chmod(0o640)
  before = world.patch.read_bytes()

  assert world.run("--write") == 0

  [backup] = world.backups()
  assert backup.read_bytes() == before
  assert stat.S_IMODE(backup.stat().st_mode) == 0o600
  assert stat.S_IMODE(world.patch.stat().st_mode) == 0o640
  assert world.patch.read_bytes() != before


def test_hooks_row_is_added_once_as_an_insert_entry_and_a_rerun_changes_nothing(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  assert world.run("--write") == 0
  out = capsys.readouterr().out
  text = world.patch.read_text(encoding="utf-8")

  assert re.search(r"hooks-claude-code +add", out)
  assert hooks_rows(text) == [{
    "id": "hooks-claude-code", "name": "@deepseek-ai/dsh-hooks-claude-code",
    "config": {"configPath": str(world.hooks_config)},
  }]
  # a plain entry with that id would patch a row no bundle declares: the loader skips it, the hooks never mount
  assert [e for e in parse(text) if e.get("id") == "hooks-claude-code"] == []
  assert "hooks-claude-code" not in preset_rows(text)  # and it is no child of one preset either
  first = world.patch.read_bytes()

  assert world.run("--write") == 0
  out = capsys.readouterr().out
  assert "Patch file is already up to date" in out
  assert re.search(r"hooks-claude-code +unchanged", out)
  assert world.patch.read_bytes() == first


def test_existing_hooks_entry_gets_the_current_path_and_keeps_the_users_keys_and_overrides(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.patch.write_text(PATCH + HOOKS_INSERT + HOOKS_OVERRIDE, encoding="utf-8")

  assert world.run("--write") == 0

  text = world.patch.read_text(encoding="utf-8")
  assert re.search(r"hooks-claude-code +update", capsys.readouterr().out)
  assert hooks_rows(text) == [{
    "id": "hooks-claude-code", "name": "@deepseek-ai/dsh-hooks-claude-code",
    "config": {"configPath": str(world.hooks_config), "defaultTimeoutMs": 5000},
  }]
  assert HOOKS_OVERRIDE in text  # the plugin manager's switch-off stays, and is not taken for the tool's entry
  assert text.count("id: hooks-claude-code") == 2  # the row and the override: no second row was added


def test_missing_hooks_config_is_a_warning_and_the_row_is_still_written(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.hooks_config.unlink()

  assert world.run("--write") == 0

  assert f"hooks-claude-code: {world.hooks_config} does not exist" in warnings_of(capsys.readouterr().out)
  assert len(hooks_rows(world.patch.read_text(encoding="utf-8"))) == 1


def test_a_row_with_the_hooks_id_that_is_another_plugin_is_refused(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  other = HOOKS_INSERT.replace("dsh-hooks-claude-code", "dsh-hooks-codex")
  world.patch.write_text(PATCH + other, encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "refusing to take it over" in capsys.readouterr().err
  assert world.patch.read_bytes() == before
  assert not world.farm.exists() and not world.agents_md.exists()


def test_farm_holds_one_link_per_skill_dir_and_local_skills_win(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  real = world.root / "real-skills"  # a skill dir that is itself a symlink in ~/.claude/skills, like cad
  world.add_skill(real, "linked")
  os.symlink(real / "linked", world.claude_skills / "linked")

  assert world.run("--dry-run") == 0
  assert not world.farm.exists()  # a dry run writes nothing
  out = capsys.readouterr().out
  assert f"Skills farm {world.farm}: add 6, update 0, remove 0, unchanged 0" in out
  assert "task-orchestration" in out

  assert world.run("--write") == 0

  assert world.links() == {  # excluded names, a dir without SKILL.md and a plain file are all absent
    "alpha": str(world.claude_skills / "alpha"),
    "beta": str(world.claude_skills / "beta"),
    "linked": str(world.claude_skills / "linked"),  # the path in the skills dir, not the target behind it
    "code-tier-assessment": str(world.local_skills / "code-tier-assessment"),
    "local-only": str(world.local_skills / "local-only"),
    "task-orchestration": str(world.local_skills / "task-orchestration"),  # local beats the excluded one
  }
  assert sorted(p.name for p in world.farm.iterdir()) == sorted(world.links())
  assert (world.farm / "linked" / "SKILL.md").is_file()  # and it resolves through both links
  capsys.readouterr()
  before = world.links()

  assert world.run("--write") == 0

  assert "Skills farm is already up to date" in capsys.readouterr().out
  assert world.links() == before


def test_a_local_skill_beats_a_skill_of_the_same_name_in_the_claude_dir(world: World) -> None:
  assert world.run("--write") == 0
  assert os.readlink(world.farm / "beta") == str(world.claude_skills / "beta")

  world.add_skill(world.local_skills, "beta")
  assert world.run("--write") == 0

  assert os.readlink(world.farm / "beta") == str(world.local_skills / "beta")  # the link was re-pointed

  shutil.rmtree(world.local_skills / "beta")
  assert world.run("--write") == 0

  assert os.readlink(world.farm / "beta") == str(world.claude_skills / "beta")  # and back


def test_farm_removes_stale_links_it_made_and_leaves_everything_else_with_a_warning(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.farm.mkdir(parents=True)
  os.symlink(world.claude_skills / "gone", world.farm / "gone")  # its skill dir was deleted
  os.symlink(world.claude_skills / "magi", world.farm / "magi")  # a skill that is excluded now
  os.symlink(world.local_skills / "retired", world.farm / "retired")
  (world.farm / "notes.txt").write_text("mine\n", encoding="utf-8")
  (world.farm / "mine").mkdir()
  (world.farm / "mine/keep.md").write_text("mine too\n", encoding="utf-8")
  os.symlink(world.root / "outside", world.farm / "elsewhere")  # a link to somewhere else
  os.symlink("../../.claude/skills/alpha", world.farm / "relative")  # not absolute: this tool never writes one

  assert world.run("--write") == 0

  warnings = warnings_of(capsys.readouterr().out)
  assert world.links() == {
    "alpha": str(world.claude_skills / "alpha"),
    "beta": str(world.claude_skills / "beta"),
    "code-tier-assessment": str(world.local_skills / "code-tier-assessment"),
    "elsewhere": str(world.root / "outside"),
    "local-only": str(world.local_skills / "local-only"),
    "relative": "../../.claude/skills/alpha",
    "task-orchestration": str(world.local_skills / "task-orchestration"),
  }  # gone, magi and retired are removed, the two foreign links stay as they were
  assert (world.farm / "notes.txt").read_text(encoding="utf-8") == "mine\n"
  assert (world.farm / "mine/keep.md").read_text(encoding="utf-8") == "mine too\n"
  for name in ("notes.txt", "mine", "elsewhere", "relative"):
    assert f"'{name}' is not a link this tool made; left as it is" in warnings
  for name in ("gone", "magi", "retired"):
    assert f"'{name}'" not in warnings  # a stale link of the tool's own is removed without a fuss


def test_farm_does_not_replace_a_foreign_entry_that_is_in_the_way_of_a_skill(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.farm.mkdir(parents=True)
  (world.farm / "alpha").write_text("mine\n", encoding="utf-8")
  os.symlink(world.root / "outside", world.farm / "beta")

  assert world.run("--write") == 0

  warnings = warnings_of(capsys.readouterr().out)
  assert (world.farm / "alpha").read_text(encoding="utf-8") == "mine\n"
  assert not (world.farm / "alpha").is_symlink()
  assert os.readlink(world.farm / "beta") == str(world.root / "outside")
  for name in ("alpha", "beta"):
    assert f"'{name}' is not a link this tool made; left as it is, so that skill is not linked" in warnings
  assert set(world.links()) == {"beta", "code-tier-assessment", "local-only", "task-orchestration"}


def test_farm_link_whose_skill_dir_is_gone_is_removed(world: World) -> None:
  assert world.run("--write") == 0
  shutil.rmtree(world.claude_skills / "alpha")

  assert world.run("--write") == 0

  assert not os.path.lexists(world.farm / "alpha")  # a dangling link is no leftover either


def test_a_link_that_turns_into_a_file_after_planning_is_not_removed(
  world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  world.farm.mkdir(parents=True)
  os.symlink(world.claude_skills / "gone", world.farm / "gone")
  real_build_plan = gen_dsh.build_plan

  def racing(*args: Any) -> gen_dsh.Plan:
    plan = real_build_plan(*args)
    (world.farm / "gone").unlink()  # the planned removal is of a link; somebody puts a file there instead
    (world.farm / "gone").write_text("mine\n", encoding="utf-8")
    return plan

  monkeypatch.setattr(gen_dsh, "build_plan", racing)

  assert world.run("--write") == 1

  assert f"{world.farm / 'gone'} changed after it was read" in capsys.readouterr().err
  assert (world.farm / "gone").read_text(encoding="utf-8") == "mine\n"


@pytest.mark.parametrize("missing", ["claude_skills", "local_skills"])
def test_missing_skills_dir_is_an_error_and_nothing_is_written(
  world: World, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
  shutil.rmtree(getattr(world, missing))
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "cannot read skills dir" in capsys.readouterr().err
  world.assert_untouched(before)


def test_a_farm_only_change_still_waits_for_the_app_to_be_stopped(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  assert world.run("--write") == 0  # patch file, instructions and farm are current now
  world.add_skill(world.claude_skills, "gamma")
  checks = iter([False, True])  # stopped at the start, started by the time of the write
  capsys.readouterr()
  before = world.links()

  assert world.run("--write", app_running=lambda: next(checks)) == 1

  assert "DeepSeek Harness is running" in capsys.readouterr().err
  assert world.links() == before


def test_instructions_file_is_claude_md_with_the_imports_expanded_and_mode_600(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  assert world.run("--write") == 0
  capsys.readouterr()

  assert world.agents_md.read_text(encoding="utf-8") == AGENTS_MD
  assert stat.S_IMODE(world.agents_md.stat().st_mode) == 0o600
  assert list(world.agents_md.parent.glob("AGENTS.md.*")) == []  # a new file: nothing to back up

  assert world.run("--write") == 0

  assert "Instructions file is already up to date" in capsys.readouterr().out
  assert world.agents_md.read_text(encoding="utf-8") == AGENTS_MD
  assert list(world.agents_md.parent.glob("AGENTS.md.*")) == []


def test_imports_are_one_level_and_a_relative_path_starts_at_the_claude_md_dir(world: World) -> None:
  (world.claude_md.parent / "sub").mkdir()
  (world.claude_md.parent / "sub/part.md").write_text("part\n@nested.md\nno final newline", encoding="utf-8")
  world.claude_md.write_text("A\n@sub/part.md\nB\n", encoding="utf-8")

  assert world.run("--write") == 0

  # the nested import stays as written, and the line after an import does not stick to its last line
  assert world.agents_md.read_text(encoding="utf-8") == AGENTS_HEADER + "A\npart\n@nested.md\nno final newline\nB\n"


def test_missing_import_is_an_error_and_nothing_is_written(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.claude_md.write_text("x\n@~/.claude/missing.md\n", encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  err = capsys.readouterr().err
  assert "cannot read import '@~/.claude/missing.md'" in err
  assert str(world.root / ".claude/missing.md") in err  # the `~` was expanded
  world.assert_untouched(before)


def test_dry_run_names_the_instructions_file_but_never_prints_its_content(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  size = len(AGENTS_MD.encode("utf-8"))

  assert world.run("--dry-run") == 0

  out, err = capsys.readouterr()
  assert f"Instructions file {world.agents_md}: add, {size} bytes" in out
  for text in ("FAKE-PERSONA-LINE", "Reply in Russian", "Mention @notes.md", "Last line."):
    assert text not in out + err
  assert not world.agents_md.exists()

  assert world.run("--write") == 0
  capsys.readouterr()
  assert world.run("--dry-run") == 0
  out, err = capsys.readouterr()
  assert f"Instructions file {world.agents_md}: unchanged, {size} bytes" in out
  assert "FAKE-PERSONA-LINE" not in out + err


def test_changed_instructions_file_is_backed_up_with_mode_600_and_replaced_with_mode_600(
  world: World,
) -> None:
  world.agents_md.write_text("old text\n", encoding="utf-8")
  world.agents_md.chmod(0o644)

  assert world.run("--write") == 0

  [backup] = world.agents_md.parent.glob("AGENTS.md.bak-*")
  assert backup.read_text(encoding="utf-8") == "old text\n"
  assert stat.S_IMODE(backup.stat().st_mode) == 0o600
  assert world.agents_md.read_text(encoding="utf-8") == AGENTS_MD
  assert stat.S_IMODE(world.agents_md.stat().st_mode) == 0o600


def test_instructions_file_with_the_right_text_but_a_wider_mode_is_rewritten(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  world.agents_md.write_text(AGENTS_MD, encoding="utf-8")
  world.agents_md.chmod(0o644)

  assert world.run("--dry-run") == 0
  assert f"Instructions file {world.agents_md}: update," in capsys.readouterr().out

  assert world.run("--write") == 0

  assert world.agents_md.read_text(encoding="utf-8") == AGENTS_MD
  assert stat.S_IMODE(world.agents_md.stat().st_mode) == 0o600


def test_instructions_file_changed_after_planning_is_not_overwritten(
  world: World, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  assert world.run("--write") == 0  # everything is current now
  world.claude_md.write_text(CLAUDE_MD + "One more line.\n", encoding="utf-8")  # the file has to change
  real_build_plan = gen_dsh.build_plan

  def racing(*args: Any) -> gen_dsh.Plan:
    plan = real_build_plan(*args)
    world.agents_md.write_text("saved by somebody else\n", encoding="utf-8")
    return plan

  monkeypatch.setattr(gen_dsh, "build_plan", racing)
  capsys.readouterr()

  assert world.run("--write") == 1

  assert "changed after it was read" in capsys.readouterr().err
  assert world.agents_md.read_text(encoding="utf-8") == "saved by somebody else\n"
  assert list(world.agents_md.parent.glob("AGENTS.md.*")) == []  # no backup of a stale text, no temp file


@pytest.mark.parametrize("name", AGENTS)
def test_every_spawn_uses_a_registered_preset_with_exact_tools(world: World, name: str) -> None:
  assert world.run("--write") == 0
  text = world.patch.read_text(encoding="utf-8")
  child = agent_rows(text)[f"cc-agent-{name}"]["config"]
  assert set(child) == {"provider", "toolName", "agentPreset", "agentOptions", "backgroundMode"}
  assert child["provider"] == "spawn"
  assert child["toolName"] == name
  assert child["backgroundMode"] == "continuable"
  assert child["agentPreset"] == f"cc-agent-{name}"
  preset = role_presets(text)[child["agentPreset"]]
  assert preset["id"] == f"preset-cc-agent-{name}"
  expected = {
    "reviewer": {"read", "bash"},
    "web-researcher": {"web_search", "web_fetch", "read"},
    "code-writer-t1": {"read", "grep", "glob", "edit", "write", "bash", "skill"},
  }.get(name, {"read", "grep", "glob"})
  assert role_tools(world, name) == expected
  plugins = role_plugins(world, name)
  assert plugins["agent-instructions"]["config"] == {"maxBytes": 65536}
  assert role_persona(world, name).endswith(f"\n\nBody of {name}.\n")
  if "skill" in expected:
    assert plugins["skill-filesystem"]["config"] == {"customSkillDirs": [str(world.farm)]}
  else:
    assert "skill-filesystem" not in plugins


@pytest.mark.parametrize("tools,expected", [
  ("Read", {"read"}), ("Write", {"write"}), ("Edit", {"edit"}),
  ("WebSearch", {"web_search"}), ("WebFetch", {"web_fetch"}), ("Skill", {"skill"}),
])
def test_individual_tool_switches_do_not_register_sibling_tools(
  world: World, tools: str, expected: set[str]
) -> None:
  world.set_tools("reviewer", tools)

  assert world.run("--write") == 0

  assert role_tools(world, "reviewer") == expected


@pytest.mark.parametrize("tools", ["Read, Grep", "Read, Glob"])
def test_unrepresentable_search_subset_is_refused_before_writing(
  world: World, capsys: pytest.CaptureFixture[str], tools: str
) -> None:
  world.set_tools("reviewer", tools)
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "grep and glob must be enabled together" in capsys.readouterr().err
  world.assert_untouched(before)


@pytest.mark.parametrize("row", [
  '- insert:\n    - id: preset-cc-agent-reviewer\n      name: "@deepseek-ai/dsh-agent-preset"\n'
  '      config: {id: cc-agent-reviewer, plugins: []}\n',
  '- insert:\n    - id: custom-reviewer\n      name: "@deepseek-ai/dsh-agent-preset"\n'
  '      config: {id: cc-agent-reviewer, plugins: []}\n',
  '- id: preset-cc-agent-reviewer\n  disabled: true\n',
])
def test_unowned_role_preset_collision_is_refused_without_writing(
  world: World, capsys: pytest.CaptureFixture[str], row: str
) -> None:
  world.patch.write_text(PATCH + row, encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "refusing to replace an unowned role preset" in capsys.readouterr().err
  world.assert_untouched(before)


def test_role_insert_with_an_unowned_sibling_is_refused_without_writing(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  assert world.run("--write") == 0
  text = world.patch.read_text(encoding="utf-8")
  entries = parse(text)
  _, chunks, _ = gen_dsh.split_entries(text)
  index = next(
    i for i, root in enumerate(entries)
    if any(
      row.get("name") == "@deepseek-ai/dsh-agent-preset"
      and row.get("config", {}).get("id") == "cc-agent-reviewer"
      for row in root.get("insert", [])
    )
  )
  body, gap = gen_dsh.split_gap(chunks[index])
  chunks[index] = body + '    - id: user-tool\n      name: "@deepseek-ai/dsh-tool-todo"\n' + gap
  header, _, _ = gen_dsh.split_entries(text)
  world.patch.write_text(header + "".join(chunks), encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "refusing to replace an unowned role preset" in capsys.readouterr().err
  assert world.patch.read_bytes() == before


def test_unrelated_insert_is_preserved_byte_for_byte(world: World) -> None:
  foreign = '- insert:\n    - id: user-preset\n      name: "@deepseek-ai/dsh-agent-preset"\n'
  foreign += '      config: {id: user, plugins: [{name: \"@deepseek-ai/dsh-tool-working-directory\"}]}  # keep my formatting\n'
  world.patch.write_text(PATCH + foreign, encoding="utf-8")

  assert world.run("--write") == 0

  assert foreign in world.patch.read_text(encoding="utf-8")


def test_dry_run_lists_registered_role_presets_and_never_creates_a_preset_directory(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  before = world.patch.read_bytes()

  assert world.run("--dry-run") == 0

  out = capsys.readouterr().out
  for name in AGENTS:
    assert re.search(rf"preset-cc-agent-{name} +add", out)
    assert f"agentPreset: cc-agent-{name}" in out
  assert not (world.root / ".dsh/.agent-presets").exists()
  world.assert_untouched(before)


@pytest.mark.parametrize("name", ["standard", "ptc", "minimal", "cordis"])
def test_working_directory_moves_from_global_into_every_shipped_non_role_preset(
  world: World, name: str
) -> None:
  assert world.run("--write") == 0
  text = world.patch.read_text(encoding="utf-8")
  assert entry(text, "tool-working-directory") == {"id": "tool-working-directory", "disabled": True}
  preset = entry(text, f"preset-{name}")
  plugins = preset["config"]["plugins"]
  assert plugins[-1] == {
    "id": "cc-inherited-working-directory", "name": "@deepseek-ai/dsh-tool-working-directory",
  }
  if name != "standard":
    original = parse(OTHER_PRESETS[name])[0]["insert"][0]
    assert plugins[:-1] == original["config"]["plugins"]
    assert preset["config"]["order"] == original["config"]["order"]
  for role in AGENTS:
    assert "working_directory" not in role_tools(world, role)


def test_user_preset_and_its_later_config_override_keep_their_other_fields(world: World) -> None:
  custom = (
    '- insert:\n    - id: preset-user\n      name: "@deepseek-ai/dsh-agent-preset"\n'
    '      config: {id: user, plugins: []}\n'
    '- id: preset-user\n  config:\n    id: user\n    name: My preset\n    plugins:\n'
    '      - id: custom-tool\n        name: "@deepseek-ai/dsh-tool-web"\n        config: {search: false, fetch: true}\n'
  )
  world.patch.write_text(PATCH + custom, encoding="utf-8")
  assert world.run("--write") == 0
  text = world.patch.read_text(encoding="utf-8")
  preset = entry(text, "preset-user")
  assert preset["config"]["name"] == "My preset"
  assert preset["config"]["plugins"] == [
    {"id": "custom-tool", "name": "@deepseek-ai/dsh-tool-web", "config": {"search": False, "fetch": True}},
    {"id": "cc-inherited-working-directory", "name": "@deepseek-ai/dsh-tool-working-directory"},
  ]


def test_insert_with_user_preset_and_unrelated_sibling_preserves_sibling(world: World) -> None:
  custom = (
    '- insert:\n    - id: preset-user\n      name: "@deepseek-ai/dsh-agent-preset"\n'
    '      config: {id: user, plugins: []}\n'
    '    - id: user-host\n      name: "@deepseek-ai/dsh-time-context"\n      disabled: true\n'
  )
  world.patch.write_text(PATCH + custom, encoding="utf-8")
  assert world.run("--write") == 0
  inserted = [e["insert"] for e in parse(world.patch.read_text(encoding="utf-8")) if "insert" in e]
  [rows] = [rows for rows in inserted if rows[0].get("id") == "preset-user"]
  assert rows[1] == parse(custom)[0]["insert"][1]
  assert rows[0]["config"]["plugins"] == [
    {"id": "cc-inherited-working-directory", "name": "@deepseek-ai/dsh-tool-working-directory"},
  ]


def test_user_disabled_global_working_directory_does_not_grant_it_to_any_preset(world: World) -> None:
  disabled = '- id: tool-working-directory\n  disabled: true  # explicit user choice\n'
  world.patch.write_text(PATCH + disabled, encoding="utf-8")
  assert world.run("--write") == 0
  text = world.patch.read_text(encoding="utf-8")
  assert disabled in text
  assert "cc-inherited-working-directory" not in text
  assert "preset-ptc" not in text


def test_working_directory_migration_keeps_existing_user_preset_tools(world: World) -> None:
  existing = (
    '- id: preset-minimal\n  config:\n    id: minimal\n    plugins:\n'
    '      - id: custom-editor\n        name: "@deepseek-ai/dsh-tool-fs"\n'
    '        config: {read: true, write: false, edit: false, readImage: false}\n'
  )
  world.patch.write_text(PATCH + existing, encoding="utf-8")
  assert world.run("--write") == 0
  preset = entry(world.patch.read_text(encoding="utf-8"), "preset-minimal")
  assert preset["config"]["plugins"][:-1] == entry(existing, "preset-minimal")["config"]["plugins"]


@pytest.mark.parametrize("row", [
  '- id: tool-working-directory\n  disabled: !!js process.platform === "win32"\n',
  '- id: tool-working-directory\n  config: {custom: value}\n',
])
def test_unknown_global_working_directory_edits_stop_before_writing(
  world: World, capsys: pytest.CaptureFixture[str], row: str
) -> None:
  world.patch.write_text(PATCH + row, encoding="utf-8")
  before = world.patch.read_bytes()
  assert world.run("--write") == 1
  assert "global working-directory row" in capsys.readouterr().err
  world.assert_untouched(before)


@pytest.mark.parametrize("extra", [
  '- id: tool-fs\n  disabled: false\n',
  '- insert:\n    - id: arbitrary-user-row\n      name: "@deepseek-ai/dsh-tool-todo"\n',
  '- insert:\n    - id: host-group\n      name: cordis:group\n      group: true\n      config:\n'
  '        - id: arbitrary-child\n          name: "@deepseek-ai/dsh-tool-fs"\n',
  '- insert:\n    - id: host-group\n      name: cordis:group\n      group: true\n      config:\n'
  '        - id: arbitrary-child\n          name: "@deepseek-ai/dsh-tool-web"\n          disabled: true\n'
  '- id: arbitrary-child\n  disabled: false\n',
  '- insert:\n    - id: arbitrary-conditional\n      name: "@deepseek-ai/dsh-tool-fs"\n'
  '      disabled: !!js process.platform === "win32"\n',
  '- insert:\n    - id: creator-tools\n      name: "@deepseek-ai/dsh-tool-cordis"\n',
])
def test_effective_global_model_tools_are_refused_before_writing(
  world: World, capsys: pytest.CaptureFixture[str], extra: str
) -> None:
  world.patch.write_text(PATCH + extra, encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "global model-facing tools would be inherited" in capsys.readouterr().err
  world.assert_untouched(before)


def test_effective_global_mcp_id_only_reenable_is_refused(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  base = MCP_INSERTED + '      disabled: true\n'
  world.set_catalog(CATALOG, base_extra=base)
  world.patch.write_text(PATCH + '- id: mcp-trino\n  disabled: false\n', encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "global MCP tools would be inherited" in capsys.readouterr().err
  world.assert_untouched(before)


@pytest.mark.parametrize("name", ["@deepseek-ai/dsh-tool-fs", "@deepseek-ai/dsh-mcp-client"])
def test_effective_global_disabled_override_does_not_grant_tools(world: World, name: str) -> None:
  extra = (
    '- insert:\n    - id: temporary-host-tool\n      name: "' + name + '"\n'
    '- id: temporary-host-tool\n  disabled: true\n'
  )
  world.patch.write_text(PATCH + extra, encoding="utf-8")

  assert world.run("--write") == 0

  assert extra in world.patch.read_text(encoding="utf-8")


@pytest.mark.parametrize("disabled", [True, False])
def test_effective_global_group_switch_controls_tool_inheritance(
  world: World, capsys: pytest.CaptureFixture[str], disabled: bool
) -> None:
  extra = (
    '- insert:\n    - id: optional-host-tools\n      name: cordis:group\n      group: true\n'
    '      config:\n        - id: optional-host-editor\n          name: "@deepseek-ai/dsh-tool-fs"\n'
    '- id: optional-host-tools\n  disabled: ' + str(disabled).lower() + '\n'
  )
  world.patch.write_text(PATCH + extra, encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == (0 if disabled else 1)

  if disabled:
    assert extra in world.patch.read_text(encoding="utf-8")
  else:
    assert "global model-facing tools would be inherited" in capsys.readouterr().err
    world.assert_untouched(before)


@pytest.mark.parametrize("name", [
  "@deepseek-ai/dsh-tool-call-timeout-policy", "@deepseek-ai/dsh-tool-cordis/host",
  "@deepseek-ai/dsh-tool-subagent/model-selection-settings",
  "@deepseek-ai/dsh-tools", "@deepseek-ai/dsh-scope", "@deepseek-ai/dsh-agent-tool-presentation",
])
def test_global_service_plugins_are_preserved_without_being_treated_as_tools(world: World, name: str) -> None:
  extra = '- insert:\n    - id: user-service\n      name: "' + name + '"\n'
  world.patch.write_text(PATCH + extra, encoding="utf-8")

  assert world.run("--write") == 0

  assert extra in world.patch.read_text(encoding="utf-8")


def test_model_tools_inside_a_root_agent_preset_are_not_global(world: World) -> None:
  extra = (
    '- insert:\n    - id: preset-only-editor\n      name: "@deepseek-ai/dsh-agent-preset"\n'
    '      config:\n        id: only-editor\n        plugins:\n'
    '          - id: local-editor\n            name: "@deepseek-ai/dsh-tool-fs"\n'
  )
  world.patch.write_text(PATCH + extra, encoding="utf-8")

  assert world.run("--write") == 0

  rows = [r for e in parse(world.patch.read_text(encoding="utf-8")) for r in e.get("insert", [])]
  [preset] = [r for r in rows if r.get("id") == "preset-only-editor"]
  assert preset["config"]["plugins"][0] == parse(extra)[0]["insert"][0]["config"]["plugins"][0]


@pytest.mark.parametrize("disabled", [False, True])
def test_working_directory_migration_refuses_active_presets_inside_groups(
  world: World, capsys: pytest.CaptureFixture[str], disabled: bool
) -> None:
  extra = (
    '- insert:\n    - id: preset-group\n      name: cordis:group\n      group: true\n'
    '      disabled: ' + str(disabled).lower() + '\n'
    '      config:\n        - id: preset-nested-user\n          name: "@deepseek-ai/dsh-agent-preset"\n'
    '          config: {id: nested-user, plugins: []}\n'
  )
  world.patch.write_text(PATCH + extra, encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == (0 if disabled else 1)

  if disabled:
    assert extra in world.patch.read_text(encoding="utf-8")
  else:
    assert "active nested Agent Preset" in capsys.readouterr().err
    world.assert_untouched(before)


def test_working_directory_migration_refuses_a_nested_preset_reenabled_by_group_override(
  world: World, capsys: pytest.CaptureFixture[str]
) -> None:
  extra = (
    '- insert:\n    - id: preset-group\n      name: cordis:group\n      group: true\n      disabled: true\n'
    '      config:\n        - id: preset-nested-user\n          name: "@deepseek-ai/dsh-agent-preset"\n'
    '          config: {id: nested-user, plugins: []}\n'
    '- id: preset-group\n  disabled: false\n'
  )
  world.patch.write_text(PATCH + extra, encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == 1

  assert "active nested Agent Preset" in capsys.readouterr().err
  world.assert_untouched(before)


@pytest.mark.parametrize("disabled", [True, False])
def test_group_targeted_insert_checks_nested_presets_with_effective_group_switch(
  world: World, capsys: pytest.CaptureFixture[str], disabled: bool
) -> None:
  extra = (
    '- insert:\n    - id: preset-target-group\n      name: cordis:group\n      group: true\n'
    '      config: []\n'
    '- id: preset-target-group\n  insert:\n    - id: preset-target-nested\n'
    '      name: "@deepseek-ai/dsh-agent-preset"\n      config: {id: target-nested, plugins: []}\n'
    '- id: preset-target-group\n  disabled: ' + str(disabled).lower() + '\n'
  )
  world.patch.write_text(PATCH + extra, encoding="utf-8")
  before = world.patch.read_bytes()

  assert world.run("--write") == (0 if disabled else 1)

  if disabled:
    assert extra in world.patch.read_text(encoding="utf-8")
  else:
    assert "active nested Agent Preset" in capsys.readouterr().err
    world.assert_untouched(before)
