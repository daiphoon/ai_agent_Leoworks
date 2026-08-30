import json
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Set

from .errors import GatewayError


FIELD_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    version: str
    text: str


class PromptStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self._templates: Dict[str, Dict[str, Dict[str, str]]] = json.loads(
                path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("无法加载提示词存储: {}".format(path)) from exc
        self._validate_store()

    def _validate_store(self) -> None:
        if not isinstance(self._templates, dict):
            raise RuntimeError("提示词存储的顶层必须是对象")
        for name, versions in self._templates.items():
            if not isinstance(versions, dict) or not versions:
                raise RuntimeError("提示词 {} 必须至少包含一个版本".format(name))
            for version, record in versions.items():
                if not isinstance(record, dict) or not isinstance(record.get("template"), str):
                    raise RuntimeError("提示词 {}@{} 缺少 template".format(name, version))
                self._template_fields(record["template"])

    def _template_fields(self, template: str) -> Set[str]:
        fields: Set[str] = set()
        for _, field_name, format_spec, conversion in string.Formatter().parse(template):
            if field_name is None:
                continue
            if not FIELD_NAME_PATTERN.fullmatch(field_name):
                raise RuntimeError("提示词变量名不安全: {}".format(field_name))
            if format_spec or conversion:
                raise RuntimeError("提示词模板不支持 format_spec 或 conversion")
            fields.add(field_name)
        return fields

    def render(self, name: str, version: str, variables: Dict[str, Any]) -> RenderedPrompt:
        versions = self._templates.get(name)
        if versions is None or version not in versions:
            raise GatewayError(
                "prompt_not_found",
                "找不到提示词版本 {}@{}".format(name, version),
                404,
            )
        template = versions[version]["template"]
        expected = self._template_fields(template)
        provided = set(variables)
        missing = sorted(expected - provided)
        unexpected = sorted(provided - expected)
        if missing or unexpected:
            raise GatewayError(
                "invalid_prompt_variables",
                "提示词变量与模板不匹配",
                400,
                details={"missing": missing, "unexpected": unexpected},
            )
        text_variables = {key: str(value) for key, value in variables.items()}
        return RenderedPrompt(name=name, version=version, text=template.format_map(text_variables))

    def list_templates(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for name, versions in sorted(self._templates.items()):
            version_rows = []
            for version, record in sorted(versions.items()):
                version_rows.append(
                    {
                        "version": version,
                        "description": record.get("description", ""),
                        "variables": sorted(self._template_fields(record["template"])),
                    }
                )
            result.append({"name": name, "versions": version_rows})
        return result
