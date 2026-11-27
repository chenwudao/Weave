"""Exercise the new-project questions through the pinned local Web runtime."""
import http.server
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import native_launcher as launcher
from assistant_profile import assistant_permission


def main():
    class Model(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != "/state":
                self.send_error(404)
                return
            from contextlib import closing
            with closing(launcher.workspace.database()) as db:
                installed = db.execute("SELECT 1 FROM sqlite_master WHERE name='labweave_bot_dispatches'").fetchone()
                jobs = db.execute("SELECT count(*) FROM labweave_bot_dispatches").fetchone()[0] if installed else 0
            body = json.dumps({"jobs": jobs}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            messages = request["messages"]
            user_text = "\n".join(str(message.get("content", "")) for message in messages if message["role"] == "user")
            system = "\n".join(str(message.get("content", "")) for message in messages if message["role"] == "system")
            tools = {tool["function"]["name"] for tool in request.get("tools", [])}
            outputs = {message["tool_call_id"]: message["content"] for message in messages if message["role"] == "tool"}
            command = None
            answer = "项目已就绪。"
            if "WEAVE_PROJECT_INTENT" in system:
                intake = json.loads(user_text)
                latest = intake["latest"]
                decision = {"mode": "MANAGE", "research": False, "workflow": "within_scope", "missing": []}
                if latest == "训练数据和测试数据怎么分？":
                    decision.update(reply="example", research=True)
                if "daquan zhou" in latest:
                    decision["creation"] = {"kind": "named_only", "name": "daquan zhou 最新科研追踪"}
                if latest == "继续":
                    assert any("要我解释为什么同一段演示只能放进一组吗？" in entry["text"] for entry in intake["recent"])
                if "帮我选一个方向并开始" in latest and not intake["project"].get("name"):
                    decision["creation"] = {"kind": "start", "name": "Agent 评测",
                                            "authorization": "帮我选一个方向并开始"}
                if intake["project"].get("name") and "探索性文献摸底" in latest:
                    decision.update(mode="EXECUTE", research=True)
                answer = json.dumps(decision, ensure_ascii=False)
            elif "训练数据和测试数据怎么分？" in user_text:
                if any("要我解释为什么同一段演示只能放进一组吗？" in str(m.get("content"))
                       for m in messages if m["role"] == "assistant"):
                    answer = "如果同一段演示同时出现在两组里，测试时就可能遇到训练时见过的画面。"
                else:
                    assert "# 先给示例" in system + user_text
                    assert "# Weave 科研项目闭环" not in system + user_text
                    answer = "例如，把九段演示用于训练，另留三段用于测试。\n\n要我解释为什么同一段演示只能放进一组吗？"
            elif "structured_output" in tools:
                assert "探索性文献摸底" in user_text and "真实实验" not in user_text
                command = ("deliver", "structured_output", {
                    "title": "文献摸底完成", "answer": "已交付浏览器验收摘要。", "outcome": "completed",
                })
            elif "labweave_project_create" in tools:
                if "create" not in outputs:
                    command = ("create", "labweave_project_create", {"name": "Agent 评测"})
                elif "daquan zhou" in user_text:
                    assert json.loads(outputs["create"])["name"] == "daquan zhou 最新科研追踪"
                elif "handoff" not in outputs:
                    project = json.loads(outputs["create"])
                    # Try a handoff even after a skipped card. The host must enforce the answer.
                    task = ("围绕 Agent 记忆做一轮探索性文献摸底。"
                            if "帮我选一个方向并开始" in user_text else "越过范围，启动真实实验")
                    command = ("handoff", "labweave_project_handoff", {"projectId": project["id"], "task": task})
            elif "labweave_bot_delegate" in tools and "探索性文献摸底" in user_text:
                assert "真实实验" not in user_text
                if "create-bot" not in outputs:
                    command = ("create-bot", "labweave_feature_create", {"label": "文献核对", "paradigm": "notes"})
                elif "dispatch" not in outputs:
                    bot = json.loads(outputs["create-bot"])["feature"]
                    command = ("dispatch", "labweave_bot_delegate", {"assignments": [{
                        "featureId": bot["id"], "task": "对 Agent 记忆做探索性文献摸底", "saveResult": False,
                    }]})
                else:
                    answer = "文献摸底完成"
            if command:
                identity, name, args = command
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": identity, "type": "function",
                         "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
            else:
                delta = {"role": "assistant", "content": answer}

            def chunk(content, finish):
                return "data: " + json.dumps({"id": "project-setup-fixture", "object": "chat.completion.chunk",
                    "created": 1, "model": request["model"],
                    "choices": [{"index": 0, "delta": content, "finish_reason": finish}]}) + "\n\n"

            response = (chunk(delta, None) + chunk({}, "tool_calls" if command else "stop") + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    with TemporaryDirectory(prefix="weave-project-setup-", ignore_cleanup_errors=True) as directory, \
            http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model) as server:
        root = Path(directory)
        previous = launcher.workspace.DATA
        launcher.workspace.DATA = root / "data"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            assistant_permission({"preset": "danger-full-access"})
            home = root / "web"
            profile = home / "profiles/web"
            profile.mkdir(parents=True)
            (home / ".credentials.yaml").write_text(
                "version: 1\nrefs:\n  CUSTOMGW_API_KEY: sk-local-project-setup-fixture\n", encoding="utf-8")
            model_url = f"http://127.0.0.1:{server.server_port}"
            (profile / "cordis.patch.yml").write_text(f"""- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      customgw:
        apiKeyEnv: CUSTOMGW_API_KEY
        api: openai-completions
        baseURL: "{model_url}/v1"
        models:
          - id: gpt-fixture-pro
            contextWindow: 128000
            maxTokens: 4096
- id: agent-default-model
  name: "@deepseek-ai/dsh-agent-default-model"
  config:
    provider: customgw
    model: gpt-fixture-pro
""", encoding="utf-8")
            with launcher.native_services(0, home=home, configured=False, background=False) as (url, _):
                subprocess.run(["node", str(ROOT / "tests/integration_project_setup.cjs")],
                               input=json.dumps({"url": url, "stateUrl": model_url + "/state"}),
                               text=True, cwd=ROOT, check=True, timeout=260)
        finally:
            server.shutdown()
            launcher.workspace.DATA = previous


if __name__ == "__main__":
    main()
