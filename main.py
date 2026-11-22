#!/usr/bin/env python3
"""Copy Path plugin for Block (blockmd)."""

import json
import os
import re
import sys


STORAGE_KEY = "format"
SETTINGS_PAGE_SURFACE = "settings_page"
FORMATS = ("absolute", "relative", "name")
DEFAULT_FORMAT = "absolute"
COPY_COMMANDS = {"copy_path"}
CHINESE_TEXT = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")


class HostRPCError(RuntimeError):
    """JSON-RPC error returned by the Block host."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class HostBridge:
    """Line-delimited JSON-RPC 2.0 bridge between plugin and Block host."""

    def __init__(self):
        self._seq = 1000

    def send(self, obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def call_host(self, method, params=None):
        self._seq += 1
        req_id = f"p-{self._seq}"
        self.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        while True:
            line = sys.stdin.readline()
            if not line:
                raise RuntimeError("stdin closed while awaiting host response")
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if str(msg.get("id")) == req_id:
                if msg.get("error"):
                    err = msg["error"]
                    raise HostRPCError(int(err.get("code", -32000)), str(err.get("message", "host error")))
                return msg.get("result", {})

    def serve(self, handler, localize_error):
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            method = req.get("method")
            req_id = req.get("id")
            try:
                result = handler(self, method, req.get("params") or {})
                self.send({"jsonrpc": "2.0", "id": req_id, "result": result})
                if method == "plugin.shutdown":
                    break
            except Exception as exc:
                self.send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": localize_error(str(exc))},
                })


class CopyPathPlugin:
    """Copies the target file path in the chosen format."""

    def __init__(self):
        self.locale = "zh"
        self.format = DEFAULT_FORMAT
        # Last copy target, used to make the settings preview concrete.
        self.last_file = ""
        self.last_rel = ""

    def _is_en(self):
        return str(self.locale or "").lower().startswith("en")

    def _tr(self, zh, en):
        return en if self._is_en() else zh

    def _localize_error(self, message):
        text = str(message or "").strip()
        if self._is_en() and CHINESE_TEXT.search(text):
            return "Operation failed"
        return text or self._tr("操作失败", "Operation failed")

    @staticmethod
    def _clean_format(value):
        value = str(value or "").strip()
        return value if value in FORMATS else DEFAULT_FORMAT

    @staticmethod
    def _render_path(fmt, abs_path, rel_path):
        if fmt == "relative" and rel_path:
            return rel_path
        if fmt == "name":
            return os.path.basename(abs_path.rstrip("/\\")) or abs_path
        return abs_path

    def _format_label(self, fmt):
        return {
            "absolute": self._tr("完整路径", "Full path"),
            "relative": self._tr("相对路径", "Relative path"),
            "name": self._tr("文件名", "File name"),
        }[fmt]

    def _sample_paths(self):
        notebook, rel = (
            ("Workspace", "Specs/api-design.md")
            if self._is_en()
            else ("团队文档", "架构/同步协议.md")
        )
        home = os.path.normpath(os.path.expanduser("~"))
        abs_path = os.path.join(home, "Documents", notebook, *rel.split("/"))
        return abs_path, rel

    # ------------------------------------------------------------------ settings

    def _build_page(self):
        default_file, default_rel = self._sample_paths()
        sample_file = self.last_file or default_file
        sample_rel = self.last_rel if self.last_file else default_rel
        preview = self._render_path(self.format, sample_file, sample_rel)
        items = [
            {
                "kind": "segmented",
                "id": "format",
                "label": self._tr("复制内容", "Copy as"),
                "value": self.format,
                "options": [{"value": f, "label": self._format_label(f)} for f in FORMATS],
            },
            {
                "kind": "kv",
                "rows": [{"label": self._tr("效果预览", "Preview"), "value": preview}],
            },
        ]
        if self.format == "relative":
            items.append({
                "kind": "kv",
                "rows": [{
                    "label": self._tr("笔记本外的文件", "Files outside notebooks"),
                    "value": self._tr("复制完整路径", "Copy the full path"),
                }],
            })
        return {
            "title": self._tr("复制路径", "Copy Path"),
            "cards": [{"id": "copy-format", "title": self._tr("格式", "Format"), "items": items}],
        }

    def _save_format(self, bridge, value):
        fmt = self._clean_format(value)
        if fmt != self.format:
            self.format = fmt
            bridge.call_host("host.storage.set", {"key": STORAGE_KEY, "value": fmt})

    # ------------------------------------------------------------------ command

    def _copy(self, bridge, context):
        abs_path = str(context.get("file") or "").strip()
        if not abs_path:
            return {
                "ok": False,
                "tone": "warning",
                "status": self._tr("没有可复制的文件路径", "No file path to copy"),
            }
        rel_path = str(context.get("note") or context.get("folder") or "").strip()
        self.last_file, self.last_rel = abs_path, rel_path
        text = self._render_path(self.format, abs_path, rel_path)
        bridge.call_host("host.clipboard.write", {"text": text})
        return {
            "ok": True,
            "tone": "success",
            "status": self._tr("已复制", "Copied ") + self._format_label(self.format).lower(),
        }

    # ------------------------------------------------------------------ dispatch

    def handle(self, bridge, method, params):
        if method == "plugin.initialize":
            self.locale = str(params.get("locale") or "zh")
            stored = bridge.call_host("host.storage.get", {"key": STORAGE_KEY})
            if isinstance(stored, dict) and stored.get("found"):
                self.format = self._clean_format(stored.get("value"))
            return {"ready": True}

        if method == "plugin.render":
            surface = str(params.get("surface") or SETTINGS_PAGE_SURFACE)
            if surface != SETTINGS_PAGE_SURFACE:
                raise RuntimeError(self._tr(f"不支持的界面: {surface}", f"Unsupported surface: {surface}"))
            values = params.get("values")
            accepted = {}
            if isinstance(values, dict) and "format" in values:
                self._save_format(bridge, values["format"])
                accepted["format"] = self.format
            elif not values:
                accepted["format"] = self.format
            return {"page": self._build_page(), "values": accepted}

        if method == "plugin.action":
            return {
                "ok": False,
                "tone": "warning",
                "status": self._tr("未知操作", "Unknown action"),
                "page": self._build_page(),
            }

        if method == "plugin.command":
            command_id = str(params.get("command_id") or "")
            if command_id not in COPY_COMMANDS:
                raise RuntimeError(self._tr(f"未知命令: {command_id}", f"Unknown command: {command_id}"))
            context = params.get("context") or {}
            return self._copy(bridge, context if isinstance(context, dict) else {})

        if method == "plugin.event":
            if params.get("event") == "locale.changed" and params.get("locale"):
                self.locale = str(params["locale"])
                return {"handled": True}
            return {"handled": False}

        if method == "plugin.shutdown":
            return {"ok": True}

        raise RuntimeError(self._tr(f"不支持的方法: {method}", f"Unsupported method: {method}"))


def main():
    plugin = CopyPathPlugin()
    HostBridge().serve(plugin.handle, plugin._localize_error)


if __name__ == "__main__":
    main()
