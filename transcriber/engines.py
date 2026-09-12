"""引擎能力描述。服务通过 /v1/engines 公布，客户端据此渲染可选项，不必内置各引擎的知识。

这里只有静态数据，不导入 torch 或任何模型库：构建服务时会读取它（见 tests 中"不导入 torch"的约束）。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EngineInfo:
    """一个引擎能做什么。

    languages 只有 "auto" 时表示该引擎不接受语言参数，由它自行判断；
    models 列出可用模型，提交任务时只接受其中的值。
    """
    name: str
    models: tuple[str, ...]
    default_model: str
    languages: tuple[str, ...]
    diarization: bool

    def as_dict(self):
        return {"name": self.name, "models": list(self.models), "default_model": self.default_model,
                "languages": list(self.languages), "diarization": self.diarization}
